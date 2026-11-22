"""Explicit source selection for an immutable material version."""

from typing import Literal

from pydantic import Field

from app.schemas.material import Strict


class MaterialSourceCheck(Strict):
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MaterialSourceUpdate(MaterialSourceCheck):
    comparison_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["version", "separate", "restore"]
