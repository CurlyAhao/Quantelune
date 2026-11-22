"""AI assistant routes: status, review endpoints, method recommendation."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import build_provider
from app.ai.service import (
    explain_statistical_result,
    recommend_method,
    review_leakage,
    review_ml_evaluation,
    review_quality,
    review_regression,
)
from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.ai import AIInvocation
from app.models.user import User
from app.schemas.ai import (
    AIReviewResponse,
    AIStatusResponse,
    ExplainResultRequest,
    LeakageReviewRequest,
    MLEvaluationReviewRequest,
    RecommendMethodRequest,
    RegressionReviewRequest,
    ReviewQualityRequest,
)
from app.services.project_access import owned_project

logger = logging.getLogger("statforge.ai")
router = APIRouter(tags=["ai"])


async def _check_project_if_set(db: AsyncSession, project_id: uuid.UUID | None, user: User) -> None:
    """Verify the user owns the project when a project_id is provided."""
    if project_id is None:
        return
    await owned_project(db, project_id, user.id)


async def _log_invocation(
    db: AsyncSession,
    prompt_id: str,
    provider: str,
    model: str,
    meta: dict[str, Any],
    user: User,
    project_id: uuid.UUID | None,
    status: str = "completed",
) -> AIInvocation:
    inv = AIInvocation(
        project_id=project_id,
        provider=provider,
        model=model,
        prompt_id=prompt_id,
        prompt_version="v1",
        input_hash=meta.get("input_hash"),
        latency_ms=meta.get("latency_ms"),
        status=status,
        error_code=meta.get("error"),
        created_by=user.email,
    )
    usage = meta.get("token_usage")
    if isinstance(usage, dict):
        from json import dumps

        inv.token_usage_json = dumps(usage, ensure_ascii=False)
    if isinstance(meta.get("error"), str) and meta["error"]:
        inv.error_message = meta["error"][:2000]
        inv.status = "failed"
    db.add(inv)
    await db.flush()
    return inv


def _enabled_check() -> None:
    from app.exceptions import StatForgeError

    if not settings.ai_enabled:
        raise StatForgeError(
            503,
            "AI_NOT_ENABLED",
            "AI is not enabled. Set AI_ENABLED=true and configure a provider.",
        )
    provider = build_provider()
    if not provider.ready or not settings.ai_model:
        raise StatForgeError(
            503,
            "AI_NOT_READY",
            "AI is not ready. Configure AI_MODEL, AI_BASE_URL, and AI_API_KEY.",
        )


@router.get("/api/v1/ai/status", response_model=AIStatusResponse)
async def ai_status() -> AIStatusResponse:
    provider = build_provider()
    ai_model = settings.ai_model
    return AIStatusResponse(
        enabled=settings.ai_enabled,
        provider=settings.ai_provider,
        model=ai_model,
        ready=provider.ready and bool(ai_model),
        message="AI is configured and ready."
        if (provider.ready and ai_model)
        else "AI is not fully configured.",
    )


@router.post("/api/v1/ai/review/quality", response_model=AIReviewResponse)
async def api_review_quality(
    body: ReviewQualityRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AIReviewResponse:
    _enabled_check()
    await _check_project_if_set(db, body.project_id, current_user)
    result, meta = await review_quality(body.quality_data)
    inv = await _log_invocation(
        db,
        meta.get("prompt_id", "data-quality-review-v1"),
        meta.get("provider", ""),
        meta.get("model", ""),
        meta,
        current_user,
        body.project_id,
        status="completed" if result is not None else "failed",
    )
    return AIReviewResponse(
        prompt_id=meta.get("prompt_id", ""),
        provider=meta.get("provider", ""),
        model=meta.get("model", ""),
        status=inv.status,
        invocation_id=inv.id,
        result=result.model_dump() if result else None,
        error=meta.get("error"),
        latency_ms=meta.get("latency_ms"),
        token_usage=meta.get("token_usage"),
    )


@router.post("/api/v1/ai/recommend/method", response_model=AIReviewResponse)
async def api_recommend_method(
    body: RecommendMethodRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AIReviewResponse:
    _enabled_check()
    await _check_project_if_set(db, body.project_id, current_user)
    result, meta = await recommend_method(body.model_dump())
    inv = await _log_invocation(
        db,
        meta.get("prompt_id", "method-advisor-v1"),
        meta.get("provider", ""),
        meta.get("model", ""),
        meta,
        current_user,
        body.project_id,
        status="completed" if result is not None else "failed",
    )
    return AIReviewResponse(
        prompt_id=meta.get("prompt_id", ""),
        provider=meta.get("provider", ""),
        model=meta.get("model", ""),
        status=inv.status,
        invocation_id=inv.id,
        result=result.model_dump() if result else None,
        error=meta.get("error"),
        latency_ms=meta.get("latency_ms"),
        token_usage=meta.get("token_usage"),
    )


@router.post("/api/v1/ai/review/statistical-result", response_model=AIReviewResponse)
async def api_explain_result(
    body: ExplainResultRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AIReviewResponse:
    _enabled_check()
    await _check_project_if_set(db, body.project_id, current_user)
    result, meta = await explain_statistical_result(body.result)
    inv = await _log_invocation(
        db,
        meta.get("prompt_id", "statistical-result-explanation-v1"),
        meta.get("provider", ""),
        meta.get("model", ""),
        meta,
        current_user,
        body.project_id,
        status="completed" if result is not None else "failed",
    )
    return AIReviewResponse(
        prompt_id=meta.get("prompt_id", ""),
        provider=meta.get("provider", ""),
        model=meta.get("model", ""),
        status=inv.status,
        invocation_id=inv.id,
        result=result.model_dump() if result else None,
        error=meta.get("error"),
        latency_ms=meta.get("latency_ms"),
        token_usage=meta.get("token_usage"),
    )


@router.post("/api/v1/ai/review/regression", response_model=AIReviewResponse)
async def api_review_regression(
    body: RegressionReviewRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AIReviewResponse:
    _enabled_check()
    await _check_project_if_set(db, body.project_id, current_user)
    result, meta = await review_regression(body.regression_data)
    inv = await _log_invocation(
        db,
        meta.get("prompt_id", "regression-review-v1"),
        meta.get("provider", ""),
        meta.get("model", ""),
        meta,
        current_user,
        body.project_id,
        status="completed" if result is not None else "failed",
    )
    return AIReviewResponse(
        prompt_id=meta.get("prompt_id", ""),
        provider=meta.get("provider", ""),
        model=meta.get("model", ""),
        status=inv.status,
        invocation_id=inv.id,
        result=result.model_dump() if result else None,
        error=meta.get("error"),
        latency_ms=meta.get("latency_ms"),
        token_usage=meta.get("token_usage"),
    )


@router.post("/api/v1/ai/review/ml-evaluation", response_model=AIReviewResponse)
async def api_review_ml_evaluation(
    body: MLEvaluationReviewRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AIReviewResponse:
    _enabled_check()
    await _check_project_if_set(db, body.project_id, current_user)
    result, meta = await review_ml_evaluation(body.ml_data)
    inv = await _log_invocation(
        db,
        meta.get("prompt_id", "ml-evaluation-review-v1"),
        meta.get("provider", ""),
        meta.get("model", ""),
        meta,
        current_user,
        body.project_id,
        status="completed" if result is not None else "failed",
    )
    return AIReviewResponse(
        prompt_id=meta.get("prompt_id", ""),
        provider=meta.get("provider", ""),
        model=meta.get("model", ""),
        status=inv.status,
        invocation_id=inv.id,
        result=result.model_dump() if result else None,
        error=meta.get("error"),
        latency_ms=meta.get("latency_ms"),
        token_usage=meta.get("token_usage"),
    )


@router.post("/api/v1/ai/review/leakage", response_model=AIReviewResponse)
async def api_review_leakage(
    body: LeakageReviewRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AIReviewResponse:
    _enabled_check()
    await _check_project_if_set(db, body.project_id, current_user)
    result, meta = await review_leakage(body.leakage_data)
    inv = await _log_invocation(
        db,
        meta.get("prompt_id", "leakage-review-v1"),
        meta.get("provider", ""),
        meta.get("model", ""),
        meta,
        current_user,
        body.project_id,
        status="completed" if result is not None else "failed",
    )
    return AIReviewResponse(
        prompt_id=meta.get("prompt_id", ""),
        provider=meta.get("provider", ""),
        model=meta.get("model", ""),
        status=inv.status,
        invocation_id=inv.id,
        result=result.model_dump() if result else None,
        error=meta.get("error"),
        latency_ms=meta.get("latency_ms"),
        token_usage=meta.get("token_usage"),
    )
