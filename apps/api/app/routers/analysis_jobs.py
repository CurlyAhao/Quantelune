import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.ml import MLExperiment
from app.models.user import User
from app.schemas.material import Strict
from app.schemas.statistics import StatisticalRunRequest
from app.services import analysis_jobs as jobs
from app.services import dataset_service, project_runs

router = APIRouter(prefix="/api/v1", tags=["analysis-runs"])
RunKind = Literal["analysis_run", "plot_run", "research", "statistics", "ml", "quality", "workflow"]


class RequestKey(Strict):
    request_id: uuid.UUID


class StatisticsStart(RequestKey):
    statistics: StatisticalRunRequest


@router.post("/datasets/{dataset_id}/statistics/jobs", status_code=202)
async def start_statistics(
    dataset_id: uuid.UUID,
    body: StatisticsStart,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await jobs.start(
        db, user, dataset, "statistics", body.request_id, body.statistics.model_dump(mode="json")
    )


@router.post("/ml/experiments/{experiment_id}/jobs", status_code=202)
async def start_ml(
    experiment_id: uuid.UUID,
    body: RequestKey,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    experiment = await db.get(MLExperiment, experiment_id)
    if experiment is None:
        raise jobs.failure("没有找到这份模型配置。", 404)
    dataset = await dataset_service.get_dataset_with_access(db, experiment.dataset_id, user.id)
    return await jobs.start(
        db,
        user,
        dataset,
        "ml",
        body.request_id,
        jobs.json.loads(experiment.config_json),
        experiment,
    )


@router.get("/analysis-jobs/{identity}")
async def read(
    identity: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await jobs.get(db, identity, user)


@router.post("/analysis-jobs/{identity}/cancel")
async def cancel(
    identity: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await jobs.cancel(db, identity, user)


@router.post("/analysis-jobs/{identity}/retry", status_code=202)
async def retry(
    identity: uuid.UUID,
    body: RequestKey,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await jobs.retry(db, identity, user, body.request_id)


@router.get("/projects/{project_id}/runs")
async def runs(
    project_id: uuid.UUID,
    q: str = Query(default="", max_length=200),
    kind: RunKind | None = None,
    state: Literal["all", "active", "attention", "finished"] = "all",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await project_runs.listing(db, project_id, user, q, kind, state, offset, limit)


@router.get("/projects/{project_id}/runs/{kind}/{identity}")
async def detail(
    project_id: uuid.UUID,
    kind: RunKind,
    identity: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await project_runs.detail(db, project_id, user, kind, identity)


@router.post("/projects/{project_id}/runs/{kind}/{identity}/cancel")
async def cancel_project_run(
    project_id: uuid.UUID,
    kind: RunKind,
    identity: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await project_runs.cancel(db, project_id, user, kind, identity)
