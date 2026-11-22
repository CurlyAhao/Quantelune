"""Statistical-analysis routes: run, retrieve, history, methods, validate."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import DatasetNotReadyError, StatForgeError
from app.models.dataset import UploadStatus
from app.models.statistics import StatFinding, StatisticalRun, StatResult
from app.models.user import User
from app.schemas.statistics import (
    MethodMetadata,
    MethodsResponse,
    StatFindingResponse,
    StatisticalResultResponse,
    StatisticalRunRequest,
    StatisticalRunResponse,
    StatisticalRunsResponse,
    StatisticalRunSummary,
    ValidationResult,
)
from app.services import dataset_service, statistics_service
from app.statistics.registry import METHOD_REGISTRY, METHODSET_VERSION
from app.statistics.types import METHOD_CATEGORIES

logger = logging.getLogger("statforge.statistics")
router = APIRouter(tags=["statistics"])


class StatisticalRunNotFoundError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(404, "STATISTICAL_RUN_NOT_FOUND", "Statistical run not found.")


def _loads(text: str | None, default: Any) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _result_to_response(row: StatResult | None) -> StatisticalResultResponse | None:
    if row is None:
        return None
    full = _loads(row.result_json, {}) or {}
    return StatisticalResultResponse(**full) if full else None


def _finding_to_response(f: StatFinding) -> StatFindingResponse:
    return StatFindingResponse(
        rule_id=f.rule_id,
        category=f.category,
        severity=f.severity,
        title=f.title,
        summary=f.summary,
        evidence=_loads(f.evidence_json, {}),
        remediation=f.remediation,
        created_at=f.created_at.isoformat() if f.created_at else None,
    )


def _run_to_response(run: StatisticalRun, include_result: bool = True) -> StatisticalRunResponse:
    result_row = run.results[0] if run.results else None
    return StatisticalRunResponse(
        id=run.id,
        project_id=run.project_id,
        dataset_id=run.dataset_id,
        profile_id=run.profile_id,
        quality_run_id=run.quality_run_id,
        method=run.method,
        status=run.status,
        methodset_version=run.methodset_version,
        dataset_sha256=run.dataset_sha256,
        input_config=_loads(run.input_config_json, None),
        missing_value_policy=run.missing_value_policy,
        sample_size=run.sample_size,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_code=run.error_code,
        error_message=run.error_message,
        created_by=run.created_by,
        created_at=run.created_at,
        updated_at=run.updated_at,
        result=_result_to_response(result_row) if include_result else None,
        findings=[_finding_to_response(f) for f in run.findings] if include_result else None,
    )


def _summary(run: StatisticalRun) -> StatisticalRunSummary:
    result_row = run.results[0] if run.results else None
    return StatisticalRunSummary(
        id=run.id,
        dataset_id=run.dataset_id,
        method=run.method,
        status=run.status,
        methodset_version=run.methodset_version,
        missing_value_policy=run.missing_value_policy,
        sample_size=run.sample_size,
        statistic_value=result_row.statistic_value if result_row else None,
        p_value=result_row.p_value if result_row else None,
        error_code=run.error_code,
        created_by=run.created_by,
        created_at=run.created_at,
    )


async def _load_run_with_access(db: AsyncSession, run_id: uuid.UUID, user: User) -> StatisticalRun:
    run = await db.get(StatisticalRun, run_id)
    if run is None:
        raise StatisticalRunNotFoundError()
    await dataset_service.get_dataset_with_access(db, run.dataset_id, user.id)
    return run


@router.get("/api/v1/statistics/methods", response_model=MethodsResponse)
async def list_methods(current_user: User = Depends(get_current_user)) -> MethodsResponse:
    """List all available statistical methods with metadata (for the method picker)."""
    return MethodsResponse(
        methodset_version=METHODSET_VERSION,
        methods=[MethodMetadata(**m) for m in METHOD_REGISTRY.describe()],
    )


@router.post("/api/v1/statistics/validate", response_model=ValidationResult)
async def validate_method(
    body: StatisticalRunRequest,
    current_user: User = Depends(get_current_user),
) -> ValidationResult:
    """Validate a method configuration without running it (static applicability)."""
    method = METHOD_REGISTRY.get(body.method_id)
    reasons: list[str] = []
    n_predictors = len(body.variables) + len(body.categorical_variables)
    if n_predictors < method.min_variables:
        reasons.append(f"{method.name} needs at least {method.min_variables} predictor(s).")
    if n_predictors > method.max_variables:
        reasons.append(f"{method.name} accepts at most {method.max_variables} predictor(s).")
    if method.needs_group and not body.group_variable:
        reasons.append(f"{method.name} requires a grouping variable.")
    if METHOD_CATEGORIES.get(body.method_id) == "regression" and not body.target:
        reasons.append(f"{method.name} requires a target (outcome) variable.")
    return ValidationResult(
        applicable=not reasons,
        method_id=body.method_id.value,
        checks=[],
        reasons=reasons,
    )


@router.post(
    "/api/v1/datasets/{dataset_id}/statistics/run",
    response_model=StatisticalRunResponse,
    status_code=201,
)
async def run_statistics(
    dataset_id: uuid.UUID,
    body: StatisticalRunRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> StatisticalRunResponse:
    """Run a single statistical method against a dataset (creates a new run)."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    if dataset.upload_status != UploadStatus.READY:
        raise DatasetNotReadyError()

    # Validate predictor count against the method's declared limits (422 on mismatch).
    method = METHOD_REGISTRY.get(body.method_id)
    n_predictors = len(body.variables) + len(body.categorical_variables)
    if n_predictors < method.min_variables or n_predictors > method.max_variables:
        raise StatForgeError(
            422,
            "INVALID_METHOD_INPUT",
            f"{method.name} requires between {method.min_variables} and "
            f"{method.max_variables} predictor(s); got {n_predictors}.",
        )
    if method.needs_group and not body.group_variable:
        raise StatForgeError(
            422, "INVALID_METHOD_INPUT", f"{method.name} requires a grouping variable."
        )
    if METHOD_CATEGORIES.get(body.method_id) == "regression" and not body.target:
        raise StatForgeError(
            422, "INVALID_METHOD_INPUT", f"{method.name} requires a target (outcome) variable."
        )

    run = await statistics_service.run_statistics(db, dataset, current_user, body)
    return _run_to_response(run)


@router.get("/api/v1/datasets/{dataset_id}/statistics", response_model=StatisticalRunResponse)
async def get_latest_statistics(
    dataset_id: uuid.UUID,
    method: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> StatisticalRunResponse:
    """Get the most recent statistical run for a dataset (optionally by method)."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    run = await statistics_service.latest_run(db, dataset.id, method)
    if run is None:
        raise StatisticalRunNotFoundError()
    return _run_to_response(run)


@router.get("/api/v1/datasets/{dataset_id}/statistics/runs", response_model=StatisticalRunsResponse)
async def list_statistical_runs(
    dataset_id: uuid.UUID,
    method: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> StatisticalRunsResponse:
    """List statistical runs for a dataset (newest first), with optional method filter."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    stmt = (
        select(StatisticalRun)
        .where(StatisticalRun.dataset_id == dataset.id)
        .order_by(StatisticalRun.created_at.desc())
    )
    if method:
        stmt = stmt.where(StatisticalRun.method == method)
    res = await db.execute(stmt)
    runs = list(res.scalars().all())
    total = len(runs)
    start = (page - 1) * page_size
    page_items = runs[start : start + page_size]
    return StatisticalRunsResponse(runs=[_summary(r) for r in page_items], total=total)


@router.get("/api/v1/statistics-runs/{run_id}", response_model=StatisticalRunResponse)
async def get_statistical_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> StatisticalRunResponse:
    """Get a specific statistical run with its result and findings."""
    run = await _load_run_with_access(db, run_id, current_user)
    return _run_to_response(run)


@router.get("/api/v1/statistics-runs/{run_id}/results")
async def get_statistical_results(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the structured result(s) and findings for a run."""
    run = await _load_run_with_access(db, run_id, current_user)
    result_row = run.results[0] if run.results else None
    return {
        "run_id": str(run.id),
        "method": run.method,
        "status": run.status.value,
        "result": _result_to_response(result_row).model_dump() if result_row else None,
        "findings": [_finding_to_response(f).model_dump() for f in run.findings],
    }
