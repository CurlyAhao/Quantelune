"""Table-backed attributes and the applied coding scope."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.material import Strict


class AttributeColumn(Strict):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["number", "text"]


class AttributeBindingRequest(Strict):
    id: uuid.UUID | None = None
    dataset_id: uuid.UUID
    target: Literal["case", "file"]
    key_column: str
    key_kind: Literal["sample_id", "file_id", "relative_path"]
    group_id: str | None = None
    columns: list[AttributeColumn] = Field(min_length=1, max_length=50)
    file_choices: dict[str, str] = Field(default_factory=dict)
    case_choices: dict[str, str] = Field(default_factory=dict)


class AttributeRow(Strict):
    key: str
    title: str
    values: dict[str, str | float | None]
    sha256: str | None = None
    version: int | None = None


class AttributeBinding(Strict):
    id: uuid.UUID
    source_id: uuid.UUID
    source_sha256: str
    source_name: str
    request: AttributeBindingRequest
    rows: list[AttributeRow]


class AttributeCondition(Strict):
    binding_id: uuid.UUID
    column: str
    operator: Literal["eq", "gte", "lte", "contains", "starts_with"] = "eq"
    value: str | float


class AttributeFilter(Strict):
    mode: Literal["all", "any"] = "all"
    conditions: list[AttributeCondition] = Field(default_factory=list, max_length=20)
