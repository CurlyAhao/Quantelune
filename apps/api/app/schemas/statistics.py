"""Pydantic schemas for the statistical-analysis endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from app.models.statistics import StatisticalRunStatus
from app.statistics.binary_prediction_evaluation import BinaryPredictionEvaluation
from app.statistics.prediction_evaluation import RegressionEvaluation
from app.statistics.types import Alternative, MethodId, MissingPolicy


class StatisticalRunRequest(BaseModel):
    """Configuration for a single statistical method run."""

    method_id: MethodId
    variables: list[str] = Field(default_factory=list)
    group_variable: str | None = None
    paired: bool = False
    mu: float = 0.0
    expected_proportions: dict[str, float] | None = None
    alternative: Alternative = Alternative.TWO_SIDED
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    missing_value_policy: MissingPolicy = MissingPolicy.LISTWISE_DELETE
    # Regression-specific (optional; ignored by non-regression methods).
    target: str | None = None
    categorical_variables: list[str] = Field(default_factory=list)
    reference_categories: dict[str, str] | None = None
    standardize: bool = False
    alpha: float = Field(default=1.0, ge=0.0)
    l1_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    evaluation: RegressionEvaluation | BinaryPredictionEvaluation | None = None

    @model_validator(mode="before")
    @classmethod
    def evaluation_scope(cls, value):
        if (
            isinstance(value, dict)
            and value.get("method_id") in {"evaluate_predictions", "evaluate_binary_predictions"}
            and set(value) - set(cls.model_fields)
        ):
            raise ValueError("Unknown existing-prediction evaluation parameter.")
        if (
            isinstance(value, dict)
            and "evaluation" in value
            and value.get("method_id")
            not in {"evaluate_predictions", "evaluate_binary_predictions"}
        ):
            raise ValueError("Evaluation configuration is method-specific.")
        return value

    @model_validator(mode="after")
    def closed_evaluation(self):
        if self.method_id in {MethodId.EVALUATE_PREDICTIONS, MethodId.EVALUATE_BINARY_PREDICTIONS}:
            expected = (
                RegressionEvaluation
                if self.method_id == MethodId.EVALUATE_PREDICTIONS
                else BinaryPredictionEvaluation
            )
            if not isinstance(self.evaluation, expected):
                raise ValueError("Evaluation version does not match the selected method.")
            if (
                self.evaluation is None
                or self.missing_value_policy != MissingPolicy.ERROR_ON_MISSING
            ):
                raise ValueError(
                    "Evaluation requires its versioned configuration and error_on_missing."
                )
            defaults = dict(
                variables=[],
                group_variable=None,
                paired=False,
                mu=0.0,
                expected_proportions=None,
                alternative=Alternative.TWO_SIDED,
                confidence_level=0.95,
                target=None,
                categorical_variables=[],
                reference_categories=None,
                standardize=False,
                alpha=1.0,
                l1_ratio=0.5,
            )
            if any(getattr(self, name) != expected for name, expected in defaults.items()):
                raise ValueError("Inference, training and transformation parameters do not apply.")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_configuration(self, handler):
        value = handler(self)
        if self.evaluation is None:
            value.pop("evaluation", None)
        return value

    @field_validator("variables")
    @classmethod
    def _variables_bounded(cls, v: list[str]) -> list[str]:
        if len(v) > 50:
            raise ValueError("Too many variables (max 50).")
        return v

    @field_validator("confidence_level")
    @classmethod
    def _confidence_sane(cls, v: float) -> float:
        if v < 0.5 or v > 0.999:
            raise ValueError("confidence_level should be between 0.5 and 0.999.")
        return v

    @field_validator("expected_proportions")
    @classmethod
    def _proportions_positive(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        if any(p < 0 for p in v.values()):
            raise ValueError("Expected proportions must be non-negative.")
        if sum(v.values()) <= 0:
            raise ValueError("Expected proportions must sum to a positive value.")
        return v


class AssumptionCheckResponse(BaseModel):
    name: str
    passed: bool
    detail: str
    value: Any = None
    threshold: Any = None


class SampleInfoResponse(BaseModel):
    original_n: int
    excluded_n: int
    final_n: int
    reason: str = ""
    per_variable: dict[str, int] = Field(default_factory=dict)


class StatisticalResultResponse(BaseModel):
    method: str
    method_version: str
    status: str
    sample: SampleInfoResponse | None = None
    estimates: dict[str, Any] = Field(default_factory=dict)
    statistic: dict[str, Any] = Field(default_factory=dict)
    p_value: float | None = None
    alternative: str
    confidence_level: float
    confidence_interval: dict[str, Any] = Field(default_factory=dict)
    effect_size: dict[str, Any] = Field(default_factory=dict)
    descriptive_statistics: dict[str, Any] = Field(default_factory=dict)
    assumption_checks: list[AssumptionCheckResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    software_versions: dict[str, str] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


class StatFindingResponse(BaseModel):
    rule_id: str
    category: str
    severity: str
    title: str
    summary: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str | None = None
    created_at: str | None = None


class StatisticalRunResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    profile_id: uuid.UUID | None
    quality_run_id: uuid.UUID | None
    method: str
    status: StatisticalRunStatus
    methodset_version: str
    dataset_sha256: str
    input_config: dict[str, Any] | None = None
    missing_value_policy: str
    sample_size: int | None = None
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None = None
    error_message: str | None = None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    result: StatisticalResultResponse | None = None
    findings: list[StatFindingResponse] | None = None

    model_config = {"from_attributes": True}


class StatisticalRunSummary(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    method: str
    status: StatisticalRunStatus
    methodset_version: str
    missing_value_policy: str
    sample_size: int | None = None
    statistic_value: float | None = None
    p_value: float | None = None
    error_code: str | None = None
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class StatisticalRunsResponse(BaseModel):
    runs: list[StatisticalRunSummary]
    total: int


class MethodMetadata(BaseModel):
    method_id: str
    name: str
    description: str
    category: str
    version: str
    min_variables: int
    max_variables: int
    needs_group: bool
    requires_numeric: bool
    manual_configuration_supported: bool = True


class MethodsResponse(BaseModel):
    methodset_version: str
    methods: list[MethodMetadata]


class ValidationResult(BaseModel):
    applicable: bool
    method_id: str
    checks: list[AssumptionCheckResponse] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
