import json
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.project import ProjectResponse
from app.services import dataset_service, project_files

router = APIRouter(prefix="/api/v1/projects", tags=["project-files"])


@router.post("/import-qdpx")
async def import_qdpx(
    file: UploadFile = File(...),
    directory: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.routers.datasets import _stream_to_temp
    from app.services import refi_project

    path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
    try:
        return await refi_project.restore(db, user, Path(path), directory)
    finally:
        Path(path).unlink(missing_ok=True)


@router.get("/{project_id}/qdpx-summary")
async def qdpx_summary(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import refi_project

    project = await dataset_service._verify_project_access(db, project_id, user.id)
    return refi_project.state_for(project).get("summary")


@router.post("/{project_id}/merge-qdpx/preview")
async def preview_qdpx_merge(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.routers.datasets import _stream_to_temp
    from app.services import refi_project_merge

    project = await dataset_service._verify_project_access(db, project_id, user.id)
    path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
    try:
        return (await refi_project_merge.prepare(db, user, project, Path(path)))["preview"]
    finally:
        Path(path).unlink(missing_ok=True)


@router.post("/{project_id}/merge-qdpx")
async def merge_qdpx(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    token: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.routers.datasets import _stream_to_temp
    from app.services import refi_project_merge

    project = await dataset_service._verify_project_access(db, project_id, user.id)
    path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
    try:
        return await refi_project_merge.apply(db, user, project, Path(path), token)
    finally:
        Path(path).unlink(missing_ok=True)


@router.get("/{project_id}/export-qdpx")
async def export_qdpx(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import refi_project

    project = await dataset_service._verify_project_access(db, project_id, user.id)
    with tempfile.NamedTemporaryFile(suffix=".qdpx", delete=False) as temp:
        path = Path(temp.name)
    try:
        await refi_project.capture(db, project, user, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        media_type="application/zip",
        filename=refi_project.safe_name(project.title) + ".qdpx",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.get("/{project_id}/package")
async def package(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    project = await dataset_service._verify_project_access(db, project_id, user.id)
    content = await project_files.capture(db, project, user)
    return Response(
        content,
        media_type="application/vnd.statforge.project",
        headers={"Content-Disposition": f'attachment; filename="project-{project_id}.statforge"'},
    )


@router.post("/open-file", response_model=ProjectResponse)
async def open_file(
    file: UploadFile = File(...),
    as_copy: bool = Form(False),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.routers.datasets import _stream_to_temp

    path, _ = await _stream_to_temp(file, settings.max_upload_size_bytes)
    try:
        return await project_files.restore(db, user, Path(path), as_copy=as_copy)
    finally:
        Path(path).unlink(missing_ok=True)


@router.get("/{project_id}/saved-content")
async def saved_content(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    project = await dataset_service._verify_project_access(db, project_id, user.id)
    snapshot = json.loads(project.snapshot_json or "{}")
    return snapshot.get("imported_project")
