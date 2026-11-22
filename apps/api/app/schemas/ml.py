"""Pydantic schemas for the ML experiment endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.ml.types import (
    MAX_FOLDS,
    MAX_MODELS_PER_RUN,
    MIN_FOLDS,
    MODEL_METADATA,
    MLModelId,
    MLTaskType,
    SplitStrategy,
)
from app.models.ml import MLRunStatus


class MLExperimentCreateRequest(BaseModel):
    name: str = Field(default="ML Experiment", max_length=255)
    task_type: MLTaskType
    target: str
    features: list[str] = Field(default_factory=list)
    categorical_features: list[str] = Field(default_factory=list)
    group_variable: str | None = None
    time_variable: str | None = None
    split_strategy: SplitStrategy = SplitStrategy.STRATIFIED_KFOLD
    test_size: float = Field(default=0.2, gt=0.0, lt=0.9)
    val_size: float = Field(default=0.15, ge=0.0, lt=0.8)
    n_splits: int = Field(default=5, ge=MIN_FOLDS, le=MAX_FOLDS)
    random_seed: int = 42
    model_ids: list[MLModelId] = Field(default_factory=list)
    standardize: bool = True
    impute_numeric: str = "median"
    impute_categorical: str = "most_frequent"
    handle_outliers: bool = False
    feature_selection_k: int | None = Field(default=None, ge=1, le=512, strict=True)
    model_params: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_feature_union(self) -> MLExperimentCreateRequest:
        """Save-time normalization: features become the stable deduped union of
        plain and categorical features; categorical_features stays a subset."""
        self.features = list(dict.fromkeys([*self.features, *self.categorical_features]))
        self.categorical_features = list(dict.fromkeys(self.categorical_features))
        if not self.features:
            raise ValueError("At least one feature is required.")
        return self

    @field_validator("model_ids")
    @classmethod
    def _models_bounded(cls, v: list[MLModelId]) -> list[MLModelId]:
        if len(v) > MAX_MODELS_PER_RUN:
            raise ValueError(f"At most {MAX_MODELS_PER_RUN} models per run.")
        return v

    @field_validator("impute_numeric")
    @classmethod
    def _impute_numeric_valid(cls, v: str) -> str:
        if v not in ("median", "mean"):
            raise ValueError("impute_numeric must be 'median' or 'mean'.")
        return v


class MLExperimentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    name: str
    task_type: str
    target_variable: str
    feature_variables: list[str] = Field(default_factory=list)
    categorical_variables: list[str] = Field(default_factory=list)
    config: dict[str, Any] | None = None
    dataset_sha256: str
    created_by: str | None
    created_at: datetime
    run_count: int = 0
    latest_run_status: str | None = None

    model_config = {"from_attributes": True}


class MLRunResponse(BaseModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    status: MLRunStatus
    random_seed: int
    validation_strategy: str | None
    split: dict[str, Any] | None = None
    models: list[dict[str, Any]] = Field(default_factory=list)
    comparison: dict[str, Any] | None = None
    leakage_checks: list[dict[str, Any]] = Field(default_factory=list)
    n_observations: int | None = None
    artifact_key: str | None = None
    artifact_meta: dict[str, Any] | None = None
    software_versions: dict[str, str] = Field(default_factory=dict)
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MLRunSummary(BaseModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    status: MLRunStatus
    validation_strategy: str | None
    n_observations: int | None = None
    primary_metric: str | None = None
    best_model: str | None = None
    best_score: float | None = None
    error_code: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MLRunsResponse(BaseModel):
    runs: list[MLRunSummary]
    total: int


class MLModelInfo(BaseModel):
    model_id: str
    name: str
    tasks: list[str]
    interpretable: bool


class MLMetadataResponse(BaseModel):
    tasks: list[str]
    split_strategies: list[str]
    models: list[MLModelInfo]


def build_metadata() -> MLMetadataResponse:
    return MLMetadataResponse(
        tasks=[t.value for t in MLTaskType],
        split_strategies=[s.value for s in SplitStrategy],
        models=[
            MLModelInfo(
                model_id=mid.value,
                name=meta["name"],
                tasks=meta["tasks"],
                interpretable=meta["interpretable"],
            )
            for mid, meta in MODEL_METADATA.items()
        ],
    )
