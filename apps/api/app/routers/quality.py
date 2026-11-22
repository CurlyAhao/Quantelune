"""Data-quality routes: run, retrieve, list history, and filter findings."""

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
from app.models.quality import QualityFinding, QualityRun
from app.models.user import User
from app.quality.thresholds import ThresholdConfig, ThresholdValidationError
from app.schemas.quality import (
    QualityFindingResponse,
    QualityRunRequest,
    QualityRunResponse,
    QualityRunsResponse,
    QualityRunSummary,
    QualityScoreResponse,
)
from app.services import audit_service, dataset_service, quality_service

logger = logging.getLogger("statforge.quality")
router = APIRouter(tags=["quality"])


class QualityRunNotFoundError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(404, "QUALITY_RUN_NOT_FOUND", "Quality run not found.")


# ── Response builders ────────────────────────────────────────────────────────


def _finding_to_response(f: QualityFinding) -> QualityFindingResponse:
    return QualityFindingResponse(
        rule_id=f.rule_id,
        rule_version=f.rule_version,
        category=f.category,
        severity=f.severity,
        status=f.status,
        title=f.title,
        summary=f.summary,
        affected_columns=_loads(f.affected_columns_json, []),
        affected_count=f.affected_count,
        affected_ratio=f.affected_ratio,
        threshold=_loads(f.threshold_json, None),
        observed_value=_loads(f.observed_value_json, None),
        evidence=_loads(f.evidence_json, {}),
        remediation=f.remediation or "",
        created_at=f.created_at.isoformat() if f.created_at else None,
    )


def _loads(text: str | None, default: Any) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _run_to_response(run: QualityRun, include_findings: bool = True) -> QualityRunResponse:
    score = _loads(run.score_json, None)
    findings = [_finding_to_response(f) for f in run.findings] if include_findings else None
    return QualityRunResponse(
        id=run.id,
        dataset_id=run.dataset_id,
        profile_id=run.profile_id,
        status=run.status,
        ruleset_version=run.ruleset_version,
        threshold_config=_loads(run.threshold_config_json, None),
        dataset_sha256=run.dataset_sha256,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_code=run.error_code,
        error_message=run.error_message,
        total_rules=run.total_rules,
        passed_count=run.passed_count,
        warning_count=run.warning_count,
        failed_count=run.failed_count,
        error_count=run.error_count,
        skipped_count=run.skipped_count,
        overall_score=run.overall_score,
        score=QualityScoreResponse(**score) if score else None,
        ohlcv_enabled=run.ohlcv_enabled,
        sampled=run.sampled,
        created_by=run.created_by,
        created_at=run.created_at,
        updated_at=run.updated_at,
        findings=findings,
    )


async def _load_run_with_access(db: AsyncSession, run_id: uuid.UUID, user: User) -> QualityRun:
    run = await db.get(QualityRun, run_id)
    if run is None:
        raise QualityRunNotFoundError()
    # Ownership check via the parent dataset.
    await dataset_service.get_dataset_with_access(db, run.dataset_id, user.id)
    return run


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post(
    "/api/v1/datasets/{dataset_id}/quality/run",
    response_model=QualityRunResponse,
    status_code=201,
)
async def run_quality(
    dataset_id: uuid.UUID,
    body: QualityRunRequest | None = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> QualityRunResponse:
    """Run the deterministic quality rule set against a dataset.

    Auto-profiles the dataset if needed. Each call creates a new run (history
    is preserved). Threshold overrides are validated and recorded.
    """
    body = body or QualityRunRequest()
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    if dataset.upload_status != UploadStatus.READY:
        raise DatasetNotReadyError()

    try:
        thresholds = ThresholdConfig().with_overrides(body.thresholds)
    except ThresholdValidationError as exc:
        raise StatForgeError(422, "INVALID_THRESHOLD", str(exc)) from exc

    await audit_service.record(
        db,
        event_type="quality_run.threshold_config",
        entity_type="dataset",
        entity_id=dataset.id,
        actor=current_user,
        project_id=dataset.project_id,
        after={"overrides": body.thresholds, "ohlcv": body.enable_ohlcv},
    )

    run = await quality_service.run_quality(
        db,
        dataset,
        current_user,
        thresholds,
        enable_ohlcv=body.enable_ohlcv,
        ohlcv_mapping=body.ohlcv_mapping,
    )
    return _run_to_response(run)


@router.post(
    "/api/v1/datasets/{dataset_id}/quality/recompute",
    response_model=QualityRunResponse,
    status_code=201,
)
async def recompute_quality(
    dataset_id: uuid.UUID,
    body: QualityRunRequest | None = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> QualityRunResponse:
    """Explicitly force a fresh quality run (same as run with force semantics)."""
    body = body or QualityRunRequest(force=True)
    return await run_quality(dataset_id, body, db, current_user)


@router.get(
    "/api/v1/datasets/{dataset_id}/quality",
    response_model=QualityRunResponse,
)
async def get_latest_quality(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> QualityRunResponse:
    """Get the most recent quality run (with findings) for a dataset."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    run = await quality_service.latest_run(db, dataset.id)
    if run is None:
        raise QualityRunNotFoundError()
    return _run_to_response(run)


@router.get(
    "/api/v1/datasets/{dataset_id}/quality/runs",
    response_model=QualityRunsResponse,
)
async def list_quality_runs(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> QualityRunsResponse:
    """List all quality runs for a dataset (newest first), without findings."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    result = await db.execute(
        select(QualityRun)
        .where(QualityRun.dataset_id == dataset.id)
        .order_by(QualityRun.created_at.desc())
    )
    runs = list(result.scalars().all())
    return QualityRunsResponse(
        runs=[QualityRunSummary.model_validate(r) for r in runs],
        total=len(runs),
    )


@router.get(
    "/api/v1/quality-runs/{run_id}",
    response_model=QualityRunResponse,
)
async def get_quality_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> QualityRunResponse:
    """Get a specific quality run with findings."""
    run = await _load_run_with_access(db, run_id, current_user)
    return _run_to_response(run)


@router.get(
    "/api/v1/quality-runs/{run_id}/findings",
)
async def list_quality_findings(
    run_id: uuid.UUID,
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None),
    column: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List findings for a run with optional filters and pagination."""
    run = await _load_run_with_access(db, run_id, current_user)

    findings = list(run.findings)
    if category:
        findings = [f for f in findings if f.category == category]
    if severity:
        findings = [f for f in findings if f.severity == severity]
    if status:
        findings = [f for f in findings if f.status == status]
    if column:
        findings = [f for f in findings if column in (_loads(f.affected_columns_json, []) or [])]

    total = len(findings)
    start = (page - 1) * page_size
    page_items = findings[start : start + page_size]

    return {
        "findings": [_finding_to_response(f).model_dump() for f in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
