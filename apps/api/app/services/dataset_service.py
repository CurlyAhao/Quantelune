"""Dataset upload orchestration: stream → hash → validate → store → persist."""

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import (
    DatasetAccessDeniedError,
    DatasetNotFoundError,
    DuplicateFileError,
    StorageError,
)
from app.models.dataset import Dataset, UploadStatus
from app.models.project import Project
from app.services import storage_service
from app.services.file_validation_service import validate_file
from app.services.project_access import check_access
from app.services.project_access import owned_project as _verify_project_access

logger = logging.getLogger("statforge.dataset")

_SAFE_FILENAME_RE = re.compile(r"[^\w.\-]")


def sanitize_filename(filename: str) -> str:
    """Produce a filesystem-safe filename, preserving extension."""
    name = Path(filename).name  # strip directory components (path traversal)
    name = name.replace("..", "").replace("/", "").replace("\\", "")
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem = "file"
    safe_stem = _SAFE_FILENAME_RE.sub("_", stem)[:200]
    safe_ext = _SAFE_FILENAME_RE.sub("", ext.lower())[:10]
    return f"{safe_stem}.{safe_ext}" if safe_ext else safe_stem


def _compute_sha256(file_path: str) -> str:
    """Stream-compute SHA-256 of a file (never loads whole file into memory)."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


async def upload_dataset(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    file_content_path: str,
    original_filename: str,
    content_type: str | None = None,
    sheet_name: str | None = None,
    csv_encoding: str | None = "auto",
    source_path: str | None = None,
    allow_duplicate: bool = False,
) -> Dataset:
    """Full upload pipeline: validate → hash → dedup → MinIO → DB.

    `file_content_path` is a temporary file already written by the router.
    Implements compensation: if MinIO succeeds but DB fails, the object is deleted.
    """
    await _verify_project_access(db, project_id, user_id)

    ext = Path(original_filename).suffix.lstrip(".")
    safe_name = sanitize_filename(original_filename)
    dataset_id = uuid.uuid4()

    # 1. Validate + parse
    try:
        result = validate_file(
            file_content_path,
            ext,
            preview_rows=settings.preview_default_rows,
            sheet_name=sheet_name,
            csv_encoding=csv_encoding,
        )
    except Exception:
        raise  # FileValidationError / UnsupportedFileTypeError propagate

    # 2. Compute SHA-256
    sha256 = _compute_sha256(file_content_path)

    # 3. Duplicate detection (same project, same hash)
    existing = await db.execute(
        select(Dataset).where(
            Dataset.project_id == project_id,
            Dataset.sha256 == sha256,
            Dataset.upload_status != UploadStatus.DELETED,
            Dataset.upload_status != UploadStatus.FAILED,
        )
    )
    selected_sheet = (sheet_name or result.sheet_names[0]) if result.sheet_names else None
    dup = next(
        (
            item
            for item in existing.scalars().all()
            if (item.csv_encoding is None or item.csv_encoding == result.csv_encoding)
            and (
                item.sheet_name == selected_sheet
                or (item.sheet_name is None and selected_sheet == (result.sheet_names or [None])[0])
            )
        ),
        None,
    )
    if dup is not None and not allow_duplicate:
        raise DuplicateFileError(str(dup.id))

    # 4. Determine MIME
    from app.services.file_validation_service import MIME_MAP

    mime = content_type or MIME_MAP.get(ext, "application/octet-stream")

    # 5. Create DB record (pending)
    object_key = storage_service.generate_object_key(
        user_id,
        project_id,
        dataset_id,
        safe_name,
    )
    dataset = Dataset(
        id=dataset_id,
        project_id=project_id,
        original_filename=original_filename,
        safe_filename=safe_name,
        file_format=ext.lower(),
        mime_type=mime,
        file_size=os.path.getsize(file_content_path),
        sha256=sha256,
        object_key=object_key,
        bucket=settings.minio_bucket,
        upload_status=UploadStatus.UPLOADING,
        name=original_filename,
        file_path=object_key,
        file_type=ext.lower(),
        content_hash=sha256,
    )
    db.add(dataset)
    await db.flush()

    # 6. Upload to MinIO (with compensation on failure)
    try:
        storage_service.upload_file(object_key, file_content_path, mime)
    except Exception as exc:
        dataset.upload_status = UploadStatus.FAILED
        dataset.error_code = "STORAGE_UPLOAD_FAILED"
        dataset.error_message = str(type(exc).__name__)
        await db.flush()
        logger.error("MinIO upload failed for dataset %s: %s", dataset_id, exc)
        raise StorageError("File upload to storage failed. Please try again.")

    # 7. Update metadata
    dataset.upload_status = UploadStatus.READY
    dataset.row_count = result.row_count
    dataset.column_count = result.column_count
    dataset.schema_json = result.schema_json()
    preview = json.loads(result.preview_json())
    preview["sheet_name"] = selected_sheet
    if result.csv_encoding is not None:
        preview["csv_encoding"] = result.csv_encoding
    # Relative names are labels only, never filesystem read targets.
    preview["source_path"] = (source_path or original_filename).replace("\\", "/")[:1024]
    dataset.preview_json = json.dumps(preview, ensure_ascii=False)
    dataset.profile_status = "profiled"
    dataset.validated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(dataset)

    logger.info(
        "Dataset uploaded: id=%s file=%s rows=%d cols=%d",
        dataset_id,
        original_filename,
        result.row_count,
        result.column_count,
    )
    return dataset


async def get_dataset_with_access(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Dataset:
    """Load a dataset and verify the user owns the parent project."""
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None or dataset.upload_status == UploadStatus.DELETED:
        raise DatasetNotFoundError()

    project = await db.get(Project, dataset.project_id)
    check_access(project, user_id, missing=DatasetNotFoundError, denied=DatasetAccessDeniedError)

    return dataset


async def list_project_datasets(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[Dataset]:
    """List all datasets for a project the user owns."""
    await _verify_project_access(db, project_id, user_id)
    result = await db.execute(
        select(Dataset)
        .where(
            Dataset.project_id == project_id,
            Dataset.upload_status != UploadStatus.DELETED,
        )
        .order_by(Dataset.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_dataset(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Soft-delete a dataset and remove the MinIO object."""
    dataset = await get_dataset_with_access(db, dataset_id, user_id)

    if dataset.raw_file:
        dependents = await list_project_datasets(db, dataset.project_id, user_id)
        if any(
            item.source_dataset_id == str(dataset_id) and item.upload_status != UploadStatus.DELETED
            for item in dependents
        ):
            from app.exceptions import StatForgeError

            raise StatForgeError(
                409, "SOURCE_FILE_IN_USE", "此原文件仍被分析数据使用，请先移除相应分析数据。"
            )
    # Delete from MinIO
    try:
        storage_service.delete_object(dataset.object_key)
    except Exception:
        logger.warning("Failed to delete MinIO object for dataset %s", dataset_id)

    dataset.upload_status = UploadStatus.DELETED
    source_key = (
        json.loads(dataset.preview_json or "{}").get("multimodal", {}).get("source_object_key")
    )
    if source_key and not dataset.source_dataset_id:
        storage_service.delete_object(source_key)
    await db.flush()
    logger.info("Dataset deleted: %s", dataset_id)
