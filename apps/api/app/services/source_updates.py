"""Compare explicitly selected files and keep immutable source versions."""

import hashlib
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.project import Project
from app.preview.context import canonical
from app.preview.write_fence import acquire_write_fence
from app.schemas.dataset import DatasetResponse
from app.services import dataset_service, storage_service
from app.services.file_validation_service import validate_file


def failure(message="文件已有变化，请重新核对。", status=409):
    return StatForgeError(status, "SOURCE_CHECK_CHANGED", message)


def metadata(dataset):
    return json.loads(dataset.preview_json or "{}")


def version(dataset):
    return metadata(dataset).get("file_version", {})


def public(dataset):
    return DatasetResponse.model_validate(dataset).model_dump(mode="json")


async def source(db, identity, user):
    selected = await dataset_service.get_dataset_with_access(db, identity, user.id)
    await dataset_service._verify_project_access(db, selected.project_id, user.id)
    root = metadata(selected).get("preparation", {}).get("root_id")
    try:
        base = (
            await dataset_service.get_dataset_with_access(db, uuid.UUID(root), user.id)
            if root
            else selected
        )
    except ValueError:
        raise failure("原始来源无法识别，请重新选择数据。") from None
    if base.project_id != selected.project_id or base.upload_status != UploadStatus.READY:
        raise failure("原始来源不可用，请重新选择数据。")
    return selected, base


async def versions(db, identity, user):
    selected, base = await source(db, identity, user)
    root = version(base).get("root_id", str(base.id))
    datasets = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == base.project_id, Dataset.upload_status == UploadStatus.READY
            )
        )
    ).all()
    items = [
        item
        for item in datasets
        if not item.preparation_steps and version(item).get("root_id", str(item.id)) == root
    ]
    items.sort(key=lambda item: (version(item).get("number", 1), str(item.id)))
    return {
        "source": public(base),
        "selected_id": str(selected.id),
        "selected_is_prepared": selected.id != base.id,
        "stored_available": storage_service.object_exists(base.object_key),
        "versions": [public(item) for item in items],
    }


def stored_matches(base):
    try:
        with tempfile.TemporaryDirectory(prefix="sf-source-check-") as directory:
            path = Path(directory) / "source"
            storage_service.download_to_path(base.object_key, str(path))
            return dataset_service._compute_sha256(str(path)) == base.sha256
    except Exception:
        return False


def inspect(path, filename, base, sheet, encoding):
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension != base.file_format:
        raise failure("格式不同，请通过“导入数据”作为独立文件打开。", 422)
    result = validate_file(
        path, extension, preview_rows=20, sheet_name=sheet, csv_encoding=encoding
    )
    selected_sheet = (sheet or result.sheet_names[0]) if result.sheet_names else None
    old_sheet = base.sheet_name or ((result.sheet_names or [None])[0])
    incoming_sha = dataset_service._compute_sha256(path)
    old_encoding = base.csv_encoding
    if extension == "csv" and old_encoding is None and incoming_sha == base.sha256:
        from app.services.csv_reader import _encoding

        old_encoding = _encoding(path)
    same_view = selected_sheet == old_sheet and (old_encoding == result.csv_encoding)
    unchanged = incoming_sha == base.sha256 and same_view
    before_columns = json.loads(base.schema_json or "[]")
    before = {column["name"]: column["polars_type"] for column in before_columns}
    after = {column["name"]: column["polars_type"] for column in result.columns}
    changes = [
        {"column": name, "before": before.get(name), "after": after.get(name)}
        for name in dict.fromkeys([*before, *after])
        if before.get(name) != after.get(name)
    ]
    token = hashlib.sha256(
        canonical(
            {
                "source_id": str(base.id),
                "source_sha256": base.sha256,
                "file_sha256": incoming_sha,
                "sheet": selected_sheet,
                "encoding": result.csv_encoding,
            }
        ).encode()
    ).hexdigest()
    return {
        "source": public(base),
        "same_content": unchanged,
        "bytes_identical": incoming_sha == base.sha256,
        "comparison_sha256": token,
        "stored_available": stored_matches(base),
        "before": {
            "rows": metadata(base).get("rows", [])[:20],
            "columns": before_columns,
            "row_count": base.row_count,
            "column_count": base.column_count,
        },
        "after": {
            "filename": Path(filename.replace("\\", "/")).name,
            "sha256": incoming_sha,
            "sheet_name": selected_sheet,
            "csv_encoding": result.csv_encoding,
            "row_count": result.row_count,
            "column_count": result.column_count,
            "columns": result.columns,
            "rows": result.preview_rows,
        },
        "column_changes": changes,
    }


async def compare(db, identity, user, path, filename, source_sha, sheet, encoding):
    selected, base = await source(db, identity, user)
    if selected.sha256 != source_sha:
        raise failure()
    result = await anyio.to_thread.run_sync(inspect, path, filename, base, sheet, encoding)
    return {**result, "selected_is_prepared": selected.id != base.id}


async def apply(db, identity, user, path, filename, source_sha, sheet, encoding, token, mode):
    comparison = await compare(db, identity, user, path, filename, source_sha, sheet, encoding)
    if comparison["comparison_sha256"] != token:
        raise failure("选中的文件或读取方式已变化，请重新核对。")
    await acquire_write_fence(db, user.id)
    await db.refresh(user)
    selected, base = await source(db, identity, user)
    await db.refresh(selected)
    await db.refresh(base)
    project = await db.get(Project, base.project_id, populate_existing=True)
    if not user.is_active or project is None or project.is_deleted:
        raise failure("当前项目不可用。", 404)
    await dataset_service._verify_project_access(db, base.project_id, user.id)
    if selected.sha256 != source_sha or base.sha256 != comparison["source"]["sha256"]:
        raise failure()
    checked_at = datetime.now(UTC).isoformat()
    if mode == "restore":
        if not comparison["same_content"]:
            raise failure("内容与原文件不一致，请选择原版本，或作为新版导入。")
        if not comparison["stored_available"]:
            key = storage_service.generate_object_key(
                user.id, base.project_id, base.id, base.safe_filename
            )
            storage_service.upload_file(key, path, base.mime_type)
            # A damaged prior object is retained; only the exact original hash is restored.
            base.object_key = key
            base.file_path = key
        value = metadata(base)
        value["source_check"] = {
            "verified_at": checked_at,
            "sha256": base.sha256,
            "filename": comparison["after"]["filename"],
        }
        base.preview_json = json.dumps(value, ensure_ascii=False)
        await db.flush()
        await db.refresh(base)
        await db.commit()
        return {"dataset": public(base), "outcome": "verified"}
    if comparison["same_content"]:
        raise failure("内容一致，无需创建新版本。")
    root = version(base).get("root_id", str(base.id))
    candidates = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == base.project_id, Dataset.upload_status == UploadStatus.READY
            )
        )
    ).all()
    matching = [
        item
        for item in candidates
        if item.sha256 == comparison["after"]["sha256"]
        and item.sheet_name == comparison["after"]["sheet_name"]
        and item.csv_encoding == comparison["after"]["csv_encoding"]
    ]
    for item in matching:
        prior = version(item)
        if mode == "version" and prior.get("parent_id") == str(base.id):
            return {"dataset": public(item), "outcome": "existing"}
        if mode == "separate" and not prior and not item.preparation_steps:
            return {"dataset": public(item), "outcome": "existing"}
    saved = await dataset_service.upload_dataset(
        db,
        base.project_id,
        user.id,
        path,
        comparison["after"]["filename"],
        sheet_name=comparison["after"]["sheet_name"],
        csv_encoding=comparison["after"]["csv_encoding"],
        allow_duplicate=True,
    )
    value = metadata(saved)
    if mode == "version":
        number = (
            max(
                [
                    version(item).get("number", 1)
                    for item in candidates
                    if version(item).get("root_id", str(item.id)) == root
                ]
                or [1]
            )
            + 1
        )
        value["file_version"] = {
            "root_id": root,
            "parent_id": str(base.id),
            "number": number,
            "parent_sha256": base.sha256,
            "checked_at": checked_at,
        }
    saved.preview_json = json.dumps(value, ensure_ascii=False)
    await db.flush()
    await db.refresh(saved)
    await db.commit()
    return {"dataset": public(saved), "outcome": "created"}
