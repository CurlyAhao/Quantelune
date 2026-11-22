"""Closed configuration for evaluating already stored, aligned predictions."""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.research.context_contracts import ContextModel, FieldReference

EVALUATION_VERSION = "existing-predictions-regression-v1"
Metric = Literal["rmse", "mae", "r2"]


class RegressionEvaluation(ContextModel):
    version: Literal["existing-predictions-regression-v1"]
    operation: Literal["evaluate_existing_predictions"]
    actual: FieldReference
    predicted: FieldReference
    primary_metric: Metric
    secondary_metrics: list[Metric] = Field(max_length=2)

    @model_validator(mode="after")
    def aligned_fields_and_metrics(self) -> Self:
        a, p = self.actual, self.predicted
        if (
            a.name == p.name
            or a.dataset_id != p.dataset_id
            or a.dataset_sha256 != p.dataset_sha256
            or a.schema_sha256 != p.schema_sha256
        ):
            raise ValueError("Evaluation needs distinct fields in one exact source snapshot.")
        metrics = [self.primary_metric, *self.secondary_metrics]
        if len(metrics) != len(set(metrics)):
            raise ValueError("Evaluation metrics must be ordered and unique.")
        return self
