"""Reporting routes: freeze, fork, export."""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.models.project import ProjectStatus
from app.models.user import User
from app.reporting.engine import (
    fork_project,
    freeze_project,
)
from app.schemas.reporting import ForkRequest, ProjectSnapshotResponse
from app.services.project_access import owned_project

logger = logging.getLogger("statforge.reporting")
router = APIRouter(tags=["reporting"])
_EXPORT_NOT_READY_CODE = "REPORT_EXPORT_NOT_READY"
_EXPORT_NOT_READY_DETAIL = "Project export is not available yet."


@router.get("/api/v1/projects/{project_id}/snapshot", response_model=ProjectSnapshotResponse)
async def get_snapshot(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ProjectSnapshotResponse:
    project = await owned_project(db, project_id, current_user.id)
    return ProjectSnapshotResponse(
        project_id=project.id,
        title=project.title,
        status=project.status,
        frozen_at=project.frozen_at,
        snapshot=None,
    )


@router.post("/api/v1/projects/{project_id}/freeze", response_model=ProjectSnapshotResponse)
async def freeze(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ProjectSnapshotResponse:
    project = await owned_project(db, project_id, current_user.id)
    if project.status == ProjectStatus.FROZEN:
        raise StatForgeError(409, "ALREADY_FROZEN", "Project is already frozen.")
    await freeze_project(db, project, current_user)
    return ProjectSnapshotResponse(
        project_id=project.id,
        title=project.title,
        status=project.status,
        frozen_at=project.frozen_at,
        snapshot=json.loads(project.snapshot_json) if project.snapshot_json else None,
    )


@router.post("/api/v1/projects/{project_id}/fork", response_model=ProjectSnapshotResponse)
async def fork(
    project_id: uuid.UUID,
    body: ForkRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ProjectSnapshotResponse:
    project = await owned_project(db, project_id, current_user.id)
    if project.status != ProjectStatus.FROZEN:
        raise StatForgeError(409, "NOT_FROZEN", "Only frozen projects can be forked.")
    fork = await fork_project(db, project, current_user, body.title)
    return ProjectSnapshotResponse(
        project_id=fork.id,
        title=fork.title,
        status=fork.status,
    )


@router.get(
    "/api/v1/projects/{project_id}/export",
    status_code=503,
    responses={
        503: {
            "description": _EXPORT_NOT_READY_DETAIL,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "detail": {"type": "string"},
                            "error_code": {"type": "string", "enum": [_EXPORT_NOT_READY_CODE]},
                        },
                        "required": ["detail", "error_code"],
                    },
                    "example": {
                        "detail": _EXPORT_NOT_READY_DETAIL,
                        "error_code": _EXPORT_NOT_READY_CODE,
                    },
                }
            },
        }
    },
)
async def export_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> None:
    await owned_project(db, project_id, current_user.id)
    raise StatForgeError(
        503,
        _EXPORT_NOT_READY_CODE,
        _EXPORT_NOT_READY_DETAIL,
    )
