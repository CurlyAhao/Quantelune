"""Pydantic schemas for project reporting + export."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.project import ProjectStatus


class ProjectSnapshotResponse(BaseModel):
    project_id: uuid.UUID
    title: str
    status: ProjectStatus
    frozen_at: datetime | None = None
    snapshot: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class FreezeRequest(BaseModel):
    pass


class ForkRequest(BaseModel):
    title: str = Field(default="Forked project", max_length=255)


class ExportResponse(BaseModel):
    export_id: str
    format: str = "zip"
    bytes: int
    created_at: str
