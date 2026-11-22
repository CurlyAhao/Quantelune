"""Inspect and map user-selected precomputed multimodal feature bundles."""

import json
import logging
import os
import re
import tempfile
import traceback
import uuid
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.dataset import Dataset
from app.models.user import User
from app.routers.datasets import _stream_to_temp
from app.schemas.dataset import DatasetResponse
from app.services import dataset_service, storage_service
from app.services import multimodal_import as mm

router = APIRouter(tags=["multimodal features"])
logger = logging.getLogger("statforge.multimodal")


@router.post("/api/v1/projects/{project_id}/imports/pkl/inspect")
async def inspect_features(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await dataset_service._verify_project_access(db, project_id, user.id)
    path = None
    try:
        path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
        return await anyio.to_thread.run_sync(mm.inspect, path)
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


@router.post(
    "/api/v1/projects/{project_id}/imports/pkl", response_model=DatasetResponse, status_code=201
)
async def import_features(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    mapping: str = Form(...),
    source_path: str | None = Form(None),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> Dataset:
    owner_id = user.id
    await dataset_service._verify_project_access(db, project_id, owner_id)
    try:
        spec = mm.Mapping.model_validate_json(mapping)
    except ValidationError:
        raise mm.invalid("字段映射不完整，请重新确认标签和模态。") from None
    path = None
    dataset = None
    source_key = None
    normalized_key = None
    phase = "read_source"
    try:
        path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
        with tempfile.TemporaryDirectory(prefix="sf-features-") as directory:
            normalized = str(Path(directory) / "features.parquet")
            phase = "convert_features"
            metadata = await anyio.to_thread.run_sync(mm.convert, path, spec, normalized)
            original = Path(file.filename or "features.pkl").name
            node = re.sub(r"[^\w.-]", "_", "_".join(spec.node) or "features")[:80]
            name = f"{Path(original).stem[:150]}.{node}.parquet"
            phase = "save_table"
            dataset = await dataset_service.upload_dataset(
                db,
                project_id,
                owner_id,
                normalized,
                name,
                source_path=f"{source_path or original}/{node}.parquet",
                allow_duplicate=True,
            )
            normalized_key = dataset.object_key
            phase = "save_original"
            source_key = storage_service.generate_object_key(
                owner_id, project_id, dataset.id, "source.pkl"
            )
            storage_service.upload_file(source_key, path, "application/octet-stream")
            metadata.update(
                source_filename=original,
                source_object_key=source_key,
                source_bytes=os.path.getsize(path),
            )
            preview = json.loads(dataset.preview_json or "{}")
            preview["multimodal"] = metadata
            dataset.preview_json = json.dumps(preview, ensure_ascii=False)
            phase = "save_fields"
            # Persist explicit feature roles for other entry points too.
            from app.models.dataset import Variable, VariableRole

            for column in json.loads(dataset.schema_json or "[]"):
                role = (
                    VariableRole.TARGET
                    if column["name"] == "label"
                    else VariableRole.IDENTIFIER
                    if column["name"] == "sample_id"
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
            await db.flush()
            await db.refresh(dataset)
            return dataset
    except Exception as exc:
        frames = ";".join(
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)[-8:]
        )
        logger.error(
            "PKL import failed phase=%s kind=%s frames=%s", phase, type(exc).__name__, frames
        )
        for key in [source_key, normalized_key]:
            if key:
                try:
                    storage_service.delete_object(key)
                except OSError:
                    logger.error("PKL import cleanup unavailable phase=%s", phase)
        from app.exceptions import StatForgeError

        if isinstance(exc, StatForgeError):
            raise
        raise StatForgeError(
            500,
            "MULTIMODAL_IMPORT_FAILED",
            f"PKL 导入未完成（{phase} / {type(exc).__name__}），\
原文件未修改，可重试或调整字段设置。",
        ) from None
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


@router.get("/api/v1/datasets/{dataset_id}/multimodal/source", response_model=None)
async def download_source(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    metadata = json.loads(dataset.preview_json or "{}").get("multimodal", {})
    key = metadata.get("source_object_key")
    if not key or not storage_service.object_exists(key):
        raise mm.invalid("此数据集没有可下载的原始 PKL。")
    if settings.storage_backend == "local":
        return FileResponse(
            storage_service.get_local_object_path(key),
            media_type="application/octet-stream",
            filename=metadata.get("source_filename", "source.pkl"),
        )
    return RedirectResponse(storage_service.get_presigned_download_url(key))
