"""Locations are explicit, bounded and expressed in Unicode code points, never UTF-16."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextLocation(Strict):
    kind: Literal["text"]
    page: int = Field(ge=1, le=50)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    quote: str = Field(min_length=1, max_length=2000)


class RegionLocation(Strict):
    kind: Literal["region"]
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Region outside image.")
        return self


class TranscriptionLocation(Strict):
    run_id: uuid.UUID
    text_id: uuid.UUID
    version: int = Field(ge=1)
    segment_ids: list[int] = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=2000)


class TimeLocation(Strict):
    kind: Literal["time"]
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)
    point: bool = False
    note: str = Field(default="", max_length=1000)
    transcription: TranscriptionLocation | None = None


class CitationRequest(Strict):
    material_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    material_version: int = Field(ge=1)
    reviewed: bool = False
    expected_revision: int | None = Field(default=None, ge=1)
    locator: Annotated[TextLocation | RegionLocation | TimeLocation, Field(discriminator="kind")]


class CitationReference(Strict):
    citation_id: uuid.UUID
    revision: int = Field(ge=1)
    citation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
