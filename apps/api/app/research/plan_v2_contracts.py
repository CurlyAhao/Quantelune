"""Explicit Protocol-bound Plan v2 DTOs; v1 contracts are unchanged."""

import uuid
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from app.research.context_contracts import ContextModel, FieldReference
from app.research.contracts import canonical_bytes

PLAN_V2_MAX_BYTES = 512 * 1024


class PlanStepV2(ContextModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    capability: Literal["inspect", "profile", "quality", "statistics"]
    dataset_id: uuid.UUID
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: list[FieldReference] = Field(default_factory=list, max_length=50)
    depends_on: list[str] = Field(default_factory=list, max_length=7)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("configuration", mode="before")
    @classmethod
    def json_configuration(cls, value: Any) -> Any:
        # Validate raw keys before Pydantic can turn bytes into text.
        canonical_bytes(value)
        return value


class PlanDocumentV2(ContextModel):
    version: Literal["research-plan-v2"]
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=160)
    research_question: str = Field(min_length=1, max_length=1000)
    protocol_version_id: uuid.UUID
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_generation: int = Field(strict=True, ge=1, le=200)
    steps: list[PlanStepV2] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounded_document(self) -> Self:
        for step in self.steps:
            canonical_bytes(step.configuration)
        if len(canonical_bytes(self.model_dump(mode="json"))) > PLAN_V2_MAX_BYTES:
            raise ValueError("Plan v2 exceeds its canonical content limit.")
        return self


class DraftSaveV2(ContextModel):
    plan: PlanDocumentV2
    expected_revision: int = Field(strict=True, ge=0)
    idempotency_key: str = Field(strict=True, min_length=1, max_length=200)
