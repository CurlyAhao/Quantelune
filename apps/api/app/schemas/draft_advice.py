"""Editable model proposals; no model-supplied execution status or source identity."""

import uuid
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.coding import CodingLocation, Color
from app.schemas.material import Strict, TextLocation

SHA = r"^[a-f0-9]{64}$"


class PlotAdvice(Strict):
    kind: Literal["plot"] = "plot"
    source_version: str = Field(pattern=SHA)
    title: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=32000)


class ProposedTheme(Strict):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    color: Color = "green"
    memo: str = Field(default="", max_length=1000)


class ProposedSegment(Strict):
    source_key: str = Field(min_length=1, max_length=80)
    start: int = Field(ge=0, le=262144, strict=True)
    end: int = Field(gt=0, le=262144, strict=True)
    quote: str = Field(min_length=1, max_length=2000)
    theme_keys: list[str] = Field(min_length=1, max_length=10)
    memo: str = Field(default="", max_length=1000)


class CodingAdvice(Strict):
    kind: Literal["coding"] = "coding"
    source_version: str = Field(pattern=SHA)
    themes: list[ProposedTheme] = Field(default_factory=list, max_length=20)
    segments: list[ProposedSegment] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def distinct(self):
        if len({theme.key for theme in self.themes}) != len(self.themes):
            raise ValueError("Duplicate proposed theme key.")
        names = [theme.name.strip().casefold() for theme in self.themes]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("Duplicate or empty proposed theme name.")
        seen = set()
        for segment in self.segments:
            key = (segment.source_key, segment.start, segment.end)
            if (
                segment.end <= segment.start
                or len(set(segment.theme_keys)) != len(segment.theme_keys)
                or key in seen
            ):
                raise ValueError("Invalid or duplicate proposed segment.")
            seen.add(key)
        return self


class AdviceTextSource(Strict):
    """One explicitly selected Unicode slice; offsets stay in the original page."""

    key: str = Field(min_length=1, max_length=80)
    material_id: uuid.UUID
    material_version: int = Field(ge=1)
    material_sha256: str = Field(pattern=SHA)
    text_sha256: str = Field(pattern=SHA)
    page: int = Field(ge=1, le=50)
    start: int = Field(ge=0, le=262144, strict=True)
    text: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def bounds(self):
        if self.start + len(self.text) > 262144:
            raise ValueError("Text slice exceeds page bounds.")
        return self


class PlotAdviceContextRequest(Strict):
    revision: int = Field(ge=1, strict=True)


class CodingTextSelection(Strict):
    material_id: uuid.UUID
    material_sha256: str = Field(pattern=SHA)
    material_version: int = Field(ge=1, strict=True)
    locator: TextLocation


class CodingAdviceContextRequest(Strict):
    revision: int = Field(ge=0, strict=True)
    selections: list[CodingTextSelection] = Field(min_length=1, max_length=5)


class PlotAdviceTarget(PlotAdviceContextRequest):
    kind: Literal["plot"]
    draft_id: uuid.UUID


class CodingAdviceTarget(CodingAdviceContextRequest):
    kind: Literal["coding"]


class SelectionTarget(Strict):
    kind: Literal["selection"] = "selection"
    revision: int = Field(ge=0)
    material_id: uuid.UUID
    material_sha256: str = Field(pattern=SHA)
    material_version: int = Field(ge=1)
    locator: CodingLocation
    task_id: uuid.UUID | None = None
    purpose: str = Field(min_length=1, max_length=500)
    action: Literal["themes", "summary"]
    annotation_id: uuid.UUID | None = None
    share_selection: Literal[True]
    share_previous_context: bool = False


class SelectedTheme(Strict):
    theme_id: str = Field(max_length=80)
    reason: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=500)


class SelectionProposal(Strict):
    source_version: str = Field(pattern=SHA)
    suggestions: list[SelectedTheme] = Field(default_factory=list, max_length=2)
    summary: str = Field(default="", max_length=600)
    evidence: str = Field(default="", max_length=500)


class AdviceCreate(Strict):
    request_id: uuid.UUID
    source_version: str = Field(pattern=SHA)
    question: str = Field(min_length=1, max_length=4000)
    target: Annotated[
        PlotAdviceTarget | CodingAdviceTarget | SelectionTarget, Field(discriminator="kind")
    ]


class AdviceSend(Strict):
    approval_id: uuid.UUID
    request_hash: str = Field(pattern=SHA)


class AdviceAccept(Strict):
    source_version: str = Field(pattern=SHA)
    proposal: Annotated[PlotAdvice | CodingAdvice, Field(discriminator="kind")] | None = None
