"""Small explicit dataset adapters; manual and Agent requests share this service."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import JSON, String, Text, Uuid, cast, inspect, or_, select

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.user import User
from app.models.workspace import WorkspaceRecord, WorkspaceRevision
from app.preview.context import canonical
from app.preview.workspace import load_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.media_runs import MediaAssociation, MediaRequest, MediaRun
from app.services import dataset_service, project_sources, storage_service
from app.services import local_workspaces as local
from app.services import project_assistant_tasks as tasks

SCHEMA = "statforge-media-case-v1"
INSTANCE = uuid.uuid4().hex
JOBS: dict[uuid.UUID, asyncio.Task] = {}


def invalid(message, status=422):
    return StatForgeError(status, "MEDIA_CASE_INPUT", message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def checked_file(root, relative):
    resolved_root = storage_service.filesystem_path(root.resolve())
    path = storage_service.filesystem_path((resolved_root / relative).resolve())
    if not path.is_relative_to(resolved_root) or not path.is_file():
        raise invalid(f"找不到文件：{relative}。请恢复原文件后刷新工作区。")
    local.checked_path(path)
    return path


async def discover(db, project, owner, root, rows):
    descriptors = []
    if (root / "metadata/samples.csv").is_file():
        with (root / "metadata/samples.csv").open(encoding="utf-8-sig", newline="") as stream:
            samples = list(csv.DictReader(stream))
        if samples and all(
            {"sample_id", "video_av", "audio", "transcript"} <= set(s) for s in samples
        ):
            descriptors.append(
                {
                    "schema": SCHEMA,
                    "kind": "ravdess",
                    "title": "RAVDESS · 语音、视频与台词",
                    "identity": "ravdess",
                    "table": "metadata/samples.csv",
                }
            )
    for instances in sorted((root / "annotations").glob("instances_*.json")):
        suffix = instances.name.removeprefix("instances_")
        captions = instances.with_name("captions_" + suffix)
        if captions.is_file():
            descriptors.append(
                {
                    "schema": SCHEMA,
                    "kind": "coco",
                    "title": "COCO · 图像与描述",
                    "identity": instances.relative_to(root).as_posix(),
                    "instances_json": instances.relative_to(root).as_posix(),
                    "captions_json": captions.relative_to(root).as_posix(),
                }
            )
    for descriptor in descriptors:
        if any(
            read_metadata(row).get("media_case", {}).get("identity") == descriptor["identity"]
            for row in rows
        ):
            continue
        with tempfile.TemporaryDirectory(prefix="sf-media-case-") as directory:
            path = Path(directory) / (descriptor["kind"] + ".sfmedia")
            path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
            row = await project_sources.store(
                db, project.id, owner, str(path), path.name, "联合查看/" + path.name
            )
            row.preview_json = json.dumps(
                {
                    "raw_file": True,
                    "media_case": descriptor,
                    "display_name": descriptor["title"],
                    "source_path": row.source_path,
                }
            )
            row.upload_status = UploadStatus.READY
            rows.append(row)


def read_metadata(row):
    return json.loads(row.preview_json or "{}")


async def collection(db, user, dataset_id):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    meta = read_metadata(source)
    if not meta.get("media_case"):
        raise invalid("请选择已识别的图像或音视频样本。")
    root = local.project_root(source.project_id)
    if root is None:
        raise invalid("请打开样本所在的本地工作文件夹。")
    return source, root, meta["media_case"]


def sample_rows(root, descriptor):
    if descriptor["kind"] == "media":
        rows = [dict(row) for row in descriptor["samples"]]
        for row in rows:
            if row.get("text"):
                row["transcript"] = checked_file(root, row["text"]).read_text(encoding="utf-8-sig")
        return rows
    if descriptor["kind"] == "ravdess":
        with checked_file(root, descriptor["table"]).open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            row["title"] = f"演员\
 {row[('actor_id')]} · {row.get(('emotion_zh'), row[('emotion')])} · 台词 {row[('statement_id')]}"
        return rows
    instances = read_json(checked_file(root, descriptor["instances_json"]))
    captions = read_json(checked_file(root, descriptor["captions_json"]))
    cats = {item["id"]: item["name"] for item in instances["categories"]}
    rows = []
    for image in instances["images"]:
        identity = image["id"]
        texts = [c for c in captions["annotations"] if c["image_id"] == identity]
        objects = [
            dict(
                o,
                category=cats.get(o["category_id"], str(o["category_id"])),
                width=image["width"],
                height=image["height"],
            )
            for o in instances["annotations"]
            if o["image_id"] == identity
        ]
        rows.append(
            {
                "sample_id": str(identity),
                "image_id": identity,
                "title": image["file_name"],
                "image_path": "images/" + image["file_name"],
                "image": image,
                "captions": texts,
                "objects": objects,
            }
        )
    return rows


async def associate(db, user, anchor_id, body: MediaAssociation):
    """Save explicit immutable file associations; no inferred pairing or media copies."""
    anchor = await dataset_service.get_dataset_with_access(db, anchor_id, user.id)
    root = local.project_root(anchor.project_id)
    if root is None:
        raise invalid("请先打开材料所在的本地文件夹。")
    if body.task_id:
        await tasks.context(db, anchor.project_id, user, str(body.task_id))
    from app.services.media_filename_metadata import filename, filename_metadata, short_title

    metadata = await filename_metadata(db, anchor.project_id, root)
    project_rows = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == anchor.project_id,
                Dataset.upload_status != UploadStatus.DELETED,
            )
        )
    ).all()
    name_counts = {}
    for source in project_rows:
        if source.file_format in {"mp4", "wav"} and read_metadata(source).get("raw_file"):
            key = filename(source.original_filename)
            name_counts[key] = name_counts.get(key, 0) + 1
    owner_case = None
    inherited_owner = (anchor.media_case or {}).get("owner_case")
    if inherited_owner and body.owner_case_collection_id:
        if str(body.owner_case_collection_id) != inherited_owner.get(
            "collection_id"
        ) or body.owner_case_sample_id != inherited_owner.get("sample_id"):
            raise invalid("不能更改已有处理的案例归属。", 409)
    owner_collection = body.owner_case_collection_id or (
        uuid.UUID(inherited_owner["collection_id"]) if inherited_owner else None
    )
    owner_sample_id = body.owner_case_sample_id or (
        inherited_owner.get("sample_id") if inherited_owner else None
    )
    if owner_collection:
        owner_source, _, owner_descriptor = await collection(db, user, owner_collection)
        if owner_source.project_id != anchor.project_id:
            raise invalid("案例不属于当前项目。")
        case = next(
            (
                row
                for row in owner_descriptor.get("samples", [])
                if str(row.get("sample_id")) == owner_sample_id
                and row.get("case_title")
                and not row.get("case_deleted")
            ),
            None,
        )
        if not case:
            raise invalid("案例已变化，请刷新后重试。", 409)
        allowed = {
            str(case.get(key))
            for key in ("primary_id", "video_id", "audio_id", "text_id", "pitch_id")
            if case.get(key)
        }
        allowed.update(
            str(member.get("id")) for member in case.get("members", []) if member.get("id")
        )
        case_hashes = {
            str(member.get("id")): member.get("sha256")
            for member in case.get("members", [])
            if member.get("id")
        }
        case_hashes.update(
            {
                str(value.get("id")): value.get("sha256")
                for value in case.get("files", {}).values()
                if value.get("id")
            }
        )
        for item in body.samples:
            selected = {
                str(value)
                for value in (
                    item.primary_id,
                    item.video_id,
                    item.audio_id,
                    item.text_id,
                    item.pitch_id,
                )
                if value
            }
            selected.update(str(value) for value in (item.member_ids or []))
            if not selected <= allowed:
                raise invalid("处理材料不属于所选案例，请重新选择。")
            for identity in selected:
                member = await dataset_service.get_dataset_with_access(
                    db, uuid.UUID(identity), user.id
                )
                if not case_hashes.get(identity) or case_hashes[identity] != member.sha256:
                    raise invalid("案例材料版本不可核验，请更新案例材料后重试。", 409)
        owner_case = {"collection_id": str(owner_source.id), "sample_id": owner_sample_id}
    samples = []
    seen = set()
    previous_samples = {
        str(row.get("sample_id")): row for row in (anchor.media_case or {}).get("samples", [])
    }
    for item in body.samples:
        primary_id = item.video_id or item.audio_id or item.primary_id
        if primary_id in seen:
            raise invalid("同一个视频或音频请只选择一次。")
        seen.add(primary_id)
        previous = previous_samples.get(str(item.sample_id or primary_id), {})
        if item.sample_id and not previous:
            raise invalid("案例已变化，请刷新后重试。", 409)
        if not previous:
            previous = next(
                (
                    s
                    for s in previous_samples.values()
                    if s.get("refi_case_guid")
                    and str(primary_id)
                    in {str(s.get("primary_id")), str(s.get("video_id")), str(s.get("audio_id"))}
                ),
                {},
            )
        sample = {
            "sample_id": previous.get(
                "sample_id", str(uuid.uuid4()) if body.purpose == "case" else str(primary_id)
            ),
            "files": {},
        }
        if previous.get("refi_case_guid"):
            sample["refi_case_guid"] = previous["refi_case_guid"]
        title = item.case_title if item.case_title is not None else previous.get("case_title", "")
        if title and title.strip():
            sample["case_title"] = title.strip()
        if item.member_ids is None:
            if previous.get("members"):
                sample["members"] = previous["members"]
        else:
            members = []
            for identity in dict.fromkeys(item.member_ids):
                row = await dataset_service.get_dataset_with_access(db, identity, user.id)
                if row.project_id != anchor.project_id or row.upload_status == UploadStatus.DELETED:
                    raise invalid("请选择当前项目中可用的材料。")
                old = next(
                    (
                        member
                        for member in previous.get("members", [])
                        if member.get("id") == str(identity)
                    ),
                    None,
                )
                if old and old.get("sha256") != row.sha256:
                    raise invalid("案例中的材料版本已变化，请先移除旧关联，再选择新版本。")
                if row.file_format.lower() not in {
                    "pdf",
                    "txt",
                    "md",
                    "markdown",
                    "png",
                    "jpg",
                    "jpeg",
                    "gif",
                    "webp",
                    "wav",
                    "mp4",
                    "pitch",
                } and not (old and previous.get("refi_case_guid")):
                    raise invalid("案例材料须为可预览的文本、PDF、图片、音频或视频。")
                member = dict(old or {}, id=str(row.id), sha256=row.sha256)
                if identity == item.range_member_id:
                    from app.services.project_assistant import material_selection

                    selected_range = material_selection(row, str(body.task_id or "project"))
                    start, end = selected_range.get("start"), selected_range.get("end")
                    if (
                        row.file_format.lower() not in {"mp4", "wav"}
                        or start is None
                        or end is None
                        or end <= start
                    ):
                        raise invalid("请先在原材料中选择起止时间。")
                    member["range"] = {"start": start, "end": end}
                members.append(member)
            if members:
                sample["members"] = members
        if item.primary_id:
            primary = await dataset_service.get_dataset_with_access(db, item.primary_id, user.id)
            if (
                primary.project_id != anchor.project_id
                or primary.upload_status == UploadStatus.DELETED
                or primary.file_format.lower()
                not in {
                    "pdf",
                    "txt",
                    "md",
                    "markdown",
                    "png",
                    "jpg",
                    "jpeg",
                    "gif",
                    "webp",
                    "wav",
                    "mp4",
                }
            ):
                raise invalid("请选择当前项目中可预览的材料。")
            sample["primary_id"] = str(primary.id)
            sample["title"] = primary.original_filename
            sample["display_title"] = primary.original_filename
            sample.setdefault("members", [])
            if not any(member["id"] == str(primary.id) for member in sample["members"]):
                sample["members"].insert(0, {"id": str(primary.id), "sha256": primary.sha256})
        if item.video_id and item.audio_id:
            sample["audio_alignment"] = {
                "offset_seconds": item.audio_offset_seconds,
                "content_reviewed": item.content_reviewed,
                "offset_convention": "audio_seconds = video_seconds + offset_seconds",
            }
        if item.pitch_id:
            sample["pitch_alignment"] = {
                "offset_seconds": item.pitch_offset_seconds,
                "convention": "audio_seconds = pitch_seconds + offset_seconds",
            }
        elif previous.get("pitch_id"):
            for key in ("pitch_id", "pitch", "pitch_alignment"):
                sample[key] = previous[key]
            sample["files"]["pitch"] = previous["files"]["pitch"]
        for key, identity, allowed in [
            ("video_av", item.video_id, {"mp4"}),
            ("audio", item.audio_id, {"wav"}),
            ("text", item.text_id, {"txt", "md", "markdown"}),
            ("pitch", item.pitch_id, {"pitch"}),
        ]:
            if not identity:
                continue
            row = await dataset_service.get_dataset_with_access(db, identity, user.id)
            if row.project_id != anchor.project_id or row.file_format not in allowed:
                raise invalid("请选择当前项目中的 MP4、WAV 或 UTF-8 文本文件。")
            resolved_root = storage_service.filesystem_path(root.resolve())
            transcript = read_metadata(row).get("transcription") if key == "text" else None
            if transcript and transcript.get("workspace_path"):
                path = storage_service.filesystem_path(
                    checked_file(root, transcript["workspace_path"]).resolve()
                )
                if hashlib.sha256(path.read_bytes()).hexdigest() != row.sha256:
                    raise invalid("转录文本已变化，请重新打开后选择。")
                sample["text_range"] = {
                    name: transcript.get(name)
                    for name in ("source_id", "source_sha256", "start", "end", "method")
                }
            else:
                path = storage_service.get_local_object_path(row.object_key).resolve()
            if not path.is_relative_to(resolved_root):
                raise invalid("材料不在当前工作区保存范围内，请通过现有文件入口添加。")
            relative = path.relative_to(resolved_root).as_posix()
            checked_file(root, relative)
            sample[key] = relative
            sample[
                {
                    "video_av": "video_id",
                    "audio": "audio_id",
                    "text": "text_id",
                    "pitch": "pitch_id",
                }[key]
            ] = str(row.id)
            sample["files"][key] = {"id": str(row.id), "sha256": row.sha256}
            if key == "video_av" or (key == "audio" and item.video_id is None):
                sample["original_filename"] = row.original_filename
                entry = (
                    metadata.get(filename(row.original_filename))
                    if name_counts.get(filename(row.original_filename)) == 1
                    else None
                )
                sample["title"] = row.original_filename
                sample["display_title"] = read_metadata(row).get("display_name") or (
                    short_title(entry, row.original_filename) if entry else row.original_filename
                )
                if entry:
                    sample["filename_metadata"] = entry
        if sample.get("text_range"):
            provenance = sample["text_range"]
            matching = any(
                provenance.get("source_id") == sample.get(id_key)
                and provenance.get("source_sha256")
                == sample.get("files", {}).get(file_key, {}).get("sha256")
                for id_key, file_key in [("video_id", "video_av"), ("audio_id", "audio")]
            )
            if not matching:
                raise invalid("转录文本不属于所选音频或视频，请选择对应的转录。")
        samples.append(sample)
    descriptor = {
        "schema": SCHEMA,
        "kind": "media",
        "purpose": body.purpose
        if not anchor.media_case
        else anchor.media_case.get("purpose", body.purpose),
        "owner_case": owner_case if not anchor.media_case else anchor.media_case.get("owner_case"),
        "title": f"媒体处理 · {len(samples)} 个样本",
        "samples": samples,
    }

    # Preserve the identity of pre-metadata collections when the associations are unchanged.
    def association_key(case):
        return [
            {
                k: v
                for k, v in s.items()
                if k not in {"display_title", "filename_metadata", "original_filename"}
            }
            for s in case.get("samples", [])
        ]

    parent_group = (
        read_metadata(anchor).get("media_group_id", str(anchor.id)) if anchor.media_case else None
    )
    fingerprint = hashlib.sha256(canonical(descriptor).encode()).hexdigest()
    descriptor["identity"] = "media:" + fingerprint
    saved = next(
        (
            row
            for row in project_rows
            if (row.media_case or {}).get("kind") == "media"
            and (
                not parent_group
                or read_metadata(row).get("media_group_id", str(row.id)) == parent_group
            )
            and row.media_case.get("owner_case") == descriptor.get("owner_case")
            and association_key(row.media_case) == association_key(descriptor)
        ),
        None,
    )
    await acquire_write_fence(db, user.id)
    if saved is None:
        with tempfile.TemporaryDirectory(prefix="sf-media-") as directory:
            path = Path(directory) / "media.sfmedia"
            path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
            saved = await project_sources.store(
                db, anchor.project_id, user.id, str(path), path.name, "联合查看/media.sfmedia"
            )
            saved.preview_json = json.dumps(
                {
                    **read_metadata(saved),
                    "media_case": descriptor,
                    "media_group_id": parent_group or str(saved.id),
                    "display_name": descriptor["title"],
                },
                ensure_ascii=False,
            )
            saved.upload_status = UploadStatus.READY
    if parent_group and saved.id != anchor.id:
        meta = read_metadata(saved)
        prior = (
            read_metadata(anchor)
            .get("sample_selection", {})
            .get(str(body.task_id or "project"), {})
        )
        if prior:
            selected = next(
                (item for item in samples if item["sample_id"] == prior.get("sample_id")), None
            )
            selection = (
                prior
                if selected
                else {**prior, "sample_id": samples[0]["sample_id"], "start": None, "end": None}
            )
            meta.setdefault("sample_selection", {})[str(body.task_id or "project")] = selection
            saved.preview_json = json.dumps(meta, ensure_ascii=False)
    if body.task_id:
        live = SimpleNamespace(
            project=anchor.project_id, value={"task_context": {"id": str(body.task_id)}}
        )
        await tasks.append(db, user, live, "dataset_ids", saved.id)
    await db.commit()
    return {"id": str(saved.id), "sample_ids": [sample["sample_id"] for sample in samples]}


async def linked_samples(db, source, root, descriptor):
    sources = list(
        (
            await db.scalars(
                select(Dataset).where(
                    Dataset.project_id == source.project_id,
                    Dataset.upload_status != UploadStatus.DELETED,
                )
            )
        ).all()
    )
    by_path = {r.source_path: str(r.id) for r in sources if r.local_file}
    rows = sample_rows(root, descriptor)
    for row in rows:
        for key, target in [
            ("video_av", "video_id"),
            ("audio", "audio_id"),
            ("text", "text_id"),
            ("image_path", "image_dataset_id"),
        ]:
            if key in row and target not in row:
                row[target] = by_path.get(row[key].replace("\\", "/"))
    return rows


def _mentions_any(value, identities):
    """Conservatively detect UUID references, including compact UUID encodings."""
    raw = str(value or "").lower()
    return any(identity in raw or identity.replace("-", "") in raw for identity in identities)


async def _other_database_reference(db, project_id, identities):
    """Check mapped persistence beyond workspace and dataset metadata.

    Tables absent from the current database cannot contain a reference. The
    intrinsic variable rows for a dataset are not independent consumers.
    """
    import app.models  # noqa: F401 -- load all mapped persistence tables
    from app.models.base import Base

    present = await db.run_sync(
        lambda session: set(inspect(session.connection()).get_table_names())
    )
    excluded = {
        "datasets",
        "workspace_preview_records",
        "workspace_preview_revisions",
        "materials",
        "variables",
    }
    needles = {value for identity in identities for value in (identity, identity.replace("-", ""))}
    for table in Base.metadata.sorted_tables:
        if table.schema or table.name not in present or table.name in excluded:
            continue
        fields = [
            column
            for column in table.columns
            if not column.primary_key and isinstance(column.type, (String, Text, Uuid, JSON))
        ]
        if not fields:
            continue
        clauses = [cast(column, String).contains(value) for column in fields for value in needles]
        query = select(table.c[next(iter(table.primary_key.columns)).name]).where(or_(*clauses))
        if "project_id" in table.c:
            query = query.where(table.c.project_id == project_id)
        if await db.scalar(query.limit(1)) is not None:
            return True
    return False


async def archive_unreferenced_case_runs(db, user, project_id, version_ids, sample_id):
    """Soft-archive explicitly owned, terminal media runs only when references are absent.

    Old records and active work are never inferred to belong to a case. Dataset
    objects and the previous run revisions remain intact for recovery.
    """
    from app.models.material import Material
    from app.models.ml import MLExperiment
    from app.models.profiling import DatasetProfile
    from app.models.quality import QualityRun
    from app.models.research import ResearchWorkspace
    from app.models.statistics import StatisticalRun
    from app.models.workflow import Workflow, WorkflowRun

    # Research ledgers contain additional immutable references. In their
    # presence, retain the run unless a complete ledger reference graph exists.
    research_exists = await db.scalar(
        select(ResearchWorkspace.id).where(ResearchWorkspace.project_id == project_id).limit(1)
    )
    records = (
        await db.scalars(select(WorkspaceRecord).where(WorkspaceRecord.project_id == project_id))
    ).all()
    revisions = (
        await db.execute(
            select(WorkspaceRevision.record_id, WorkspaceRevision.payload_json)
            .join(WorkspaceRecord, WorkspaceRecord.id == WorkspaceRevision.record_id)
            .where(WorkspaceRecord.project_id == project_id)
        )
    ).all()
    datasets = (await db.scalars(select(Dataset).where(Dataset.project_id == project_id))).all()
    materials = (await db.scalars(select(Material).where(Material.project_id == project_id))).all()
    by_id = {str(item.id): item for item in datasets}
    archived = retained = 0
    for row in records:
        if row.kind != "media_run" or row.user_id != user.id:
            continue
        run = MediaRun.model_validate_json(row.payload_json)
        owner = run.owner_case or {}
        if (
            owner.get("collection_id") not in version_ids
            or owner.get("sample_id") != sample_id
            or run.case_archive
        ):
            continue
        if (
            research_exists
            or run.task_id
            or run.status not in {"completed", "failed", "interrupted"}
            or row.id in JOBS
        ):
            retained += 1
            continue
        output_ids = set(run.result_ids) | {
            value for value in (run.summary_id, run.package_id, run.index_id) if value
        }
        outputs = [by_id.get(identity) for identity in output_ids]
        if any(item is None for item in outputs):
            retained += 1
            continue
        if any(
            item.upload_status == UploadStatus.DELETED
            or item.local_file
            or read_metadata(item).get("source_dataset_id") != str(run.dataset_id)
            or (read_metadata(item).get("media_result") or {}).get("run_id") != str(row.id)
            or (read_metadata(item).get("media_result") or {}).get("owner_case") != owner
            or (read_metadata(item).get("media_result") or {}).get("kind")
            not in {"summary_csv", "arrays_npz", "index_json", "parameters_json", "media"}
            for item in outputs
        ):
            retained += 1
            continue
        identities = {str(row.id), *output_ids}
        if (
            any(
                other.id != row.id and _mentions_any(other.payload_json, identities)
                for other in records
            )
            or any(
                record_id != row.id and _mentions_any(payload, identities)
                for record_id, payload in revisions
            )
            or any(
                str(other.id) not in output_ids
                and any(
                    _mentions_any(value, identities)
                    for value in (
                        other.preview_json,
                        other.schema_json,
                        other.object_key,
                        other.file_path,
                    )
                )
                for other in datasets
            )
            or any(
                any(
                    _mentions_any(value, identities)
                    for value in (item.metadata_json, item.extraction_json, item.object_key)
                )
                for item in materials
            )
            or any(
                other.object_key == item.object_key
                for item in outputs
                for other in datasets
                if str(other.id) not in output_ids
            )
            or any(other.object_key == item.object_key for item in outputs for other in materials)
        ):
            retained += 1
            continue
        # Foreign-key references from persisted analyses, profiles, or workflows
        # make the result shared. Variables belong to their dataset itself.
        try:
            parsed_ids = [uuid.UUID(identity) for identity in output_ids]
        except ValueError:
            retained += 1
            continue
        referenced = False
        for model in (
            MLExperiment,
            DatasetProfile,
            QualityRun,
            StatisticalRun,
            Workflow,
            WorkflowRun,
        ):
            if parsed_ids and await db.scalar(
                select(model.id).where(model.dataset_id.in_(parsed_ids)).limit(1)
            ):
                referenced = True
                break
        if referenced or await _other_database_reference(db, project_id, identities):
            retained += 1
            continue
        run.case_archive = "case_removed"
        row.revision += 1
        row.payload_json = run.model_dump_json()
        db.add(
            WorkspaceRevision(
                record_id=row.id, revision=row.revision, payload_json=row.payload_json
            )
        )
        for item in outputs:
            meta = read_metadata(item)
            meta["case_archive"] = {
                "reason": "case_removed",
                "run_id": str(row.id),
                "previous_status": item.upload_status.value,
            }
            item.preview_json = json.dumps(meta, ensure_ascii=False)
            item.upload_status = UploadStatus.DELETED
        archived += 1
    return archived, retained


async def change_case(db, user, dataset_id, sample_id, title=None, remove=False):
    """Edit a case across its association versions without touching source files."""
    await acquire_write_fence(db, user.id)
    source, _, descriptor = await collection(db, user, dataset_id)
    if descriptor.get("kind") != "media":
        raise invalid("只有手动关联的案例可以在此修改。")
    if not any(str(row.get("sample_id")) == sample_id for row in descriptor.get("samples", [])):
        raise invalid("案例已变化，请刷新后重试。", 409)
    if not remove and not (title or "").strip():
        raise invalid("请填写案例名称。")
    group = read_metadata(source).get("media_group_id", str(source.id))
    rows = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == source.project_id,
                Dataset.upload_status != UploadStatus.DELETED,
            )
        )
    ).all()
    versions = [
        row
        for row in rows
        if row.media_case
        and row.media_case.get("kind") == "media"
        and read_metadata(row).get("media_group_id", str(row.id)) == group
    ]
    version_ids = {str(row.id) for row in versions}
    archived_runs, retained_runs = (
        await archive_unreferenced_case_runs(db, user, source.project_id, version_ids, sample_id)
        if remove
        else (0, 0)
    )
    for row in versions:
        meta = read_metadata(row)
        cases = meta["media_case"].get("samples", [])
        if remove:
            for case in cases:
                if str(case.get("sample_id")) == sample_id:
                    case["case_deleted"] = True
                    case.pop("case_title", None)
            for scope, selected in list(meta.get("sample_selection", {}).items()):
                if selected.get("sample_id") == sample_id:
                    meta["sample_selection"].pop(scope)
        else:
            for case in cases:
                if str(case.get("sample_id")) == sample_id:
                    case["case_title"] = title.strip()
        row.preview_json = json.dumps(meta, ensure_ascii=False)
    # Coding snapshots belong to the project and can also contain other cases.
    # Only segments with both an exact collection version and sample ID match.
    records = (
        await db.scalars(
            select(WorkspaceRecord).where(
                WorkspaceRecord.project_id == source.project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "coding",
            )
        )
    ).all()
    for record in records:
        changed = False
        for entry in [record]:
            payload = json.loads(entry.payload_json)
            segments = payload.get("segments", [])

            def matches(segment):
                return (
                    str(segment.get("sample_collection_id") or "") in version_ids
                    and str(segment.get("sample_id") or "") == sample_id
                )

            if any(matches(segment) for segment in segments):
                changed = True
            for segment in segments:
                if matches(segment):
                    if remove:
                        for key in ("sample_collection_id", "sample_id", "sample_title"):
                            segment.pop(key, None)
                    else:
                        segment["sample_title"] = title.strip()
            entry.payload_json = json.dumps(payload, ensure_ascii=False)
        if changed:
            # A stale open editor must not silently restore the previous document.
            record.revision += 1
            db.add(
                WorkspaceRevision(
                    record_id=record.id, revision=record.revision, payload_json=record.payload_json
                )
            )
    await db.commit()
    return {
        "removed": remove,
        "archived_runs": archived_runs,
        "retained_runs": retained_runs,
        "group_empty": not any(
            not case.get("case_deleted") for case in read_metadata(source)["media_case"]["samples"]
        ),
    }


def speech_count_scope(dataset_id, sample_id, start, end, options):
    """A manual count belongs to one recording version, interval, sound source and unit."""
    return [
        str(dataset_id),
        sample_id,
        start or 0,
        end,
        options.get("audio_source"),
        options.get("speech_count_unit"),
    ]


def recorded_media_parameters(parameters):
    # Pre-1.0.42 runs/drafts used the F0 floor and shared time step for intensity.
    # Resolve them only when reading saved settings, never for a fresh request.
    if not parameters:
        return parameters
    return {
        "intensity_minimum_pitch_hz": parameters.get("pitch_floor_hz", 75),
        "intensity_time_step": parameters.get("praat_time_step", 0.01),
        "intensity_subtract_mean": True,
        **parameters,
    }


def saved_selection(source, task_id):
    selection = read_metadata(source).get("sample_selection", {}).get(str(task_id), {})
    processing = recorded_media_parameters(selection.get("processing") or {})
    if processing:
        selection = {**selection, "processing": processing}
    if processing.get("speech_count") is None:
        return selection
    scope = speech_count_scope(
        source.id,
        selection.get("sample_id"),
        selection.get("start"),
        selection.get("end"),
        processing,
    )
    if selection.get("speech_count_for") == scope:
        return selection
    # Old drafts could carry an unrelated count after changing sample. Historical runs are
    # immutable.
    return {**selection, "processing": {**processing, "speech_count": None}}


async def view(db, user, identity, task_id="project"):
    source, root, descriptor = await collection(db, user, identity)
    rows = await linked_samples(db, source, root, descriptor)
    if descriptor["kind"] == "media" and any(not r.get("display_title") for r in rows):
        from app.services.media_filename_metadata import filename, filename_metadata

        labels = await filename_metadata(db, source.project_id, root)
        for row in rows:
            if not row.get("display_title"):
                row["display_title"] = labels.get(filename(row["title"]), {}).get(
                    "label", row["title"]
                )
    runs = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == source.project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "media_run",
            )
            .order_by(WorkspaceRecord.created_at.desc())
        )
    ).all()
    group_id = read_metadata(source).get("media_group_id", str(source.id))
    members = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == source.project_id,
                Dataset.upload_status != UploadStatus.DELETED,
            )
        )
    ).all()
    versions = {
        item.id: item
        for item in members
        if item.media_case and read_metadata(item).get("media_group_id", str(item.id)) == group_id
    }
    results = []
    for row in runs:
        run = MediaRun.model_validate_json(row.payload_json)
        if run.dataset_id in versions and not run.case_archive:
            results.append(await get_run(db, user, versions[run.dataset_id], row.id))
    import wave

    for sample in rows:
        if sample.get("audio") and str(sample["audio"]).lower().endswith(".wav"):
            try:
                with wave.open(str(checked_file(root, sample["audio"])), "rb") as sound:
                    sample["audio_sample_rate"] = sound.getframerate()
            except (wave.Error, OSError):
                pass
    return {
        "kind": descriptor["kind"],
        "samples": rows,
        "runs": results,
        "selection": saved_selection(source, task_id),
    }


async def put(db, user, project, identity, payload):
    # Reuse the versioned workspace run writer and its actor transaction fence.
    from app.services.plots import put as put_record

    await acquire_write_fence(db, user.id)
    return await put_record(db, user, project, payload, identity)


def failure_reason(message, progress):
    detail = (message or "").splitlines()[-1:] or [""]
    detail = re.sub(r"^\w+(?:Error|Exception):\s*", "", detail[0]).strip()
    if "Cannot decode frame" in detail:
        return "均匀取帧：抽出的图片未能读取，请重新取帧。"
    if "No video frames" in detail:
        return "均匀取帧：所选时间段内没有视频帧，请调整起止时间。"
    if "Audio selection is shorter" in detail:
        return "声音测量：选段短于分析窗口，请扩大选段或减小窗口。"
    if "Audio stream is required" in detail:
        return "提取音轨：材料中没有所选的声音轨，请选择独立音频或其他视频。"
    if re.match(r"^[\u4e00-\u9fff]", detail):
        return detail[:160]
    stage = re.split(r" · ", progress or "")[-1]
    if not re.search(r"[\u4e00-\u9fff]", stage):
        stage = "读取材料"
    return f"{stage}：请确认所选材料可打开，并检查时间范围后重试。"


async def get_run(db, user, source, identity):
    row = await load_record(db, identity, user, source.project_id)
    if row.kind != "media_run":
        raise invalid("未找到这次处理。", 404)
    payload = MediaRun.model_validate_json(row.payload_json)
    if payload.dataset_id != source.id:
        raise invalid("这次处理不属于当前样本。", 404)
    if payload.status in {"queued", "running"} and payload.instance != INSTANCE:
        payload.status = "interrupted"
        payload.error_message = (
            "软件退出时处理尚未完成。原文件和已经保存的旧结果仍保留，可重新处理。"
        )
        await put(db, user, source.project_id, row.id, payload)
        await db.commit()
    return {
        "id": str(row.id),
        **payload.model_dump(mode="json"),
        "parameters": recorded_media_parameters(payload.parameters),
        "failure_reason": failure_reason(payload.error_message, payload.progress)
        if payload.status == "failed"
        else None,
    }


async def start(db, user, identity, options: MediaRequest):
    source, root, descriptor = await collection(db, user, identity)
    owner = descriptor.get("owner_case")
    if owner:
        owner_source, _, owner_descriptor = await collection(
            db, user, uuid.UUID(owner["collection_id"])
        )
        if owner_source.project_id != source.project_id or not any(
            str(case.get("sample_id")) == owner["sample_id"]
            and case.get("case_title")
            and not case.get("case_deleted")
            for case in owner_descriptor.get("samples", [])
        ):
            raise invalid("所属案例已删除或不可用。", 409)
    if options.task_id:
        await tasks.context(db, source.project_id, user, str(options.task_id))
    samples = sample_rows(root, descriptor)
    if options.sample_ids:
        chosen = set(options.sample_ids)
        samples = [row for row in samples if row["sample_id"] in chosen]
        if len(samples) != len(chosen):
            raise invalid("选中的样本已变化，请刷新后重选。")
    if not samples:
        raise invalid("当前没有可处理样本。")
    if descriptor["kind"] == "media":
        audio_only = all(s.get("audio") and not s.get("video_av") for s in samples)
        if options.steps is None:
            options = options.model_copy(
                update={
                    "steps": ["pitch", "intensity"]
                    if audio_only
                    else ["extract_audio", "mfcc", "rms", "frames"]
                }
            )
        if audio_only:
            options = options.model_copy(update={"audio_source": "independent"})
    if descriptor["kind"] == "coco" and options.steps is not None:
        raise invalid("COCO 仍使用现有图像与描述处理组合；可选步骤用于视频材料。")
    fingerprints = []
    for sample in samples:
        for key in ("video_av", "audio", "text", "image_path"):
            if not sample.get(key):
                continue
            path = checked_file(root, sample[key])
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            expected = sample.get("files", {}).get(key, {}).get("sha256")
            if expected and expected != digest:
                raise invalid(
                    f"文件已变化：{path.name}。请刷新文件夹后重新选择材料；旧关联与结果已保留。"
                )
            fingerprints.append([sample["sample_id"], key, digest])
    config = runtime_config()
    if options.audio_filter != "none":
        from app.services.media_features import _run

        for sample in samples:
            key = (
                "audio"
                if options.audio_source == "independent" or not sample.get("video_av")
                else "video_av"
            )
            path = checked_file(root, sample.get(key) or "")
            info = json.loads(
                await asyncio.to_thread(
                    _run,
                    [
                        str(config["options"]["ffprobe_path"]),
                        "-v",
                        "error",
                        "-select_streams",
                        "a:0",
                        "-show_entries",
                        "stream=sample_rate",
                        "-of",
                        "json",
                        str(path),
                    ],
                )
            )
            streams = info.get("streams", [])
            if not streams:
                raise invalid("所选材料没有声音，请选择对应 WAV。")
            ceiling = int(streams[0]["sample_rate"]) / 2
            if (
                (options.audio_filter != "lowpass" and options.filter_low_hz >= ceiling)
                or (options.audio_filter != "highpass" and options.filter_high_hz > ceiling)
                or options.filter_transition_hz >= ceiling
            ):
                raise invalid(
                    f"滤波参数超出 {path.name} 的频率范围：上限不超过 {ceiling:g} Hz，\
下限和过渡宽度须小于它。"
                )
    if "pitch" in (options.steps or []):
        from app.services.praat_pitch import configured_path, runtime_identity

        try:
            executable = configured_path() if options.pitch_method.startswith("filtered_") else None
            config["options"]["pitch_runtime"] = await asyncio.to_thread(
                runtime_identity, config, options.pitch_method, executable
            )
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    engine = "media-20261106-rms-waveform-2"
    if "speech_rate" in (options.steps or []) and (options.start or options.end is not None):
        # Older clipped runs could use a full transcript or a count retained from another selection.
        engine = "media-speech-range-3"
    if {"intensity", "formants"} & set(options.steps or []):
        engine += "-intensity-bandwidth-1"
    if "pitch" in (options.steps or []):
        engine += "-native-pitch-candidates-1"
    signature = hashlib.sha256(
        canonical(
            {
                "parameters": options.model_dump(mode="json", exclude={"force", "task_id"}),
                "samples": samples,
                "files": fingerprints,
                "tools": config["options"],
                "engine": engine,
            }
        ).encode()
    ).hexdigest()
    if not options.force:
        prior = (
            await db.scalars(
                select(WorkspaceRecord)
                .where(
                    WorkspaceRecord.project_id == source.project_id,
                    WorkspaceRecord.user_id == user.id,
                    WorkspaceRecord.kind == "media_run",
                )
                .order_by(WorkspaceRecord.created_at.desc())
            )
        ).all()
        for row in prior:
            previous = MediaRun.model_validate_json(row.payload_json)
            if (
                previous.dataset_id == source.id
                and not previous.case_archive
                and previous.status == "completed"
                and previous.parameters.get("signature") == signature
            ):
                if options.task_id:
                    live = SimpleNamespace(
                        project=source.project_id,
                        value={"task_context": {"id": str(options.task_id)}},
                    )
                    for identity in [
                        source.id,
                        previous.summary_id,
                        previous.package_id,
                        previous.index_id,
                    ]:
                        if identity:
                            await tasks.append(
                                db, user, live, "dataset_ids", uuid.UUID(str(identity))
                            )
                    await db.commit()
                return {"id": str(row.id), **previous.model_dump(mode="json"), "reused": True}
    if options.start is not None and options.end is not None and options.start >= options.end:
        raise invalid("选段结束必须晚于开始。")
    run_id = uuid.uuid4()
    run_owner = descriptor.get("owner_case")
    if run_owner is None and descriptor.get("kind") == "media" and len(samples) == 1:
        case = samples[0]
        if case.get("case_title") and not case.get("case_deleted"):
            run_owner = {"collection_id": str(source.id), "sample_id": str(case["sample_id"])}
    payload = MediaRun(
        title=f"{
            ('媒体处理')
            if descriptor[('kind')]
            == (
                'med\
ia'
            )
            else descriptor[('kind')].upper()
        } · {len(samples)} 个样本 \
· {datetime.now().strftime('%m-%d %H:%M:%S')}",
        dataset_id=source.id,
        task_id=options.task_id,
        owner_case=run_owner,
        instance=INSTANCE,
        execution={"pitch": config["options"]["pitch_runtime"]}
        if "pitch" in (options.steps or [])
        else {},
        parameters={
            **options.model_dump(mode="json"),
            "case": descriptor,
            "sample_ids": [s["sample_id"] for s in samples],
            "signature": signature,
            "input_files": fingerprints,
        },
        started_at=datetime.now(UTC).isoformat(),
        progress="等待本地处理",
    )
    await put(db, user, source.project_id, run_id, payload)
    await db.commit()
    JOBS[run_id] = asyncio.create_task(
        execute(run_id, user.id, source.project_id, root, descriptor, samples, options, config)
    )
    JOBS[run_id].add_done_callback(lambda _: JOBS.pop(run_id, None))
    return {"id": str(run_id), **payload.model_dump(mode="json")}


def runtime_config():
    path = (
        Path(sys.executable).parent / "media-runtime.json"
        if getattr(sys, "frozen", False)
        else Path(
            os.environ.get(
                "STATFORGE_MEDIA_RUNTIME_CONFIG",
                Path(__file__).resolve().parents[4] / ".local" / "release-base" / "media-runtime.json",
            )
        )
    )
    if not path.is_file():
        raise invalid("本地特征处理组件未找到，请使用完整的新版桌面程序。")
    config = read_json(path)

    # Release resources are relative to this file, so moving/installing the
    # application does not retain the developer machine's drive letters.
    def resource(value):
        candidate = Path(value)
        return str(candidate if candidate.is_absolute() else (path.parent / candidate).resolve())

    for key in ("python", "model_root"):
        config[key] = resource(config[key])
    for key in ("ffmpeg_path", "ffprobe_path", "resnet_weights", "minilm_path", "whisper_path"):
        if config["options"].get(key):
            config["options"][key] = resource(config["options"][key])
    if config.get("portable"):
        data = Path(os.environ["APP_DATA_PATH"])
        config["temp"] = str(data / "temp" / "media")
        config["cache"] = str(data / "cache" / "numba")
        Path(config["temp"]).mkdir(parents=True, exist_ok=True)
        Path(config["cache"]).mkdir(parents=True, exist_ok=True)
    return config


async def execute(identity, owner, project, root, descriptor, samples, options, config):
    output = root / ".statforge" / "storage" / "media-runs" / identity.hex
    local.checked_path(output)
    output.mkdir(parents=True, exist_ok=False)
    process = None
    try:
        values = {
            **config["options"],
            **options.model_dump(mode="json"),
            **{k: v for k, v in descriptor.items() if k.endswith("_json")},
        }
        request = output / "request.json"
        request.write_text(
            json.dumps(
                {
                    "kind": "media" if options.steps is not None else descriptor["kind"],
                    "root": str(root),
                    "samples": samples,
                    "output": str(output),
                    "options": values,
                }
            ),
            encoding="utf-8",
        )
        worker = Path(__file__).with_name("media_feature_worker.py")
        env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "HF_HOME": config["model_root"],
            "TORCH_HOME": config["model_root"],
            "NUMBA_CACHE_DIR": config["cache"],
            "TEMP": config["temp"],
            "TMP": config["temp"],
        }
        process = await asyncio.create_subprocess_exec(
            config["python"],
            str(worker),
            str(request),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )

        async def errors():
            content = await process.stderr.read()
            (output / "process.log").write_bytes(content)
            return content.decode("utf-8", errors="replace")

        stderr = asyncio.create_task(errors())
        async for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            async with async_session_factory() as db:
                user = await db.get(User, owner)
                row = await load_record(db, identity, user, project)
                run = MediaRun.model_validate_json(row.payload_json)
                run.status, run.progress = "running", str(event.get("label", "正在提取特征"))
                await put(db, user, project, identity, run)
                await db.commit()
        code = await process.wait()
        error_log = await stderr
        if code:
            raise RuntimeError(
                error_log.strip().splitlines()[-1] if error_log.strip() else "本地特征程序未完成。"
            )
        result = read_json(output / "result.json")
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            row = await load_record(db, identity, user, project)
            run = MediaRun.model_validate_json(row.payload_json)
            source = await dataset_service.get_dataset_with_access(db, run.dataset_id, owner)
            await acquire_write_fence(db, user.id)
            result_rows = []
            names = {
                "summary_csv": "特征摘要.csv",
                "arrays_npz": "完整特征.npz",
                "index_json": "样本与特征索引.json",
                "parameters_json": "处理参数.json",
            }
            for key, name in names.items():
                path = Path(result[key]).resolve()
                if not path.is_relative_to(output.resolve()) or not path.is_file():
                    raise invalid("处理产物路径无效。")
                if key == "summary_csv":
                    saved = await dataset_service.upload_dataset(
                        db, project, owner, str(path), name, allow_duplicate=True
                    )
                else:
                    saved = await project_sources.store(
                        db, project, owner, str(path), name, f"特征/{identity.hex}/{name}"
                    )
                meta = read_metadata(saved)
                meta.update(
                    source_dataset_id=str(source.id),
                    display_name=f"{run.title} · {name.rsplit('.', 1)[0]}",
                    media_result={
                        "run_id": str(identity),
                        "collection_id": str(source.id),
                        "owner_case": run.owner_case,
                        "kind": key,
                    },
                )
                saved.preview_json = json.dumps(meta, ensure_ascii=False)
                result_rows.append(saved)
                if key == "summary_csv":
                    run.summary_id = str(saved.id)
                if key == "arrays_npz":
                    run.package_id = str(saved.id)
                if key == "index_json":
                    run.index_id = str(saved.id)
            # Derived media are ordinary files in task data, with explicit parent identity.
            for filename in result.get("media_files", []):
                path = Path(filename).resolve()
                if not path.is_relative_to(output.resolve()):
                    raise invalid("媒体产物不在本次处理目录。")
                saved = await project_sources.store(
                    db, project, owner, str(path), path.name, f"特征/{identity.hex}/{path.name}"
                )
                saved.preview_json = json.dumps(
                    {
                        **read_metadata(saved),
                        "source_dataset_id": str(source.id),
                        "media_result": {
                            "run_id": str(identity),
                            "collection_id": str(source.id),
                            "owner_case": run.owner_case,
                            "kind": "media",
                        },
                    }
                )
                result_rows.append(saved)
            run.result_ids = [str(r.id) for r in result_rows]
            if options.task_id:
                live = SimpleNamespace(
                    project=project, value={"task_context": {"id": str(options.task_id)}}
                )
                for saved in [source, *result_rows[:4]]:
                    await tasks.append(db, user, live, "dataset_ids", saved.id)
            run.status, run.progress = "completed", f"{len(samples)} 个样本已完成"
            run.artifact_directory = str(output)
            run.completed_at = datetime.now(UTC).isoformat()
            await put(db, user, project, identity, run)
            await db.commit()
    except Exception as error:
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            row = await load_record(db, identity, user, project)
            run = MediaRun.model_validate_json(row.payload_json)
            run.status, run.error_message = "failed", str(error)[:1500]
            run.completed_at = datetime.now(UTC).isoformat()
            await put(db, user, project, identity, run)
            await db.commit()


async def result_index(db, user, dataset):
    meta = read_metadata(dataset).get("media_result")
    if not meta:
        raise invalid("这份数据没有关联的多模态特征。")
    source = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(meta["collection_id"]), user.id
    )
    run = await get_run(db, user, source, uuid.UUID(meta["run_id"]))
    if run["status"] != "completed":
        raise invalid("这次处理尚未完成。")
    index = await dataset_service.get_dataset_with_access(db, uuid.UUID(run["index_id"]), user.id)
    arrays = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(run["package_id"]), user.id
    )
    import numpy as np

    index_data = read_json(storage_service.get_local_object_path(index.object_key))
    # Old runs have only filenames; resolve display metadata from the same collection,
    # without replacing the saved sample identity, sources or numerical results.
    if source.file_format == "pitch" and index_data.get("native_pitch"):
        root = local.project_root(source.project_id)
        from app.services import pitch_native

        index_data["native_pitch"]["association"] = await pitch_native.association(db, user, source)
    else:
        _, root, descriptor = await collection(db, user, source.id)
    from app.services.media_filename_metadata import filename, filename_metadata, short_title

    labels = await filename_metadata(db, source.project_id, root) if root else {}
    for entry in index_data.get("entries", []):
        sample = entry.get("sample") or {}
        metadata = sample.get("filename_metadata") or labels.get(filename(sample.get("title", "")))
        if metadata:
            sample["display_title"] = short_title(metadata, sample.get("title", ""))

    with np.load(
        storage_service.get_local_object_path(arrays.object_key), allow_pickle=False
    ) as bundle:
        shapes = {
            key: {"shape": list(bundle[key].shape), "dtype": str(bundle[key].dtype)}
            for key in bundle.files
        }
        previews = []
        for entry in index_data.get("entries", []):
            prefix = entry.get("array_prefix")
            if not prefix:
                continue
            curves = {}
            for name, clock, unit in [
                ("waveform", "waveform", "amplitude"),
                ("rms", "audio", "amplitude"),
                ("pitch", "pitch", "Hz"),
                ("intensity", "intensity", "dB"),
                *[(f"f{n}", "formant", "Hz") for n in range(1, 5)],
            ]:
                value_key = (
                    f"{prefix}_{name}"
                    if name in ("rms", "waveform")
                    else f"{prefix}_{name}_db"
                    if name == "intensity"
                    else f"{prefix}_{name}_hz"
                )
                time_key = f"{prefix}_{clock}_times_player"
                if value_key not in bundle or time_key not in bundle:
                    continue
                values, times = bundle[value_key], bundle[time_key]
                if name == "rms" and values.ndim == 2:
                    values = values.mean(axis=0)
                if values.ndim != 1 or times.ndim != 1:
                    continue
                if len(values) != len(times) or not len(times):
                    continue
                indices = np.unique(np.linspace(0, len(times) - 1, min(len(times), 600), dtype=int))
                points, previous = [], None
                for position in indices:
                    value, time = values[position], times[position]
                    valid = np.isfinite(value) and np.isfinite(time)
                    # If an interval contains missing data, do not draw a line across it.
                    if (
                        previous is not None
                        and position > previous + 1
                        and not np.isfinite(values[previous + 1 : position]).all()
                    ):
                        points.append({"time": None, "value": None})
                    points.append(
                        {
                            "time": float(time) if np.isfinite(time) else None,
                            "value": float(value) if valid else None,
                        }
                    )
                    previous = position
                curves[name] = {
                    "unit": unit,
                    "points": points,
                    "valid_count": int((np.isfinite(values) & np.isfinite(times)).sum()),
                    "total_count": int(len(times)),
                }
            if curves:
                sample = entry.get("sample", {})
                previews.append(
                    {
                        "sample_id": sample.get("sample_id"),
                        "title": sample.get("title"),
                        "display_title": sample.get("display_title"),
                        "curves": curves,
                    }
                )
    # The collection access above verifies this project's owner. Fetch its saved
    # media in one query instead of two database round trips per extracted frame.
    identities = [uuid.UUID(identity) for identity in run["result_ids"]]
    items = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == source.project_id,
                Dataset.id.in_(identities),
                Dataset.upload_status != UploadStatus.DELETED,
            )
        )
    ).all()
    by_id = {str(item.id): item for item in items}
    from app.services.material_run_facts import number as finite_number

    summary = by_id.get(run["summary_id"])
    summary_rows = read_metadata(summary).get("rows", []) if summary else []
    counts = {
        str(row.get("sample_id")): row["speech_count"]
        for row in summary_rows
        if row.get("speech_count") is not None
    }
    for entry in index_data.get("entries", []):
        speech = entry.get("speech_rate")
        if not speech:
            continue
        sample = entry.get("sample") or {}
        speech.setdefault("count", counts.get(str(sample.get("sample_id"))))
        if speech.get("count_source") == "associated_text" and not sample.get("text_range"):
            segment = entry.get("segment") or {}
            duration = finite_number((entry.get("audio_format") or {}).get("duration"))
            start, end = (
                finite_number(segment.get("audio_player_start")),
                finite_number(segment.get("audio_player_end")),
            )
            if (
                start is not None
                and end is not None
                and duration is not None
                and (start > 0.0005 or end < duration - 0.0005)
            ):
                speech["range_note"] = "这次旧运行把完整台词用于选段。请填写选段词数后重新处理。"
    media = [
        {"id": identity, "name": by_id[identity].original_filename}
        for identity in run["result_ids"]
        if identity in by_id
        and read_metadata(by_id[identity]).get("media_result", {}).get("kind") == "media"
    ]
    methods = None
    parameter_file = next(
        (
            item
            for item in items
            if read_metadata(item).get("media_result", {}).get("kind") == "parameters_json"
        ),
        None,
    )
    if parameter_file:
        try:
            methods = read_json(storage_service.get_local_object_path(parameter_file.object_key))
        except (OSError, ValueError):
            pass  # Older or missing parameter artifacts remain readable.
    return {
        "run": run,
        "index": index_data,
        "arrays": shapes,
        "media": media,
        "curve_previews": previews,
        "method_parameters": methods,
    }


async def search(db, user, source, run_id, sample_id, caption_id=None):
    run = await get_run(db, user, source, run_id)
    arrays = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(run["package_id"]), user.id
    )
    result = await result_index(db, user, arrays)
    if result["index"]["kind"] != "coco":
        raise invalid("相似检索用于当前 COCO 图像或描述。")
    import numpy as np

    entries = result["index"]["entries"]
    with np.load(
        storage_service.get_local_object_path(arrays.object_key), allow_pickle=False
    ) as bundle:
        if caption_id is not None:
            captions = [(entry, c) for entry in entries for c in entry["captions"]]
            selected = next(
                (i for i, (_, c) in enumerate(captions) if c["annotation"]["id"] == caption_id),
                None,
            )
            if selected is None:
                raise invalid("这次处理没有包含所选描述，请处理全部样本。")
            vectors = bundle["caption_minilm"]
            scores = vectors @ vectors[selected]
            ranked = np.argsort(-scores)
            matches = []
            for i in ranked:
                entry, caption = captions[int(i)]
                if int(i) == selected:
                    continue
                matches.append(
                    {
                        "sample_id": str(entry["image"]["id"]),
                        "title": caption["annotation"]["caption"],
                        "score": float(scores[i]),
                        "caption_id": caption["annotation"]["id"],
                    }
                )
                if len(matches) == 10:
                    break
        else:
            selected = next(
                (i for i, entry in enumerate(entries) if str(entry["image"]["id"]) == sample_id),
                None,
            )
            if selected is None:
                raise invalid("这次处理没有包含所选图像，请处理全部样本。")
            vectors = bundle["image_resnet"]
            vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            scores = vectors @ vectors[selected]
            matches = [
                {
                    "sample_id": str(entries[int(i)]["image"]["id"]),
                    "title": entries[int(i)]["image"]["file_name"],
                    "score": float(scores[i]),
                }
                for i in np.argsort(-scores)
                if int(i) != selected
            ][:10]
    return {
        "matches": matches,
        "space": "MiniLM text" if caption_id is not None else "ResNet image",
    }
