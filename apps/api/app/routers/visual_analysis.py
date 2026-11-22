"""Ordinary image feature extraction and comparison in a project."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.services import visual_samples as service

router = APIRouter(prefix="/api/v1/projects/{project_id}/visual-analysis", tags=["visual-analysis"])


@router.post("/runs")
async def start(
    project_id: uuid.UUID,
    body: service.VisualRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.start(db, user, project_id, body)


@router.get("/runs")
async def runs(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return {"items": await service.list_runs(db, user, project_id, dataset_id, task_id)}


@router.get("/runs/{run_id}")
async def get_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    row, run = await service.owned_run(db, user, project_id, run_id)
    return service.public(row, run)


@router.get("/runs/{run_id}/similar")
async def similar(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.similar(db, user, project_id, run_id, dataset_id)
