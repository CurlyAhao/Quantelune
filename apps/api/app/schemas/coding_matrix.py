"""A view of existing, versioned coding evidence."""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.material import Strict


class MatrixRequest(Strict):
    revision: int = Field(ge=0)
    row_kind: Literal["case", "file"] = "case"
    theme_ids: list[uuid.UUID] | None = None
    case_key: str | None = None
