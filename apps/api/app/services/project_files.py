"""Owned project packages: original bytes, drafts and read-only historical snapshots."""

import hashlib
import io
import json
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.base import Base
from app.models.dataset import Dataset, Variable, VariableRole
from app.models.material import Material
from app.models.project import Project
from app.models.user import User
from app.preview.workspace import credential_free, save_record
from app.schemas.material import CitationRequest
from app.schemas.workspace import SaveRecord
from app.services import dataset_service, material_service, storage_service
from app.services.archive_staging import expanded_limit, require_disk_space

SCHEMA = "statforge-project-v2"
LEGACY_SCHEMA = "statforge-project-v1"
EXCLUDED = re.compile(
    r"auth|credential|provider|budget|charge|policy|approval|harness|lease|dispatch|receipt|intent|audit|event|outbox|config"
)


def fail(message="项目文件无法读取，请重新选择。"):
    return StatForgeError(422, "PROJECT_FILE_INVALID", message)


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda v: getattr(v, "value", str(v)),
        separators=(",", ":"),
    ).encode()


async def capture_records(db: AsyncSession, project: Project, user: User) -> dict:
    from app.services.plot_code_files import flush_project

    await flush_project(db, project, user)
    # Follow project descendants, never upstream users or provider configuration.
    tables = list(Base.metadata.tables.values())
    raw = {
        "projects": [
            dict(
                (await db.execute(select(Project.__table__).where(Project.id == project.id)))
                .mappings()
                .one()
            )
        ]
    }
    for _ in tables:
        changed = False
        for table in tables:
            if table.name == "projects" or EXCLUDED.search(table.name):
                continue
            clauses = []
            for fk in table.foreign_keys:
                parent = raw.get(fk.column.table.name, [])
                values = [
                    row[fk.column.name] for row in parent if row.get(fk.column.name) is not None
                ]
                if values:
                    clauses.append(fk.parent.in_(values))
            if not clauses:
                continue
            query = select(table).where(or_(*clauses))
            if "project_id" in table.c:
                query = query.where(table.c.project_id == project.id)
            if "user_id" in table.c:
                query = query.where(table.c.user_id == user.id)
            if "owner_id" in table.c:
                query = query.where(table.c.owner_id == user.id)
            rows = [dict(row) for row in (await db.execute(query)).mappings()]
            if len(rows) != len(raw.get(table.name, [])):
                raw[table.name] = rows
                changed = True
        if not changed:
            break
    records = json.loads(encoded(raw))
    # IDE bindings belong to this installation. Portable code is already in
    # PlotDraft/PlotRun; do not export host file names or restore live bindings.
    internal = {
        row["id"]
        for row in records.get("workspace_preview_records", [])
        if row.get("kind") == "plot_code_file"
    }
    if internal:
        records["workspace_preview_records"] = [
            row for row in records["workspace_preview_records"] if row["id"] not in internal
        ]
        records["workspace_preview_revisions"] = [
            row
            for row in records.get("workspace_preview_revisions", [])
            if row.get("record_id") not in internal
        ]
    # Credentials are not part of the package; existing field checks also guard drafts.
    await credential_free(db, user, records)
    return records


async def capture(db: AsyncSession, project: Project, user: User) -> bytes:
    records = await capture_records(db, project, user)
    objects = {}
    for table_name, rows in records.items():
        for row in rows:
            if row.get("upload_status") == "deleted":
                continue
            for field, key in row.items():
                if field in {"object_key", "artifact_key"} and isinstance(key, str) and key:
                    expected = (
                        row.get("sha256") if table_name in {"datasets", "materials"} else None
                    )
                    objects[key] = expected or objects.get(key)
    from app.plotting.archives import owned_objects

    for row in records.get("datasets", []):
        metadata = json.loads(row.get("preview_json") or "{}").get("multimodal", {})
        key = metadata.get("source_object_key")
        if row.get("upload_status") != "deleted" and key:
            source_id = metadata.get("source_dataset_id", row["id"])
            if source_id != row["id"]:
                original = next(
                    (
                        item
                        for item in records.get("datasets", [])
                        if item["id"] == source_id and item.get("upload_status") != "deleted"
                    ),
                    None,
                )
                if (
                    not original
                    or original["object_key"] != key
                    or original["sha256"] != metadata.get("source_sha256")
                ):
                    raise fail("多模态原文件引用不一致。")
            prefix = f"users/{user.id}/projects/{project.id}/datasets/{source_id}/"
            if not key.startswith(prefix):
                raise fail("多模态源文件的项目归属不一致。")
            objects[key] = metadata.get("source_sha256")
    objects.update(owned_objects(records, project, user))
    from app.services import report_sources

    report_objects = await report_sources.capture(db, records, project, user)
    objects.update(report_objects)
    stream = io.BytesIO()
    metadata = []
    total = 0
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for key in objects:
            with tempfile.TemporaryDirectory(prefix="sf-package-") as directory:
                path = Path(directory) / "source"
                storage_service.download_to_path(key, str(path))
                total += path.stat().st_size
                data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if objects[key] and sha != objects[key]:
                raise fail("源文件已变化，请重新导入后保存。")
            name = f"blobs/{sha}"
            if name not in bundle.namelist():
                bundle.writestr(name, data)
            metadata.append({"object_key": key, "sha256": sha, "bytes": len(data)})
        manifest = {
            "format": SCHEMA if report_objects else LEGACY_SCHEMA,
            "saved_at": datetime.now(UTC).isoformat(),
            "project_id": str(project.id),
            "records": records,
            "objects": metadata,
        }
        manifest_bytes = encoded(manifest)
        bundle.writestr("project.json", manifest_bytes)
        bundle.writestr("project.sha256", hashlib.sha256(manifest_bytes).hexdigest())
    return stream.getvalue()


MAX_MANIFEST_BYTES = 64 * 1024**2


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def unpack(path: Path, staging: Path):
    try:
        with zipfile.ZipFile(path) as bundle:
            entries = bundle.infolist()
            if len(entries) > 10000 or len({item.filename for item in entries}) != len(entries):
                raise fail()
            if any(
                item.filename not in {"project.json", "project.sha256"}
                and not re.fullmatch(r"blobs/[a-f0-9]{64}", item.filename)
                for item in entries
            ):
                raise fail()

            def read_limited(name, limit):
                with bundle.open(name) as source:
                    data = source.read(limit + 1)
                    if len(data) > limit:
                        raise fail()
                    return data

            manifest_bytes = read_limited("project.json", MAX_MANIFEST_BYTES)
            if (
                hashlib.sha256(manifest_bytes).hexdigest()
                != read_limited("project.sha256", 64).decode()
            ):
                raise fail()
            manifest = json.loads(manifest_bytes)
            if manifest.get("format") not in {SCHEMA, LEGACY_SCHEMA} or not isinstance(
                manifest.get("records"), dict
            ):
                raise fail()
            if any(
                not isinstance(items, list) or any(not isinstance(row, dict) for row in items)
                for items in manifest["records"].values()
            ):
                raise fail()
            uuid.UUID(manifest["project_id"])
            if len(manifest["records"].get("projects", [])) != 1:
                raise fail()
            blobs, staged, total = {}, {}, len(manifest_bytes) + 64
            for item in manifest["objects"]:
                sha, expected = item["sha256"], item["bytes"]
                key = item["object_key"]
                if (
                    not isinstance(sha, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", sha)
                    or not isinstance(key, str)
                    or key in blobs
                    or type(expected) is not int
                    or expected < 0
                ):
                    raise fail()
                if sha not in staged:
                    target = staging / sha
                    size, digest = 0, hashlib.sha256()
                    with bundle.open(f"blobs/{sha}") as source, target.open("wb") as dest:
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            total += len(chunk)
                            if total > expanded_limit():
                                raise fail("项目文件解压后超过大小上限。")
                            require_disk_space(staging, len(chunk))
                            digest.update(chunk)
                            dest.write(chunk)
                    if size != expected or digest.hexdigest() != sha:
                        raise fail("项目文件中的材料已损坏，请使用原备份。")
                    staged[sha] = (target, size)
                elif staged[sha][1] != expected:
                    raise fail()
                blobs[key] = staged[sha][0]
            return manifest, blobs, file_hash(path)
    except StatForgeError:
        raise
    except Exception as exc:
        raise fail() from exc


async def restore(db: AsyncSession, user: User, path: Path, *, as_copy: bool = False) -> Project:
    with tempfile.TemporaryDirectory(prefix="sf-package-open-") as directory:
        manifest, blobs, package_sha = await anyio.to_thread.run_sync(unpack, path, Path(directory))
        return await _restore_staged(db, user, manifest, blobs, package_sha, as_copy=as_copy)


async def _restore_staged(db, user, manifest, blobs, package_sha, *, as_copy=False):
    records = manifest["records"]
    source = next(
        (p for p in records.get("projects", []) if p.get("id") == manifest["project_id"]), None
    )
    if (
        source is None
        or not isinstance(source.get("title"), str)
        or not 1 <= len(source["title"]) <= 255
    ):
        raise fail()
    await credential_free(db, user, records)
    existing = await db.get(Project, uuid.UUID(source["id"]))
    if (
        not as_copy
        and existing is not None
        and existing.owner_id == user.id
        and not existing.is_deleted
    ):
        return existing
    from app.preview.write_fence import acquire_write_fence

    await acquire_write_fence(db, user.id)
    title = f"{source['title'][:251]}（副本）" if as_copy else source["title"]
    project = Project(
        title=title,
        description=source.get("description"),
        research_question=source.get("research_question"),
        owner_id=user.id,
    )
    db.add(project)
    await db.flush()
    mapping = {source["id"]: str(project.id)}
    created_keys = []
    object_references = {}
    committing = False
    try:
        from app.services import report_sources

        object_references.update(
            await report_sources.restore(db, records, blobs, project, user, created_keys)
        )
        from app.plotting.archives import bindings, mapped, references
        from app.plotting.worker import ARTIFACT_TYPES

        imported_references = references(records)
        for original_key, (extension, artifact) in bindings(records).items():
            package_key = mapped(original_key, imported_references)
            content = blobs.get(package_key)
            if (
                content is None
                or content.stat().st_size != artifact.bytes
                or file_hash(content) != artifact.sha256
            ):
                raise fail("项目文件中的图形已损坏，请使用原备份。")
            key = f"users\
/{user.id}/projects/{project.id}/imported-plots/{artifact.sha256}.{extension}"
            if key not in created_keys:
                storage_service.upload_file(key, str(content), ARTIFACT_TYPES[extension])
                created_keys.append(key)
            object_references[original_key] = key
            object_references[package_key] = key
        for item in sorted(
            records.get("datasets", []),
            key=lambda value: (
                not json.loads(value.get("preview_json") or "{}").get("raw_file", False)
            ),
        ):
            if item.get("upload_status") == "deleted":
                continue
            content = blobs[item["object_key"]]
            if file_hash(content) != item["sha256"]:
                raise fail()
            metadata = json.loads(item.get("preview_json") or "{}")
            extension = Path(item["original_filename"]).suffix.lower()
            if not metadata.get("raw_file") and extension not in {".csv", ".xlsx", ".parquet"}:
                raise fail()
            with tempfile.TemporaryDirectory(prefix="sf-project-open-") as directory:
                path = Path(directory) / f"source{extension}"
                shutil.copyfile(content, path)
                if metadata.get("raw_file"):
                    from app.services.project_sources import store

                    dataset = await store(
                        db,
                        project.id,
                        user.id,
                        str(path),
                        item["original_filename"],
                        metadata.get("source_path") or item["original_filename"],
                    )
                else:
                    dataset = await dataset_service.upload_dataset(
                        db,
                        project.id,
                        user.id,
                        str(path),
                        item["original_filename"],
                        sheet_name=metadata.get("sheet_name"),
                        csv_encoding=metadata.get("csv_encoding"),
                        source_path=metadata.get("source_path"),
                        allow_duplicate=True,
                    )
            created_keys.append(dataset.object_key)
            mapping[item["id"]] = str(dataset.id)
            multimodal = metadata.get("multimodal")
            if multimodal:
                source_key = multimodal.get("source_object_key")
                original = blobs.get(source_key)
                if original is None or file_hash(original) != multimodal.get("source_sha256"):
                    raise fail("多模态原始文件缺失或已损坏。")
                old_source_id = multimodal.get("source_dataset_id")
                new_source_id = mapping.get(old_source_id) if old_source_id else None
                if old_source_id and not new_source_id:
                    raise fail("多模态原文件记录缺失。")
                if new_source_id:
                    raw_source = await db.get(Dataset, uuid.UUID(new_source_id))
                    key = raw_source.object_key
                else:
                    key = storage_service.generate_object_key(
                        user.id, project.id, dataset.id, "source.pkl"
                    )
                    storage_service.upload_file(key, str(original), "application/octet-stream")
                    created_keys.append(key)
                current = json.loads(dataset.preview_json or "{}")
                current["multimodal"] = {**multimodal, "source_object_key": key}
                if new_source_id:
                    current["multimodal"]["source_dataset_id"] = new_source_id
                    current["source_dataset_id"] = new_source_id
                dataset.preview_json = json.dumps(current, ensure_ascii=False)
                for column in json.loads(dataset.schema_json or "[]"):
                    role = (
                        VariableRole.TARGET
                        if column["name"] == multimodal.get("label_column")
                        else VariableRole.IDENTIFIER
                        if column["name"] == multimodal.get("identifier_column")
                        else VariableRole.FEATURE
                    )
                    db.add(
                        Variable(
                            dataset_id=dataset.id,
                            name=column["name"],
                            data_type=column["inferred_type"],
                            role=role,
                        )
                    )
        for item in records.get("datasets", []):
            metadata = json.loads(item.get("preview_json") or "{}")
            if item["id"] in mapping and metadata.get("source_dataset_id"):
                source_id = mapping.get(metadata["source_dataset_id"])
                if not source_id:
                    raise fail("原文件来源记录缺失。")
                restored_source = await db.get(Dataset, uuid.UUID(mapping[item["id"]]))
                current_source = json.loads(restored_source.preview_json or "{}")
                current_source["source_dataset_id"] = source_id
                recipe = metadata.get("prepared_source")
                if isinstance(recipe, dict):
                    raw = await db.get(Dataset, uuid.UUID(source_id))
                    expected_format = "parquet" if raw.file_format == "json" else raw.file_format
                    if (
                        raw.raw_file
                        and raw.sha256 == recipe.get("sha256")
                        and restored_source.file_format == expected_format
                    ):
                        current_source["prepared_source"] = {
                            "sha256": raw.sha256,
                            "sheet_name": restored_source.sheet_name,
                            "csv_encoding": restored_source.csv_encoding or "auto",
                        }
                        if raw.file_format == "json":
                            current_source["verify_prepared_json"] = True
                restored_source.preview_json = json.dumps(current_source, ensure_ascii=False)
            draft = metadata.get("preparation_draft")
            if item["id"] in mapping and draft:
                restored = await db.get(Dataset, uuid.UUID(mapping[item["id"]]))
                current = json.loads(restored.preview_json)
                request = draft.get("request")
                if request and request.get("right_dataset_id"):
                    request["right_dataset_id"] = mapping.get(request["right_dataset_id"])
                    if not request["right_dataset_id"]:
                        raise fail("整理草稿的关联来源缺失。")
                if request:
                    from app.schemas.preparation import Preparation

                    request = Preparation.model_validate(request).model_dump(mode="json")
                current["preparation_draft"] = {"revision": 1, "request": request}
                restored.preview_json = json.dumps(current, ensure_ascii=False)
            file_version = metadata.get("file_version")
            if item["id"] in mapping and file_version:
                restored = await db.get(Dataset, uuid.UUID(mapping[item["id"]]))
                current = json.loads(restored.preview_json)
                originals = {value["id"]: value for value in records.get("datasets", [])}
                parent = originals.get(file_version.get("parent_id"))
                root = originals.get(file_version.get("root_id"))
                parent_version = (
                    json.loads(parent.get("preview_json") or "{}").get("file_version", {})
                    if parent
                    else {}
                )
                if (
                    not parent
                    or not root
                    or parent["id"] == item["id"]
                    or root["id"] == item["id"]
                    or file_version.get("parent_sha256") != parent["sha256"]
                    or parent_version.get("root_id", parent["id"]) != root["id"]
                    or type(file_version.get("number")) is not int
                    or file_version["number"] <= parent_version.get("number", 1)
                ):
                    raise fail("文件版本的来源关系无效。")
                file_version["parent_id"] = mapping.get(file_version.get("parent_id"))
                file_version["root_id"] = mapping.get(file_version.get("root_id"), str(restored.id))
                current["file_version"] = file_version
                restored.preview_json = json.dumps(current, ensure_ascii=False)
            preparation = metadata.get("preparation")
            if item["id"] not in mapping or not preparation:
                continue
            restored = await db.get(Dataset, uuid.UUID(mapping[item["id"]]))
            current = json.loads(restored.preview_json)
            preparation["parent_id"] = mapping.get(preparation.get("parent_id"))
            preparation["root_id"] = mapping.get(preparation.get("root_id"))
            for step in preparation.get("steps", []):
                recipe = step.get("recipe", {})
                if recipe.get("right_dataset_id"):
                    recipe["right_dataset_id"] = mapping.get(recipe["right_dataset_id"])
            current["preparation"] = preparation
            restored.preview_json = json.dumps(current, ensure_ascii=False)
        for item in records.get("materials", []):
            content = blobs[item["object_key"]]
            if file_hash(content) != item["sha256"]:
                raise fail()
            material = await material_service.upload_path(
                db,
                project.id,
                user.id,
                item["filename"],
                content,
                source_path=json.loads(item.get("metadata_json") or "{}").get("source_path"),
            )
            mapping[item["id"]] = material["id"]
            row = await db.get(Material, uuid.UUID(material["id"]))
            created_keys.append(row.object_key)
        # Recreate owned source locations against restored, re-extracted material bytes.
        restored_citations = {}
        from app.services.material_updates import restore_lineage

        await restore_lineage(db, records.get("materials", []), mapping, user.id, project.id)
        for item in records.get("material_citations", []):
            material_id = mapping.get(item.get("material_id"))
            if material_id is None:
                continue
            material = await material_service.load(db, uuid.UUID(material_id), user.id, project.id)
            citation = await material_service.save_citation(
                db,
                material.id,
                user.id,
                CitationRequest(
                    material_sha256=material.sha256,
                    material_version=material.version,
                    reviewed=item.get("reviewed", False),
                    locator=json.loads(item["locator_json"]),
                ),
            )
            mapping[item["id"]] = citation["id"]
            restored_citations[item["id"]] = citation
        for item in records.get("workspace_preview_records", []):
            payload = json.loads(item["payload_json"])
            if payload.get("kind") != "coding":
                continue
            from app.schemas.coding import CodingDocument
            from app.services.text_coding import segment_from_citation

            document = CodingDocument.model_validate(payload)
            for index, segment in enumerate(document.segments):
                citation = restored_citations.get(str(segment.citation_id))
                if citation is None or citation["locator"] != segment.locator.model_dump(
                    mode="json"
                ):
                    material_id = mapping.get(str(segment.material_id))
                    if material_id is None:
                        raise fail("编码来源无法匹配，请保留原项目并核对材料。")
                    material = await material_service.load(
                        db, uuid.UUID(material_id), user.id, project.id
                    )
                    if material.sha256 != segment.material_sha256:
                        raise fail("编码材料内容不一致，请保留原项目。")
                    citation = await material_service.save_citation(
                        db,
                        material.id,
                        user.id,
                        CitationRequest(
                            material_sha256=material.sha256,
                            material_version=material.version,
                            reviewed=True,
                            locator=segment.locator,
                        ),
                    )
                document.segments[index] = segment_from_citation(
                    citation, segment.theme_ids, segment.memo, segment.id
                )
            document.undo_stack = []  # Old revision numbers belong to the original record.
            saved = await save_record(
                db,
                user,
                SaveRecord(project_id=project.id, payload=document),
                commit=False,
                coding_write=True,
            )
            mapping[item["id"]] = saved["id"]
        # Restore editable analysis drafts. Historical Runs stay immutable snapshots;
        # imported model binaries are never registered as executable ML artifacts.
        restored_tasks = {}
        for item in records.get("workspace_preview_records", []):
            payload = json.loads(item["payload_json"])
            if payload.get("kind") != "research_task":
                continue
            payload.update(
                dataset_id=mapping.get(payload.get("dataset_id")),
                analysis_ids=[],
                figure_ids=[],
                adopted_id=None,
                archived_analysis_ids=[],
                archived_figure_ids=[],
            )
            saved = await save_record(
                db, user, SaveRecord(project_id=project.id, payload=payload), commit=False
            )
            mapping[item["id"]] = saved["id"]
            restored_tasks[item["id"]] = saved
        for item in records.get("workspace_preview_records", []):
            payload = json.loads(item["payload_json"])
            if payload.get("kind") == "research_input":
                payload["context_key"] = mapping.get(
                    payload.get("context_key"), payload.get("context_key", "project")
                )
                payload["workspace_id"] = None
                payload["session_id"] = None
                payload["dataset_id"] = mapping.get(payload.get("dataset_id"))
                payload["columns"] = {
                    mapping[key]: value
                    for key, value in payload.get("columns", {}).items()
                    if key in mapping
                }
                await save_record(
                    db, user, SaveRecord(project_id=project.id, payload=payload), commit=False
                )
                continue
            if payload.get("kind") == "data_workspace":
                if payload.get("dataset_id") in mapping:
                    payload["dataset_id"] = mapping[payload["dataset_id"]]
                    await save_record(
                        db, user, SaveRecord(project_id=project.id, payload=payload), commit=False
                    )
                continue
            if payload.get("kind") not in {"analysis", "plot"}:
                continue
            scope = payload.get("scope", {})
            dataset_id = mapping.get(scope.get("dataset_id"))
            if not dataset_id:
                continue
            payload["scope"] = {
                "project_id": str(project.id),
                "dataset_id": dataset_id,
                "dataset_sha256": scope["dataset_sha256"],
                "schema_sha256": scope.get("schema_sha256"),
                "columns": scope.get("columns", []),
            }
            saved = await save_record(
                db, user, SaveRecord(project_id=project.id, payload=payload), commit=False
            )
            mapping[item["id"]] = saved["id"]
        for item in records.get("workspace_preview_records", []):
            payload = json.loads(item["payload_json"])
            if payload.get("kind") != "workspace":
                continue

            def ref(value):
                return (
                    {**value, "id": mapping[value["id"]]}
                    if value and value["id"] in mapping
                    else None
                )

            payload["tabs"] = [value for old in payload.get("tabs", []) if (value := ref(old))]
            payload["selected"] = ref(payload.get("selected"))
            payload["inspector"] = "closed"
            await save_record(
                db, user, SaveRecord(project_id=project.id, payload=payload), commit=False
            )
        # Preserve all historical data and original identities without restoring
        # leases, provider permissions, executable models or completed native claims.
        project.snapshot_json = encoded(
            {
                "imported_project": {
                    "package_sha256": package_sha,
                    "saved_at": manifest["saved_at"],
                    "source_project_id": source["id"],
                    "references": mapping,
                    "object_references": object_references,
                    "records": records,
                }
            }
        ).decode()
        for item in records.get("workspace_preview_records", []):
            if item["id"] not in restored_tasks:
                continue
            payload = json.loads(item["payload_json"])
            original_analyses = payload.get("analysis_ids", [])
            payload.update(
                dataset_id=mapping.get(payload.get("dataset_id")),
                analysis_ids=[mapping[value] for value in original_analyses if value in mapping],
                adopted_id=mapping.get(payload.get("adopted_id")),
                figure_ids=[],
                archived_analysis_ids=list(
                    dict.fromkeys([*payload.get("archived_analysis_ids", []), *original_analyses])
                ),
                archived_figure_ids=list(
                    dict.fromkeys(
                        [*payload.get("archived_figure_ids", []), *payload.get("figure_ids", [])]
                    )
                ),
            )
            prior = restored_tasks[item["id"]]
            await save_record(
                db,
                user,
                SaveRecord(
                    project_id=project.id, expected_revision=prior["revision"], payload=payload
                ),
                uuid.UUID(prior["id"]),
                commit=False,
            )
        for item in records.get("workspace_preview_records", []):
            payload = json.loads(item["payload_json"])
            if payload.get("kind") != "report":
                continue
            from app.schemas.workspace import ReportDocument

            report = ReportDocument.model_validate(payload)
            allowed = {block.source.model_dump_json() for block in report.blocks if block.source}
            saved = await save_record(
                db,
                user,
                SaveRecord(project_id=project.id, payload=report),
                commit=False,
                retained_sources=allowed,
            )
            mapping[item["id"]] = saved["id"]
        project.snapshot_json = encoded(
            {
                "imported_project": {
                    "package_sha256": package_sha,
                    "saved_at": manifest["saved_at"],
                    "source_project_id": source["id"],
                    "references": mapping,
                    "object_references": object_references,
                    "records": records,
                }
            }
        ).decode()
        await db.flush()
        # Complete the transaction before returning a newly opened identity;
        # the next page may request its plots immediately after this response.
        committing = True
        await db.commit()
        await db.refresh(project)
        return project
    except Exception:
        if not committing:
            for key in created_keys:
                storage_service.delete_object(key)
        raise
