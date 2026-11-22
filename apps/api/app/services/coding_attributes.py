"""One attribute predicate shared by evidence, scoped CSV and sample summaries."""

import json
import math
import uuid
from pathlib import PurePosixPath

import anyio
from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.material import Material
from app.schemas.coding_attributes import AttributeBinding, AttributeRow
from app.services import dataset_service, local_workspaces, preparation


def invalid(message):
    return StatForgeError(422, "CODING_ATTRIBUTES_INVALID", message)


async def table_columns(db, project, user, dataset_id):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    if source.project_id != project:
        raise invalid("请选择当前项目的属性表。")
    frame = await anyio.to_thread.run_sync(preparation.load, source)
    return {
        "columns": [
            {"name": name, "polars_type": str(dtype)} for name, dtype in frame.schema.items()
        ]
    }


async def catalog(db, project, user):
    from app.services import multimodal_cases, text_coding

    state = await text_coding.read(db, project, user)
    datasets = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == project, Dataset.upload_status != UploadStatus.DELETED
            )
        )
    ).all()
    materials = (
        await db.scalars(
            select(Material).where(Material.project_id == project, Material.user_id == user.id)
        )
    ).all()
    by_dataset = {str(d.id): d for d in datasets}
    cases = {c["key"]: c for c in state.get("case_scopes", [])}
    root = local_workspaces.project_root(project)
    from app.services.survey_import import latest

    current_surveys = {str(r.id) for r in latest(datasets).values()}
    for d in datasets:
        meta = json.loads(d.preview_json or "{}")
        if meta.get("survey") and str(d.id) not in current_surveys or meta.get("survey_answer"):
            continue
        group = str(meta.get("media_group_id") or d.id)
        descriptor = meta.get("media_case")
        if descriptor and root:
            for sample in multimodal_cases.sample_rows(root, descriptor):
                sample_id = str(sample["sample_id"])
                key = f"{group}:{sample_id}"
                cases.setdefault(
                    key,
                    {
                        "key": key,
                        "group_id": group,
                        "collection_id": str(d.id),
                        "sample_id": sample_id,
                        "title": sample.get("title") or sample_id,
                        "group_title": meta.get("display_name") or d.original_filename,
                    },
                )
        elif meta.get("raw_file") and d.file_format in {
            "wav",
            "mp4",
            "txt",
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "webp",
        }:
            key = f"{group}:{d.id}"
            cases.setdefault(
                key,
                {
                    "key": key,
                    "group_id": group,
                    "collection_id": str(d.id),
                    "sample_id": str(d.id),
                    "title": d.original_filename,
                    "group_title": d.original_filename,
                },
            )
    files = []
    covered = set()
    for m in materials:
        source_id = json.loads(m.metadata_json or "{}").get("source_dataset_id")
        d = by_dataset.get(source_id)
        if not d:
            d = next((d for d in datasets if d.material_id == str(m.id)), None)
        if d:
            covered.add(str(d.id))
        files.append(
            {
                "key": str(m.id),
                "title": m.filename,
                "sha256": m.sha256,
                "version": m.version,
                "dataset_id": str(d.id) if d else None,
                "relative_path": d.source_path if d and d.local_file else None,
            }
        )
    for d in datasets:
        if str(d.id) not in covered and d.raw_file and not d.media_case:
            files.append(
                {
                    "key": str(d.id),
                    "title": d.original_filename,
                    "sha256": d.sha256,
                    "version": None,
                    "dataset_id": str(d.id),
                    "relative_path": d.source_path if d.local_file else None,
                }
            )
    tables = [
        {"id": str(d.id), "title": d.display_name or d.original_filename}
        for d in datasets
        if d.file_format in {"csv", "tsv", "parquet", "xlsx", "xls"}
    ]
    return {"cases": list(cases.values()), "files": files, "tables": tables}


async def preview_binding(db, project, user, request):
    source = await dataset_service.get_dataset_with_access(db, request.dataset_id, user.id)
    if source.project_id != project:
        raise invalid("请选择当前项目的属性表。")
    frame = await anyio.to_thread.run_sync(preparation.load, source)
    selected = [c.name for c in request.columns]
    if request.key_column not in frame.columns or not set(selected) <= set(frame.columns):
        raise invalid("表格字段已变化，请重新选择。")
    if len(set(selected)) != len(selected):
        raise invalid("同一个属性只需选择一次。")
    if (
        request.target == "case"
        and request.key_kind != "sample_id"
        or request.target == "file"
        and request.key_kind == "sample_id"
    ):
        raise invalid("请选择对应的案例或文件关联字段。")
    objects = await catalog(db, project, user)
    candidates = objects["cases"] if request.target == "case" else objects["files"]
    if request.target == "case" and request.group_id:
        candidates = [c for c in candidates if c["group_id"] == request.group_id]
    rows, unmatched, used = [], [], set()
    for index, row in enumerate(frame.to_dicts()):
        raw_key = row[request.key_column]
        key = str(raw_key) if raw_key is not None else ""
        choice = (request.file_choices if request.target == "file" else request.case_choices).get(
            str(index)
        )
        if choice:
            matches = [c for c in candidates if c["key"] == choice]
        elif request.key_kind == "sample_id":
            matches = [c for c in candidates if c["sample_id"] == key]
        elif request.key_kind == "file_id":
            matches = [c for c in candidates if key in {c["key"], c.get("dataset_id")}]
        else:
            path = PurePosixPath(key.replace("\\", "/")).as_posix()
            matches = [c for c in candidates if c.get("relative_path") == path] if key else []
        if len(matches) != 1:
            unmatched.append(
                {
                    "row": index,
                    "value": key,
                    "reason": "有多个同标识对象，请指定材料组或文件"
                    if matches
                    else "未找到对应对象",
                }
            )
            continue
        target = matches[0]
        if target["key"] in used:
            raise invalid(f"属性表有多行对应同一对象：{target['title']}。请先整理重复行。")
        used.add(target["key"])
        values = {}
        for col in request.columns:
            value = row[col.name]
            if value is None or value == "":
                values[col.name] = None
            elif col.kind == "number":
                try:
                    number = float(value)
                except (ValueError, TypeError):
                    raise invalid(f"第 {index + 1} 行的 {col.name} 不是数值。") from None
                if not math.isfinite(number):
                    raise invalid(f"第 {index + 1} 行的 {col.name} 不是有效数值。")
                values[col.name] = number
            else:
                values[col.name] = str(value)
        rows.append(
            AttributeRow(
                key=target["key"],
                title=target["title"],
                values=values,
                sha256=target.get("sha256"),
                version=target.get("version"),
            )
        )
    binding = AttributeBinding(
        id=request.id or uuid.uuid4(),
        source_id=source.id,
        source_sha256=source.sha256,
        source_name=source.display_name or source.original_filename,
        request=request,
        rows=rows,
    )
    return {
        "binding": binding.model_dump(mode="json"),
        "total_rows": frame.height,
        "matched_count": len(rows),
        "unmatched": unmatched,
    }


def validated_conditions(document):
    bindings = {b.id: b for b in document.attribute_bindings}
    result = []
    for condition in document.attribute_filter.conditions:
        binding = bindings.get(condition.binding_id)
        column = (
            next((c for c in binding.request.columns if c.name == condition.column), None)
            if binding
            else None
        )
        if not column:
            raise invalid("筛选属性已变化，请重新选择条件。")
        value = condition.value
        if column.kind == "number":
            if condition.operator not in {"eq", "gte", "lte"}:
                raise invalid("数值属性请选择等于、大于等于或小于等于。")
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise invalid("请填写筛选数值。") from None
            if not math.isfinite(value):
                raise invalid("请填写有效的筛选数值。")
        elif condition.operator not in {"eq", "contains", "starts_with"}:
            raise invalid("文本属性请选择等于、包含或开头。")
        result.append((binding, column, condition, value, {r.key: r for r in binding.rows}))
    return result


def matches_source(document, conditions, case_key, file_id, sha256, version):
    verdicts = []
    for binding, column, condition, expected, rows in conditions:
        key = case_key if binding.request.target == "case" else str(file_id or "")
        row = rows.get(key)
        if (
            row
            and binding.request.target == "file"
            and (row.sha256 != sha256 or (row.version is not None and row.version != version))
        ):
            row = None
        value = row.values.get(column.name) if row else None
        matched = False
        if value is not None:
            if column.kind == "text":
                value, expected = str(value), str(expected)
            if condition.operator == "eq":
                matched = value == expected
            elif condition.operator == "gte":
                matched = value >= expected
            elif condition.operator == "lte":
                matched = value <= expected
            elif condition.operator == "contains":
                matched = expected in value
            elif condition.operator == "starts_with":
                matched = value.startswith(expected)
        verdicts.append(matched)
    return not verdicts or (
        all(verdicts) if document.attribute_filter.mode == "all" else any(verdicts)
    )


def scoped(view, document, case=None):
    conditions = validated_conditions(document)
    accepted = []
    for segment in document.segments:
        case_key = view.get("segment_cases", {}).get(str(segment.id))
        if case and case_key != case["key"]:
            continue
        if matches_source(
            document,
            conditions,
            case_key,
            segment.material_id or segment.dataset_id,
            segment.material_sha256 or segment.dataset_sha256,
            segment.material_version,
        ):
            accepted.append(segment)
    current = [
        s
        for s in accepted
        if view.get("transcription_status", {}).get(str(s.id), "current") == "current"
    ]
    operators = {"eq": "=", "gte": "≥", "lte": "≤", "contains": "包含", "starts_with": "开头是"}
    joiner = " 且 " if document.attribute_filter.mode == "all" else " 或 "
    description = joiner.join(
        f"{'案例' if b.request.target == 'case' else '文件'} · {c.name} {operators[q.operator]} {v}"
        for b, c, q, v, _ in conditions
    )
    label = f"案例 · {case['title']}" if case else "全项目"
    return {
        "segment_ids": [str(s.id) for s in accepted],
        "current_segment_ids": [str(s.id) for s in current],
        "case_count": len({view.get("segment_cases", {}).get(str(s.id)) for s in current} - {None}),
        "file_count": len({str(s.material_id or s.dataset_id) for s in current}),
        "annotation_count": len(current),
        "scope_label": label + (f" · {description}" if description else ""),
        "description": description,
        "filter": document.attribute_filter.model_dump(mode="json"),
        "sources": [
            {
                "binding_id": str(b.id),
                "dataset_id": str(b.source_id),
                "sha256": b.source_sha256,
                "filename": b.source_name,
            }
            for b in document.attribute_bindings
            if any(q.binding_id == b.id for q in document.attribute_filter.conditions)
        ],
    }
