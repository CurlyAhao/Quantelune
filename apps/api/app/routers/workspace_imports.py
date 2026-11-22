"""Read a user-selected ZIP into an import queue; never extract host paths."""

import base64
import json
import os
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import anyio
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.models.user import User
from app.routers.datasets import _stream_to_temp
from app.services.archive_staging import expanded_limit, require_disk_space
from app.services.material_service import project_access

router = APIRouter(prefix="/api/v1/projects", tags=["workspace imports"])


CHUNK = 1024 * 1024 * 3  # Multiple of three for independent Base64 chunks.


def read_zip(path: str, output: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive, open(output, "wb") as result:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) > 200:
                raise ValueError("ZIP最多200个文件")
            seen, total, first = set(), 0, True
            result.write(b'{"files":[')
            for entry in entries:
                label = entry.filename.replace("\\", "/")
                parts = PurePosixPath(label)
                if (
                    parts.is_absolute()
                    or ".." in parts.parts
                    or ":" in label
                    or any(ord(char) < 32 for char in label)
                    or len(label) > 900
                    or label in seen
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.flag_bits & 1
                ):
                    raise ValueError("压缩包包含无法导入的路径、链接、重复文件或加密文件")
                seen.add(label)
                if label.startswith("__MACOSX/") or parts.name == ".DS_Store":
                    continue
                if not first:
                    result.write(b",")
                first = False
                result.write(
                    b'{"path":' + json.dumps(label, ensure_ascii=False).encode() + b',"content":"'
                )
                size = 0
                with archive.open(entry) as source:
                    while chunk := source.read(CHUNK):
                        size += len(chunk)
                        total += len(chunk)
                        if total > expanded_limit():
                            raise ValueError("压缩包解压后超过大小上限")
                        encoded = base64.b64encode(chunk)
                        require_disk_space(Path(output).parent, len(encoded))
                        result.write(encoded)
                if size != entry.file_size:
                    raise ValueError("压缩包文件大小不一致")
                result.write(b'"}')
            result.write(b"]}")
    except (ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as reason:
        raise StatForgeError(422, "WORKSPACE_ZIP_INVALID", str(reason)[:300]) from None


@router.post("/{project_id}/imports/zip")
async def inspect_zip(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await project_access(db, project_id, user.id)
    path = output = None
    try:
        path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
        fd, output = tempfile.mkstemp(prefix="sf-zip-response-", suffix=".json")
        os.close(fd)
        await anyio.to_thread.run_sync(read_zip, path, output)
        response = FileResponse(
            output, media_type="application/json", background=BackgroundTask(os.unlink, output)
        )
        output = None
        return response
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
        if output and os.path.exists(output):
            os.unlink(output)
