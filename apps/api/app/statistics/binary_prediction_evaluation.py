"""Closed first version for already stored integer 0/1 prediction labels."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.research.context_contracts import ContextModel, FieldReference

BinaryMetric = Literal["recall", "accuracy", "precision", "specificity"]


class BinaryPredictionEvaluation(ContextModel):
    version: Literal["existing-predictions-binary-v1"]
    operation: Literal["evaluate_existing_predictions"]
    actual: FieldReference
    predicted: FieldReference
    label_encoding: Literal["integer_0_1"]
    positive_label: Annotated[int, Field(strict=True, ge=0, le=1)]
    primary_metric: BinaryMetric
    secondary_metrics: list[BinaryMetric] = Field(max_length=3)

    @model_validator(mode="after")
    def aligned_fields_and_unique_metrics(self) -> Self:
        a, p = self.actual, self.predicted
        if (
            a.name == p.name
            or a.dataset_id != p.dataset_id
            or a.dataset_sha256 != p.dataset_sha256
            or a.schema_sha256 != p.schema_sha256
        ):
            raise ValueError("Binary evaluation needs distinct fields in one exact snapshot.")
        metrics = [self.primary_metric, *self.secondary_metrics]
        if len(set(metrics)) != len(metrics):
            raise ValueError("Binary metrics must be ordered and unique.")
        return self
