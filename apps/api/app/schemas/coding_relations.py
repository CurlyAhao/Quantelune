"""Text relations use the same saved coding scope as evidence and summaries."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.material import Strict


class RelationsRequest(Strict):
    revision: int = Field(ge=0)
    case_collection_id: uuid.UUID | None = None
    case_sample_id: str | None = None
    coder_id: str | None = Field(default=None, max_length=255)
    theme_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    pair: list[uuid.UUID] | None = Field(default=None, min_length=2, max_length=2)
    relation: Literal["all", "equal", "contains", "overlap", "touching", "separate"] = "all"
    page: int = Field(default=0, ge=0)
