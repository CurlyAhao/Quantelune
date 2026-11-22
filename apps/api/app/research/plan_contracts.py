"""Bounded, explicit source DAG; proposals confer no execution authority."""

import uuid
from typing import Any, Literal

from pydantic import Field

from app.research.context_contracts import ContextModel, FieldReference

CapabilityId = Literal["inspect", "profile", "quality", "descriptive", "correlation"]


class PlanStep(ContextModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    capability: CapabilityId
    dataset_id: uuid.UUID
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: list[FieldReference] = Field(default_factory=list, max_length=50)
    depends_on: list[str] = Field(default_factory=list, max_length=7)
    configuration: dict[str, Any] = Field(default_factory=dict)


class PlanDocument(ContextModel):
    version: Literal["research-plan-v1"] = "research-plan-v1"
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=160)
    research_question: str = Field(default="", max_length=1000)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)


class CapabilityContract(ContextModel):
    id: CapabilityId
    version: str
    effect: Literal["metadata_read", "saved_profile_read", "native_run"]
    methods: list[str] = Field(default_factory=list)


class RuleSelection(ContextModel):
    capability: CapabilityId
    dataset_id: uuid.UUID | None = None
    field_names: list[str] = Field(default_factory=list, max_length=50)
    configuration: dict[str, Any] = Field(default_factory=dict)


class RuleProposalRequest(ContextModel):
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection: RuleSelection


class DraftSave(ContextModel):
    plan: PlanDocument
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class PlanAccept(ContextModel):
    version_id: uuid.UUID
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
