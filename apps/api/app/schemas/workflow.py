"""Pydantic schemas for the workflow endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from app.models.workflow import WorkflowRunStatus
from app.workflow.node_parameters import normalize_node_data


class WorkflowPosition(BaseModel):
    x: float = Field(default=0, allow_inf_nan=False)
    y: float = Field(default=0, allow_inf_nan=False)
    model_config = ConfigDict(extra="forbid")


class WorkflowGraphNode(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    position: WorkflowPosition = Field(default_factory=WorkflowPosition)
    model_config = ConfigDict(extra="forbid")


class WorkflowGraphEdge(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    source_handle: str | None = Field(default=None, max_length=128, alias="sourceHandle")
    target_handle: str | None = Field(default=None, max_length=128, alias="targetHandle")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkflowGraph(BaseModel):
    # V1 documents remain readable; newly authored documents use V2 parameters.
    schema_version: Literal[1, 2] = 1
    nodes: list[WorkflowGraphNode] = Field(default_factory=list, max_length=100)
    edges: list[WorkflowGraphEdge] = Field(default_factory=list, max_length=300)
    model_config = ConfigDict(extra="forbid")


class WorkflowGraphRequest(WorkflowGraph):
    @model_validator(mode="after")
    def validate_provided_parameters(self) -> WorkflowGraphRequest:
        for node in self.nodes:
            try:
                normalize_node_data(node.type, node.data, legacy=self.schema_version == 1)
            except ValidationError as exc:
                # A draft can omit required fields; supplied invalid fields or
                # arbitrary extra parameters are never persisted as valid input.
                if any(error["type"] != "missing" for error in exc.errors(include_input=False)):
                    raise ValueError(f"Node {node.id}: supplied parameters are invalid") from exc
            except ValueError as exc:
                raise ValueError(f"Node {node.id}: supplied parameters are invalid") from exc
        return self


class WorkflowCreateRequest(BaseModel):
    name: str = Field(default="Analysis workflow", max_length=255)
    dataset_id: uuid.UUID | None = None
    graph: WorkflowGraphRequest = Field(default_factory=WorkflowGraphRequest)


class WorkflowUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    dataset_id: uuid.UUID | None = None
    graph: WorkflowGraphRequest | None = None
    # Optimistic concurrency: if provided and != current version → 409.
    expected_version: int | None = None


class WorkflowResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID | None
    name: str
    version: int
    status: str
    graph: WorkflowGraph | None = None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    # None means history was intentionally not read (e.g. corrupt-binding cleanup).
    run_count: int | None = 0
    latest_run_status: str | None = None

    model_config = {"from_attributes": True}


class GraphIssue(BaseModel):
    level: str
    code: str
    message: str
    node_id: str | None = None


class GraphValidationResponse(BaseModel):
    valid: bool
    issues: list[GraphIssue] = Field(default_factory=list)
    topo_order: list[str] = Field(default_factory=list)


class NodeResult(BaseModel):
    status: str
    node_type: str | None = None
    output_kind: str | None = None
    summary: str = ""
    error: str | None = None
    error_code: str | None = None
    reason_code: str | None = None
    seconds: float = 0.0
    output: dict[str, Any] | None = None


class WorkflowRunResponse(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    dataset_id: uuid.UUID | None
    graph_snapshot: dict[str, Any] | None = None
    workflow_version: int | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    software_versions: dict[str, str] = Field(default_factory=dict)
    status: WorkflowRunStatus
    node_results: dict[str, NodeResult] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)
    total_seconds: float | None = None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None = None
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowRunSummary(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    status: WorkflowRunStatus
    total_seconds: float | None = None
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowRunsResponse(BaseModel):
    runs: list[WorkflowRunSummary]
    total: int
