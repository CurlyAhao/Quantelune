"""Local source references, never a flattened table or provider wire."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldReference(ContextModel):
    dataset_id: uuid.UUID
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=255)
    variable_id: uuid.UUID | None = None


class ContextField(ContextModel):
    reference: FieldReference
    dtype: str
    inferred_type: str
    role: str | None = None
    semantic_type: str | None = None


class DatasetContext(ContextModel):
    dataset_id: uuid.UUID
    project_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reported_row_count: int | None = Field(default=None, ge=0)
    fields: list[ContextField] = Field(min_length=1, max_length=1000)


class ArtifactContext(ContextModel):
    artifact_id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str
    byte_size: int = Field(gt=0)


class ContextSnapshot(ContextModel):
    version: Literal["research-context-v1"] = "research-context-v1"
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    workspace_revision: int = Field(ge=0)
    workspace_epoch: int = Field(ge=0)
    session_revision: int = Field(ge=0)
    session_epoch: int = Field(ge=0)
    attachment_revision: int = Field(ge=0)
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    datasets: list[DatasetContext] = Field(default_factory=list, max_length=5)
    artifacts: list[ArtifactContext] = Field(default_factory=list, max_length=5)
