import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.routers.datasets import _stream_to_temp
from app.services import source_updates as service

router = APIRouter(prefix="/api/v1/datasets", tags=["source-updates"])
Encoding = Literal["auto", "utf-8", "gb18030", "big5", "cp1252", "latin-1"]


@router.get("/{dataset_id}/source-versions")
async def versions(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.versions(db, dataset_id, user)


@router.post("/{dataset_id}/source-check")
async def check(
    dataset_id: uuid.UUID,
    file: UploadFile = File(...),
    source_sha256: str = Form(..., pattern=r"^[a-f0-9]{64}$"),
    sheet_name: str | None = Form(None),
    csv_encoding: Encoding = Form("auto"),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await service.source(db, dataset_id, user)
    path = None
    try:
        path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
        return await service.compare(
            db, dataset_id, user, path, file.filename or "", source_sha256, sheet_name, csv_encoding
        )
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


@router.post("/{dataset_id}/source-update")
async def update(
    dataset_id: uuid.UUID,
    file: UploadFile = File(...),
    source_sha256: str = Form(..., pattern=r"^[a-f0-9]{64}$"),
    comparison_sha256: str = Form(..., pattern=r"^[a-f0-9]{64}$"),
    mode: Literal["version", "separate", "restore"] = Form(...),
    sheet_name: str | None = Form(None),
    csv_encoding: Encoding = Form("auto"),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await service.source(db, dataset_id, user)
    path = None
    try:
        path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
        return await service.apply(
            db,
            dataset_id,
            user,
            path,
            file.filename or "",
            source_sha256,
            sheet_name,
            csv_encoding,
            comparison_sha256,
            mode,
        )
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
