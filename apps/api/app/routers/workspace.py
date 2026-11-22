"""Finite project workspace preview: authenticated records and explicit execution."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.models.user import User
from app.models.workspace import WorkspaceExecution, WorkspaceRecord
from app.preview.workspace import (
    execute_plan,
    execution_response,
    instantiate,
    read_record,
    rejected,
    save_record,
)
from app.routers.preview import safe_body
from app.schemas.workspace import BindTemplate, ExecutePlan, GenerateFigureData, SaveRecord
from app.services.workflow_service import check_project_access

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace-preview"])


@router.get("/records")
async def list_records(
    project_id: uuid.UUID,
    kind: str | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await check_project_access(db, project_id, user)
    query = select(WorkspaceRecord.id).where(
        WorkspaceRecord.user_id == user.id, WorkspaceRecord.project_id == project_id
    )
    if kind is not None:
        if kind in {"figure_request", "figure_data", "history_copy", "plot_code_file"}:
            raise rejected("WORKSPACE_NOT_FOUND", 404)
        query = query.where(WorkspaceRecord.kind == kind)
    else:
        query = query.where(
            WorkspaceRecord.kind.notin_(
                ["figure_data", "figure_request", "history_copy", "plot_code_file"]
            )
        )
    ids = (await db.scalars(query.order_by(WorkspaceRecord.updated_at.desc()).limit(100))).all()
    records = []
    for record_id in ids:
        try:
            records.append(await read_record(db, record_id, user))
        except StatForgeError:
            records.append({"id": str(record_id), "state": "unavailable"})
    return {"records": records}


@router.post("/records")
async def create_record(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await save_record(
        db, user, await safe_body(request, SaveRecord, max_bytes=5 * 1024 * 1024)
    )


@router.post("/figure-data")
async def generate_figure_data(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.preview.figure_data import generate

    return await generate(db, user, await safe_body(request, GenerateFigureData))


@router.get("/records/{record_id}")
async def get_record(
    record_id: uuid.UUID,
    revision: int | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await read_record(db, record_id, user, revision)


@router.patch("/records/{record_id}")
async def patch_record(
    record_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await save_record(
        db, user, await safe_body(request, SaveRecord, max_bytes=5 * 1024 * 1024), record_id
    )


@router.post("/templates/{template_id}/instantiate")
async def instantiate_template(
    template_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await instantiate(db, user, template_id, await safe_body(request, BindTemplate))


@router.post("/plans/{plan_id}/execute")
async def run_plan(
    plan_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await execute_plan(db, user, plan_id, await safe_body(request, ExecutePlan))


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    row = await db.scalar(
        select(WorkspaceExecution).where(
            WorkspaceExecution.id == execution_id, WorkspaceExecution.user_id == user.id
        )
    )
    if row is None:
        raise rejected("WORKSPACE_NOT_FOUND", 404)
    return await execution_response(db, row, user)


@router.get("/executions")
async def list_executions(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.preview.workspace import load_record

    await load_record(db, plan_id, user)
    rows = (
        await db.scalars(
            select(WorkspaceExecution)
            .where(WorkspaceExecution.plan_id == plan_id, WorkspaceExecution.user_id == user.id)
            .order_by(WorkspaceExecution.created_at.desc())
            .limit(20)
        )
    ).all()
    return {"executions": [await execution_response(db, row, user) for row in rows]}
