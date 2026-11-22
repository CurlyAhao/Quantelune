"""Pydantic schemas for Project CRUD."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.project import ProjectStatus, TaskType


class ProjectCreate(BaseModel):
    """Schema for creating a project."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    research_question: str | None = None
    task_type: TaskType | None = None


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    research_question: str | None = None
    task_type: TaskType | None = None
    status: ProjectStatus | None = None


class ProjectResponse(BaseModel):
    """Schema for project responses."""

    id: uuid.UUID
    workspace_path: str | None = None
    title: str
    description: str | None
    research_question: str | None
    task_type: TaskType | None
    status: ProjectStatus
    current_revision: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
