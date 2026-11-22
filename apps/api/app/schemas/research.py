"""Formal research API; completion and object paths are never client inputs."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.research.contracts import State
from app.research.egress import DisclosureSelection


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceCreate(RequestModel):
    title: str = Field(min_length=1, max_length=255)
    project_id: uuid.UUID | None = None


class SessionCreate(RequestModel):
    title: str = Field(min_length=1, max_length=255)


class LifecycleChange(RequestModel):
    expected_revision: int = Field(ge=0)
    action: Literal["archive", "restore", "delete"]


class WorkspaceSave(RequestModel):
    expected_revision: int = Field(ge=0)
    project_id: uuid.UUID


class AttachmentCreate(RequestModel):
    target_kind: Literal["project", "dataset_version", "artifact"]
    target_id: uuid.UUID
    allow_cross_project: bool = False


class RunCreate(RequestModel):
    kind: Literal["quality", "statistics", "ml", "workflow"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict)
    assistant: DisclosureSelection | None = None


class CancelRun(RequestModel):
    expected_revision: int = Field(ge=0)


class PlanningControlRequest(CancelRun):
    action: Literal["pause", "resume", "cancel"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class PlannerRunCreate(RequestModel):
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    selection: DisclosureSelection


class PlannerRunCreateV2(PlannerRunCreate):
    version: Literal["research-planner-v2"]
    protocol_version_id: uuid.UUID
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    protocol_generation: int = Field(strict=True, ge=1, le=200)


class RetryRun(CancelRun):
    retry_key: str = Field(min_length=1, max_length=200)


class MessageCreate(RequestModel):
    text: str = Field(min_length=1, max_length=16000)


class DomainResultCreate(RequestModel):
    domain_kind: Literal["quality", "statistics", "ml", "workflow"]
    domain_run_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AttemptResponse(RowModel):
    id: uuid.UUID
    number: int
    state: State
    retry_key: str | None
    base_revision: int | None
    domain_kind: str | None
    domain_run_id: uuid.UUID | None
    completion_hash: str | None


class ArtifactResponse(RowModel):
    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    media_type: str
    byte_size: int
    sha256: str
    logical_hash: str
    provenance_json: str


class WorkspaceResponse(RowModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    title: str
    revision: int
    lifecycle_epoch: int
    is_deleted: bool
    event_sequence: int
    event_head_hash: str


class SessionResponse(RowModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    revision: int
    lifecycle_epoch: int
    is_archived: bool
    is_deleted: bool


class AttachmentResponse(RowModel):
    id: uuid.UUID
    target_kind: str
    target_id: uuid.UUID
    source_project_id: uuid.UUID
    target_sha256: str | None
    active: bool


class RunResponse(RowModel):
    id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    input_hash: str
    state: State
    revision: int
    attempt_count: int
    domain_kind: str | None
    domain_run_id: uuid.UUID | None
    completion_hash: str | None


class MessageResponse(RowModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    logical_hash: str
    content_json: str


class EventResponse(RowModel):
    sequence: int
    entity_kind: str
    entity_id: uuid.UUID
    event_type: str
    payload_json: str
    logical_hash: str
    previous_hash: str
    chain_hash: str
    hash_version: str
    occurred_at: str
