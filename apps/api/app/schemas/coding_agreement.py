import uuid
from typing import Literal

from pydantic import Field

from app.schemas.material import Strict


class AgreementRequest(Strict):
    modality: Literal["text", "image", "audio", "video"] = "text"
    revision: int = Field(ge=0)
    coder_a: str = Field(min_length=1, max_length=255)
    coder_b: str = Field(min_length=1, max_length=255)
    theme_id: uuid.UUID
    material_id: uuid.UUID | None = None
    case_key: str | None = None
    page: int = Field(default=0, ge=0)
