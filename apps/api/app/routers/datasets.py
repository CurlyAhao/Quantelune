"""Dataset upload, list, detail, preview, download, and delete routes."""

import json
import logging
import os
import tempfile
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import FileTooLargeError
from app.models.dataset import Dataset
from app.models.user import User
from app.schemas.dataset import DatasetPreviewResponse, DatasetResponse
from app.services import audit_service, dataset_service, storage_service

logger = logging.getLogger("statforge.datasets")
router = APIRouter(tags=["datasets"])

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


@router.post("/api/v1/datasets/excel-sheets")
async def excel_sheets(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Inspect worksheet names without creating a dataset."""
    from app.services.file_validation_service import excel_sheet_names

    path = None
    try:
        path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
        return {"sheet_names": excel_sheet_names(path)}
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


async def _stream_to_temp(upload: UploadFile, max_bytes: int | None = None) -> tuple[str, int]:
    """Stream an UploadFile to a temp file in chunks. Returns (path, size).

    Never loads the whole file into memory. Enforces size limit during streaming.
    """
    suffix = os.path.splitext(upload.filename or "file")[1] or ".tmp"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sf_upload_")
    total = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await upload.read(_CHUNK_SIZE):
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise FileTooLargeError(max_bytes // (1024 * 1024))
                f.write(chunk)
    except FileTooLargeError:
        os.unlink(tmp_path)
        raise
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return tmp_path, total


@router.post(
    "/api/v1/projects/{project_id}/datasets/upload",
    response_model=DatasetResponse,
    status_code=201,
)
async def upload_dataset(
    project_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    sheet_name: str | None = Form(None),
    csv_encoding: Literal["auto", "utf-8", "gb18030", "big5", "cp1252", "latin-1"] = Form("auto"),
    source_path: str | None = Form(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> Dataset:
    """Upload a CSV, Excel, or Parquet file to a project."""
    filename = file.filename or "unnamed"
    logger.info("Upload started: file=%s user=%s project=%s", filename, current_user.id, project_id)

    tmp_path: str | None = None
    try:
        tmp_path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
        dataset = await dataset_service.upload_dataset(
            db=db,
            project_id=project_id,
            user_id=current_user.id,
            file_content_path=tmp_path,
            original_filename=filename,
            content_type=file.content_type,
            sheet_name=sheet_name,
            csv_encoding=csv_encoding,
            source_path=source_path,
        )
        await audit_service.record(
            db,
            event_type="dataset.upload",
            entity_type="dataset",
            entity_id=dataset.id,
            actor=current_user,
            project_id=project_id,
            after={
                "filename": dataset.original_filename,
                "file_size": dataset.file_size,
                "format": dataset.file_format,
            },
        )
        return dataset
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get(
    "/api/v1/projects/{project_id}/datasets",
    response_model=list[DatasetResponse],
)
async def list_datasets(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[Dataset]:
    """List all datasets in a project."""
    return await dataset_service.list_project_datasets(db, project_id, current_user.id)


@router.get(
    "/api/v1/datasets/{dataset_id}",
    response_model=DatasetResponse,
)
async def get_dataset(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> Dataset:
    """Get dataset metadata."""
    return await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)


@router.get(
    "/api/v1/datasets/{dataset_id}/preview",
    response_model=DatasetPreviewResponse,
)
async def preview_dataset(
    dataset_id: uuid.UUID,
    n: int = Query(default=settings.preview_default_rows, ge=0, le=settings.preview_max_rows),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get dataset preview: schema + first N rows."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)

    schema = json.loads(dataset.schema_json) if dataset.schema_json else []
    preview_data = (
        json.loads(dataset.preview_json) if dataset.preview_json else {"rows": [], "total_rows": 0}
    )

    rows = preview_data.get("rows", [])[:n]
    return {
        "dataset_id": dataset.id,
        "original_filename": dataset.original_filename,
        "file_format": dataset.file_format,
        "row_count": dataset.row_count,
        "column_count": dataset.column_count,
        "upload_status": dataset.upload_status.value,
        "columns": schema,
        "rows": rows,
        "showing_rows": len(rows),
        "total_rows": dataset.row_count or 0,
    }


@router.get("/api/v1/datasets/{dataset_id}/download", response_model=None)
async def download_dataset(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> FileResponse | RedirectResponse:
    """Stream a local object or redirect to a remote presigned URL."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)

    if not storage_service.object_exists(dataset.object_key):
        from app.exceptions import DatasetNotFoundError

        raise DatasetNotFoundError()

    if settings.storage_backend == "local":
        return FileResponse(
            path=storage_service.get_local_object_path(dataset.object_key),
            media_type=dataset.mime_type,
            filename=dataset.original_filename,
        )

    url = storage_service.get_presigned_download_url(dataset.object_key)
    return RedirectResponse(url=url, status_code=302)


@router.delete("/api/v1/datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a dataset (soft-delete + remove MinIO object)."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    project_id = dataset.project_id
    filename = dataset.original_filename
    await dataset_service.delete_dataset(db, dataset_id, current_user.id)
    await audit_service.record(
        db,
        event_type="dataset.delete",
        entity_type="dataset",
        entity_id=dataset_id,
        actor=current_user,
        project_id=project_id,
        before={"filename": filename},
    )
