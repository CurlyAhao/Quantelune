"""Pydantic schemas for the AI assistant endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class AIStatusResponse(BaseModel):
    enabled: bool
    provider: str
    model: str | None = None
    ready: bool
    message: str = ""


class ReviewQualityRequest(BaseModel):
    project_id: uuid.UUID | None = None
    quality_data: dict[str, Any] = Field(default_factory=dict)


class RecommendMethodRequest(BaseModel):
    project_id: uuid.UUID | None = None
    research_question: str = ""
    variables: list[dict[str, Any]] = Field(default_factory=list)
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    task_type: str | None = None


class ExplainResultRequest(BaseModel):
    project_id: uuid.UUID | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class RegressionReviewRequest(BaseModel):
    project_id: uuid.UUID | None = None
    regression_data: dict[str, Any] = Field(default_factory=dict)


class MLEvaluationReviewRequest(BaseModel):
    project_id: uuid.UUID | None = None
    ml_data: dict[str, Any] = Field(default_factory=dict)


class LeakageReviewRequest(BaseModel):
    project_id: uuid.UUID | None = None
    leakage_data: dict[str, Any] = Field(default_factory=dict)


class AIReviewResponse(BaseModel):
    prompt_id: str
    provider: str
    model: str
    status: str
    invocation_id: uuid.UUID | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: int | None = None
    token_usage: dict[str, Any] | None = None
