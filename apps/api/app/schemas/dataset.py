"""Pydantic schemas for Dataset."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.dataset import SemanticType, UploadStatus, VariableRole


class DatasetResponse(BaseModel):
    """Schema for dataset responses."""

    id: uuid.UUID
    multimodal: dict[str, Any] | None = None
    raw_file: bool = False
    signal_session: bool = False
    media_case: dict[str, Any] | None = None
    material_id: str | None = None
    visual_result: dict[str, Any] | None = None
    media_result: dict[str, Any] | None = None
    transcript_pair: dict[str, Any] | None = None
    transcription: dict[str, Any] | None = None
    preparation_operation: str | None = None
    preparation_change: str | None = None
    coding_summary: dict[str, Any] | None = None
    coding_matrix: dict[str, Any] | None = None
    coding_agreement: dict[str, Any] | None = None
    local_file: bool = False
    source_dataset_id: str | None = None
    prepared_source_sha256: str | None = None
    project_id: uuid.UUID
    original_filename: str
    safe_filename: str
    file_format: str
    mime_type: str
    file_size: int
    sha256: str
    row_count: int | None
    column_count: int | None
    upload_status: UploadStatus
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    validated_at: datetime | None = None
    sheet_name: str | None = None
    csv_encoding: str | None = None
    source_path: str | None = None
    display_name: str | None = None
    preparation_parent_id: str | None = None
    preparation_steps: int = 0
    source_version: int = 1
    source_parent_id: str | None = None
    source_root_id: str | None = None

    model_config = {"from_attributes": True}


class DatasetPreviewResponse(BaseModel):
    """Schema for dataset preview responses."""

    dataset_id: uuid.UUID
    original_filename: str
    file_format: str
    row_count: int | None
    column_count: int | None
    upload_status: str
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    showing_rows: int
    total_rows: int


class VariableResponse(BaseModel):
    """Schema for variable responses."""

    id: uuid.UUID
    dataset_id: uuid.UUID
    name: str
    display_name: str | None
    data_type: str
    semantic_type: SemanticType | None
    role: VariableRole
    missing_rate: float | None
    unique_count: int | None
    nullable: bool | None = None

    model_config = {"from_attributes": True}
