"""Immutable manual-analysis requests and observed execution states."""

import uuid
from typing import Any, Literal

from pydantic import Field

from app.schemas.material import Strict


class RunEvent(Strict):
    phase: Literal[
        "queued",
        "prepare",
        "compute",
        "publish",
        "completed",
        "not_applicable",
        "failed",
        "cancel_requested",
        "cancelled",
        "interrupted",
    ]
    at: str


class AnalysisJob(Strict):
    schema_version: Literal[1] = 1
    kind: Literal["analysis_run"] = "analysis_run"
    title: str = Field(min_length=1, max_length=160)
    operation: Literal["statistics", "ml"]
    request_id: uuid.UUID
    instance: uuid.UUID
    dataset_id: uuid.UUID
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sheet_name: str | None = None
    csv_encoding: str | None = None
    experiment_id: uuid.UUID | None = None
    configuration: dict[str, Any]
    native_id: uuid.UUID | None = None
    status: Literal[
        "queued",
        "running",
        "cancel_requested",
        "completed",
        "not_applicable",
        "failed",
        "cancelled",
        "interrupted",
    ] = "queued"
    phase: str = "queued"
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    events: list[RunEvent] = Field(default_factory=list, max_length=30)
    error_code: str | None = None
    error_message: str | None = None
