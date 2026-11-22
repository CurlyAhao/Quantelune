"""Pydantic schemas for data profiling endpoints."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.profiling import ProfileStatus


class ProfileResponse(BaseModel):
    """Full profile record including the parsed result payload."""

    id: uuid.UUID
    dataset_id: uuid.UUID
    status: ProfileStatus
    profiling_version: int
    algorithm_version: str
    dataset_sha256: str
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None = None
    error_message: str | None = None
    row_count: int | None
    column_count: int | None
    sampled: bool
    sample_size: int | None
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None


class ProfileColumnsResponse(BaseModel):
    """Per-column profile section."""

    columns: list[dict[str, Any]]


class CorrelationMatrix(BaseModel):
    """A single correlation matrix (Pearson or Spearman)."""

    columns: list[str]
    matrix: list[list[float | None]]


class ProfileCorrelationsResponse(BaseModel):
    """Correlations section of a profile."""

    pearson: CorrelationMatrix
    spearman: CorrelationMatrix
    note: str | None = None


class ColumnMissingness(BaseModel):
    """Per-column null counts."""

    name: str
    null_count: int
    null_rate: float


class RowMissingBucket(BaseModel):
    """Histogram bucket: rows having exactly `missing_count` null values."""

    missing_count: int
    row_count: int


class ProfileMissingnessResponse(BaseModel):
    """Missingness section of a profile."""

    per_column: list[ColumnMissingness]
    complete_rows: int
    complete_row_rate: float
    all_null_columns: list[str]
    high_missing_columns: list[str]
    row_missing_distribution: list[RowMissingBucket]
