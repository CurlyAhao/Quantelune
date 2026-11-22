"""Pydantic schemas for the data-quality endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.quality import QualityRunStatus


class QualityRunRequest(BaseModel):
    """Body for triggering a quality run (all fields optional)."""

    # Threshold overrides; unknown keys are rejected by the engine.
    thresholds: dict[str, float] | None = None
    # Enable the optional OHLCV/crypto rule pack.
    enable_ohlcv: bool = False
    # Explicit OHLCV role → column mapping (auto-detected if omitted).
    ohlcv_mapping: dict[str, str] | None = None
    # Force a new run even if nothing changed.
    force: bool = False


class QualityFindingResponse(BaseModel):
    rule_id: str
    rule_name: str = ""
    description: str = ""
    status: str
    severity: str
    category: str
    title: str
    summary: str | None = None
    affected_columns: list[str] = Field(default_factory=list)
    affected_count: int = 0
    affected_ratio: float = 0.0
    threshold: dict[str, Any] | None = None
    observed_value: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str = ""
    rule_version: str = "1"
    limitations: str = ""
    created_at: str | None = None


class QualityScoreResponse(BaseModel):
    total_score: float
    dimensions: dict[str, Any]
    deductions: list[dict[str, Any]] = Field(default_factory=list)
    ruleset_note: str = ""


class QualityRunResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    profile_id: uuid.UUID | None
    status: QualityRunStatus
    ruleset_version: str
    threshold_config: dict[str, Any] | None = None
    dataset_sha256: str
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None = None
    error_message: str | None = None
    total_rules: int
    passed_count: int
    warning_count: int
    failed_count: int
    error_count: int
    skipped_count: int
    overall_score: float | None
    score: QualityScoreResponse | None = None
    ohlcv_enabled: bool
    sampled: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    findings: list[QualityFindingResponse] | None = None

    model_config = {"from_attributes": True}


class QualityRunSummary(BaseModel):
    """Lightweight run row for the history list (no findings)."""

    id: uuid.UUID
    dataset_id: uuid.UUID
    status: QualityRunStatus
    ruleset_version: str
    overall_score: float | None
    total_rules: int
    passed_count: int
    warning_count: int
    failed_count: int
    error_count: int
    skipped_count: int
    ohlcv_enabled: bool
    sampled: bool
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class QualityRunsResponse(BaseModel):
    runs: list[QualityRunSummary]
    total: int


class QualityFindingsResponse(BaseModel):
    findings: list[QualityFindingResponse]
    total: int
    page: int
    page_size: int
