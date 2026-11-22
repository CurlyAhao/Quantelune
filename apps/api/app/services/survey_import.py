"""Map questionnaire columns into existing cases, materials and coding attributes."""

import copy
import hashlib
import json
import math
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import anyio
import polars as pl
from pydantic import Field
from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.preview.workspace import save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.coding import CodingDocument
from app.schemas.coding_attributes import (
    AttributeBinding,
    AttributeBindingRequest,
    AttributeColumn,
    AttributeRow,
)
from app.schemas.material import Strict
from app.schemas.workspace import SaveRecord
from app.services import (
    dataset_service,
    material_service,
    material_updates,
    preparation,
    project_sources,
    storage_service,
    text_coding,
)


class Column(Strict):
    name: str
    role: Literal["id", "attribute", "answer", "ignore"]
    kind: Literal["number", "text"] = "text"


class Mapping(Strict):
    dataset_id: uuid.UUID
    collection_id: uuid.UUID | None = None
    title: str = Field(default="问卷案例", min_length=1, max_length=120)
    columns: list[Column]
    empty: Literal["keep", "clear"] = "keep"
    preview_sha256: str | None = None


def invalid(message, status=422):
    return StatForgeError(status, "SURVEY_INPUT", message)


def meta(row):
    return json.loads(row.preview_json or "{}")


def latest(rows):
    groups = {}
    for row in sorted(rows, key=lambda r: (r.created_at, str(r.id)), reverse=True):
        info = meta(row)
        if info.get("survey") and row.media_case:
            groups.setdefault(info["media_group_id"], row)
    return groups


async def collections(db, project):
    rows = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == project, Dataset.upload_status != UploadStatus.DELETED
            )
        )
    ).all()
    return latest(rows)


async def source_table(db, project, user, identity):
    source = await dataset_service.get_dataset_with_access(db, identity, user.id)
    if source.project_id != project or source.file_format not in {"csv", "xlsx", "parquet"}:
        raise invalid("请选择当前项目的 CSV 或 XLSX 表格。")
    frame = await anyio.to_thread.run_sync(preparation.load, source)
    return source, frame


def missing(value):
    return value is None or isinstance(value, str) and not value.strip()


def identifier(value):
    if missing(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


async def options(db, project, user, identity):
    source, frame = await source_table(db, project, user, identity)
    groups = await collections(db, project)
    columns = []
    for name, dtype in frame.schema.items():
        values = [preparation.cell(v) for v in frame[name].head(10).to_list() if not missing(v)]
        role = (
            "id"
            if name.casefold() in {"case", "id", "case_id", "案例id", "受访者id"}
            else "answer"
            if any(len(str(v)) > 80 for v in values)
            else "attribute"
        )
        columns.append(
            {
                "name": name,
                "role": role,
                "kind": "number" if dtype.is_numeric() else "text",
                "example": values[0] if values else None,
            }
        )
    if not any(c["role"] == "id" for c in columns) and columns:
        columns[0]["role"] = "id"
    return {
        "columns": columns,
        "rows": frame.height,
        "collections": [
            {"id": str(r.id), "title": r.display_name, "count": len(r.media_case["samples"])}
            for r in groups.values()
        ],
    }


async def preview(db, project, user, body):
    source, frame = await source_table(db, project, user, body.dataset_id)
    if len({c.name for c in body.columns}) != len(body.columns) or set(
        c.name for c in body.columns
    ) != set(frame.columns):
        raise invalid("表格字段已变化，请重新打开问卷映射。")
    ids = [c.name for c in body.columns if c.role == "id"]
    if len(ids) != 1:
        raise invalid("请选择且只选择一个案例 ID 字段。")
    if not any(c.role in {"attribute", "answer"} for c in body.columns):
        raise invalid("请选择需要导入的属性或开放回答。")
    groups = await collections(db, project)
    old = None
    if body.collection_id:
        chosen = await dataset_service.get_dataset_with_access(db, body.collection_id, user.id)
        if chosen.project_id != project or not meta(chosen).get("survey"):
            raise invalid("问卷案例组已变化，请重新选择。")
        old = groups.get(meta(chosen)["media_group_id"])
    samples = {s["sample_id"]: s for s in old.media_case["samples"]} if old else {}
    raw_rows = frame.to_dicts()
    counts = Counter(identifier(row[ids[0]]) for row in raw_rows)
    rows, errors = [], []
    for index, raw in enumerate(raw_rows, 2):
        identity = identifier(raw[ids[0]])
        issue = "缺少案例 ID" if not identity else "案例 ID 重复" if counts[identity] > 1 else None
        if issue:
            errors.append({"row": index, "id": identity, "message": issue})
            continue
        previous = samples.get(identity)
        attributes = dict(previous.get("attributes", {})) if previous else {}
        answers = copy.deepcopy(previous.get("answers", {})) if previous else {}
        answer_values = {}
        for col in body.columns:
            value = preparation.cell(raw[col.name])
            if col.role not in {"attribute", "answer"}:
                continue
            if missing(value):
                if body.empty == "keep" and col.name in (
                    attributes if col.role == "attribute" else answers
                ):
                    continue
                value = None
            if col.role == "attribute":
                if value is not None and col.kind == "number":
                    try:
                        value = float(value)
                        if not math.isfinite(value):
                            raise ValueError()
                    except (ValueError, TypeError):
                        errors.append(
                            {"row": index, "id": identity, "message": f"{col.name} 不是有效数值"}
                        )
                        continue
                elif value is not None:
                    value = str(value)
                attributes[col.name] = value
            else:
                value = None if value is None else str(value)
                answer_values[col.name] = value
        changed_answers = {
            name: value
            for name, value in answer_values.items()
            if name not in answers or answers[name].get("answer") != value
        }
        changed = (
            not previous or attributes != previous.get("attributes", {}) or bool(changed_answers)
        )
        rows.append(
            {
                "row": index,
                "id": identity,
                "status": "new" if not previous else "updated" if changed else "unchanged",
                "attributes": attributes,
                "answers": answer_values,
                "changed_answers": changed_answers,
                "answer_count": len(set(answers) | set(answer_values)),
            }
        )
    record = await text_coding.current(db, project, user)
    revision = record.revision if record else 0
    fingerprint = {
        "mapping": body.model_dump(mode="json", exclude={"preview_sha256"}),
        "source_sha256": source.sha256,
        "collection": old.media_case if old else None,
        "coding_revision": revision,
        "rows": rows,
        "errors": errors,
    }
    digest = hashlib.sha256(material_service.canonical(fingerprint).encode()).hexdigest()
    return {
        "rows": rows,
        "errors": errors,
        "new": sum(r["status"] == "new" for r in rows),
        "updated": sum(r["status"] == "updated" for r in rows),
        "unchanged": sum(r["status"] == "unchanged" for r in rows),
        "preview_sha256": digest,
        "collection_id": str(old.id) if old else None,
        "revision": revision,
    }


async def stored_table(db, project, user, source):
    candidates = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == project,
                Dataset.sha256 == source.sha256,
                Dataset.upload_status != UploadStatus.DELETED,
            )
        )
    ).all()
    previous = next(
        (
            r
            for r in candidates
            if meta(r).get("survey_source", {}).get("original_id") == str(source.id)
        ),
        None,
    )
    if previous:
        return previous
    path = storage_service.get_local_object_path(source.object_key)
    saved = await dataset_service.upload_dataset(
        db,
        project,
        user.id,
        str(path),
        source.original_filename,
        allow_duplicate=True,
        **({"sheet_name": source.sheet_name} if source.sheet_name else {}),
    )
    info = meta(saved)
    info.update(
        source_dataset_id=str(source.id),
        survey_source={"original_id": str(source.id), "sha256": source.sha256},
        display_name=source.original_filename + " · 导入时原表",
    )
    saved.preview_json = json.dumps(info, ensure_ascii=False)
    return saved


async def answer_material(
    db, project, user, group, case, question, value, previous, source, row, snapshot
):
    text = f"[{question}]:\n" + (value or "")
    data = text.encode("utf-8")
    name = dataset_service.sanitize_filename(f"{case} · {question}.txt")
    if previous:
        old = await material_service.load(db, uuid.UUID(previous["material_id"]), user.id, project)
        body = SimpleNamespace(
            source_sha256=old.sha256,
            mode="version",
            comparison_sha256=material_updates.token(old, hashlib.sha256(data).hexdigest()),
        )
        result = await material_updates.apply(db, old.id, user.id, name, data, body, snapshot)
        public = result["material"]
    else:
        public = await material_service.upload(db, project, user.id, name, data, snapshot)
    material = await material_service.load(db, uuid.UUID(public["id"]), user.id, project)
    # A retried version is reused together with its original Dataset relationship.
    existing = json.loads(material.metadata_json).get("source_dataset_id")
    if existing:
        return {
            **previous,
            "dataset_id": existing,
            "material_id": str(material.id),
            "sha256": material.sha256,
            "version": material.version,
            "answer": value,
        }
    path = storage_service.get_local_object_path(material.object_key)
    saved = await project_sources.store(db, project, user.id, str(path), name, "问卷回答/" + name)
    provenance = {
        "group_id": group,
        "case_id": case,
        "question": question,
        "source_id": str(source.id),
        "source_sha256": source.sha256,
        "source_name": source.original_filename,
        "row": row,
        "sheet_name": source.sheet_name,
        "version": material.version,
    }
    info = meta(saved)
    info.update(
        material_id=str(material.id),
        source_dataset_id=str(source.id),
        survey_answer=provenance,
        display_name=f"{case} · {question} · v{material.version}",
    )
    if previous:
        prior = await db.get(Dataset, uuid.UUID(previous["dataset_id"]))
        info["file_version"] = {
            "root_id": prior.source_root_id,
            "parent_id": str(prior.id),
            "number": material.version,
        }
    saved.preview_json = json.dumps(info, ensure_ascii=False)
    details = json.loads(material.metadata_json)
    details.update(source_dataset_id=str(saved.id), survey_answer=provenance)
    material.metadata_json = json.dumps(details, ensure_ascii=False)
    return {
        **provenance,
        "dataset_id": str(saved.id),
        "material_id": str(material.id),
        "sha256": material.sha256,
        "answer": value,
    }


async def apply(db, project, user, body):
    await acquire_write_fence(db, user.id)
    checked = await preview(db, project, user, body)
    if checked["errors"]:
        raise invalid("请先处理预览中缺少或重复的 ID，以及字段错误。")
    if not body.preview_sha256 or checked["preview_sha256"] != body.preview_sha256:
        raise invalid("表格或案例已变化，请重新查看导入预览。", 409)
    old = (
        await db.get(Dataset, uuid.UUID(checked["collection_id"]))
        if checked["collection_id"]
        else None
    )
    if old and not checked["new"] and not checked["updated"]:
        return {**checked, "id": str(old.id), "reused": True}
    source = await dataset_service.get_dataset_with_access(db, body.dataset_id, user.id)
    snapshot = await material_service.key_snapshot(db, user.id)
    saved_source = await stored_table(db, project, user, source)
    group = meta(old)["media_group_id"] if old else str(uuid.uuid4())
    samples = {s["sample_id"]: copy.deepcopy(s) for s in old.media_case["samples"]} if old else {}
    for row in checked["rows"]:
        sample = samples.setdefault(
            row["id"],
            {
                "sample_id": row["id"],
                "title": row["id"],
                "case_title": row["id"],
                "display_title": row["id"],
                "members": [],
                "files": {},
                "answers": {},
            },
        )
        sample["attributes"] = row["attributes"]
        for question, value in row["changed_answers"].items():
            sample["answers"][question] = await answer_material(
                db,
                project,
                user,
                group,
                row["id"],
                question,
                value,
                sample["answers"].get(question),
                saved_source,
                row["row"],
                snapshot,
            )
        answers = list(sample["answers"].values())
        sample["members"] = [{"id": a["dataset_id"], "sha256": a["sha256"]} for a in answers]
        if answers:
            sample["primary_id"] = answers[0]["dataset_id"]
    record = await text_coding.current(db, project, user)
    document = (
        CodingDocument.model_validate_json(record.payload_json) if record else CodingDocument()
    )
    binding_id = uuid.UUID(meta(old)["survey"]["binding_id"]) if old else uuid.uuid4()
    prior_binding = next((b for b in document.attribute_bindings if b.id == binding_id), None)
    kinds = {c.name: c.kind for c in prior_binding.request.columns} if prior_binding else {}
    kinds.update({c.name: c.kind for c in body.columns if c.role == "attribute"})
    title = old.media_case["title"] if old else body.title.strip()
    descriptor = {
        "schema": "statforge-media-case-v1",
        "kind": "media",
        "identity": "survey:" + group,
        "survey": True,
        "title": title,
        "samples": list(samples.values()),
    }
    with tempfile.TemporaryDirectory(prefix="sf-survey-") as directory:
        path = Path(directory) / "survey.sfmedia"
        path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
        collection = await project_sources.store(
            db, project, user.id, str(path), path.name, "联合查看/survey.sfmedia"
        )
        if kinds:
            values = [
                {"案例 ID": s["sample_id"], **{k: s["attributes"].get(k) for k in kinds}}
                for s in samples.values()
            ]
            schema = {
                "案例 ID": pl.String,
                **{k: pl.Float64 if kind == "number" else pl.String for k, kind in kinds.items()},
            }
            table = pl.DataFrame(values, schema=schema, strict=True)
            path = Path(directory) / "问卷案例属性.parquet"
            table.write_parquet(path)
            attributes = await dataset_service.upload_dataset(
                db, project, user.id, str(path), path.name, allow_duplicate=True
            )
            attributes.preview_json = json.dumps(
                {
                    **meta(attributes),
                    "source_dataset_id": str(saved_source.id),
                    "display_name": title + " · 案例属性",
                    "survey_attributes": {"group_id": group},
                },
                ensure_ascii=False,
            )
            request = AttributeBindingRequest(
                id=binding_id,
                dataset_id=attributes.id,
                target="case",
                key_column="案例 ID",
                key_kind="sample_id",
                group_id=group,
                columns=[AttributeColumn(name=k, kind=v) for k, v in kinds.items()],
            )
            binding = AttributeBinding(
                id=binding_id,
                source_id=attributes.id,
                source_sha256=attributes.sha256,
                source_name=title,
                request=request,
                rows=[
                    AttributeRow(
                        key=group + ":" + s["sample_id"],
                        title=s["sample_id"],
                        values={k: s["attributes"].get(k) for k in kinds},
                    )
                    for s in samples.values()
                ],
            )
            document.attribute_bindings = [
                b for b in document.attribute_bindings if b.id != binding_id
            ] + [binding]
    survey = {
        "binding_id": str(binding_id),
        "source_id": str(saved_source.id),
        "mapping": body.model_dump(mode="json"),
        "previous_collection_id": str(old.id) if old else None,
        "attribute_dataset_id": str(attributes.id) if kinds else None,
    }
    collection.preview_json = json.dumps(
        {
            **meta(collection),
            "media_case": descriptor,
            "media_group_id": group,
            "display_name": title,
            "survey": survey,
        },
        ensure_ascii=False,
    )
    collection.upload_status = UploadStatus.READY
    await save_record(
        db,
        user,
        SaveRecord(project_id=project, expected_revision=checked["revision"], payload=document),
        record.id if record else None,
        coding_write=True,
        commit=False,
    )
    await db.commit()
    return {**checked, "id": str(collection.id), "reused": False}


async def source_row(db, project, user, identity, row, sha256):
    source, frame = await source_table(db, project, user, identity)
    if source.sha256 != sha256 or not 2 <= row <= frame.height + 1:
        raise invalid("原表版本或行号无法对应，请从回答重新打开来源。", 409)
    return {
        "dataset_id": str(source.id),
        "original_filename": source.original_filename,
        "file_format": source.file_format,
        "row_count": frame.height,
        "column_count": frame.width,
        "upload_status": "ready",
        "columns": [
            {"name": name, "inferred_type": str(dtype)} for name, dtype in frame.schema.items()
        ],
        "rows": [{k: preparation.cell(v) for k, v in frame.row(row - 2, named=True).items()}],
        "showing_rows": 1,
        "total_rows": frame.height,
    }
