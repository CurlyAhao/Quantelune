"""Finite proposals and field-backed explanations; no free-form factual claims."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.preview import StrictModel
from app.schemas.workspace import PlanStep


class PlanProposal(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    research_question: str = Field(default="", max_length=1000)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)


class ExplanationField(StrictModel):
    kind: Literal["statistics", "ml"]
    run_id: uuid.UUID
    field: Literal[
        "status", "n", "statistic", "p_value", "confidence_interval", "group_means", "scores"
    ]


class ExplanationRequest(StrictModel):
    fields: list[ExplanationField] = Field(min_length=1, max_length=12)
    interpretation: Literal["descriptive", "mean_difference", "association", "prediction"] = (
        "descriptive"
    )
