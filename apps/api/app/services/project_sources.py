"""Preserve selected source files before choosing how to analyse them."""

import asyncio
import hashlib
import json
import mimetypes
import os
import uuid
from pathlib import Path, PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.services import dataset_service, storage_service


def source_path(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ":" in value
        or ".." in path.parts
        or len(value) > 1024
        or any(ord(c) < 32 for c in value)
    ):
        raise StatForgeError(
            422, "SOURCE_PATH_INVALID", "文件的相对目录无法读取，请重新选择文件夹。"
        )
    return str(path)


def file_digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def store(
    db: AsyncSession,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    path: str,
    filename: str,
    relative: str,
    linked: bool = False,
) -> Dataset:
    await dataset_service._verify_project_access(db, project_id, owner_id)
    relative = source_path(relative)
    filename = PurePosixPath(filename.replace("\\", "/")).name
    sha = await asyncio.to_thread(file_digest, path)
    identifier = uuid.uuid4()
    safe = dataset_service.sanitize_filename(filename)
    key = storage_service.generate_object_key(owner_id, project_id, identifier, safe)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    row = Dataset(
        id=identifier,
        project_id=project_id,
        original_filename=filename,
        safe_filename=safe,
        file_format=Path(filename).suffix.lstrip(".").lower()[:20] or "file",
        mime_type=mime,
        file_size=os.path.getsize(path),
        sha256=sha,
        object_key=key,
        bucket=settings.minio_bucket,
        upload_status=UploadStatus.UPLOADED,
        name=filename,
        file_path=key,
        file_type="source",
        content_hash=sha,
        profile_status="not_prepared",
        schema_json="[]",
        preview_json=json.dumps({"raw_file": True, "local_file": linked, "source_path": relative}),
    )
    if Path(filename).suffix.lower() in {".pkl", ".pickle"}:
        with open(path, "rb") as stream:
            header = stream.read(256)
            is_signal = any(
                marker in header
                for marker in (
                    b"statforge-physiocgm-v1",
                    b"statforge-bidmc-v1",
                    b"statforge-hapt-v1",
                )
            )
        if is_signal:
            row.preview_json = json.dumps(
                {
                    "raw_file": True,
                    "local_file": linked,
                    "source_path": relative,
                    "signal_session": True,
                }
            )
            row.upload_status = UploadStatus.READY
    try:
        if not linked:
            storage_service.upload_file(key, path, mime)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row
    except Exception:
        if not linked:
            storage_service.delete_object(key)
        raise
