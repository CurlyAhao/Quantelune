"""Finite, local data preparation requests; no expression or code execution."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Preparation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: Literal["missing", "units", "dates", "join", "rename", "cast", "trim", "deduplicate"]
    column: str = Field(default="", max_length=255)
    rule: Literal["keep", "median", "mean", "value", "drop"] = "keep"
    value: str = Field(default="", max_length=1000)
    target: str = Field(default="", max_length=255)
    right_dataset_id: uuid.UUID | None = None
    right_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    right_key: str = Field(default="", max_length=255)
    duplicate: Literal["block", "first", "last"] = "block"
    result_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
