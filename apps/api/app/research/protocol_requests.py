"""Explicit actor requests; a stored protocol is not execution authority."""

import uuid
from typing import Literal

from pydantic import Field

from app.research.context_contracts import ContextModel
from app.research.protocol_contracts import ResearchProtocolDocument


class ProtocolSave(ContextModel):
    protocol: ResearchProtocolDocument
    expected_revision: int = Field(strict=True, ge=0, le=99)
    idempotency_key: str = Field(strict=True, min_length=1, max_length=200)


class ProtocolActivate(ContextModel):
    version_id: uuid.UUID
    protocol_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    expected_generation: int = Field(strict=True, ge=0, le=199)
    action: Literal["confirm", "revoke"]
    idempotency_key: str = Field(strict=True, min_length=1, max_length=200)
