"""Project text coding; excerpts keep their original Unicode source locations."""

import uuid
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.coding_attributes import AttributeBinding, AttributeBindingRequest, AttributeFilter
from app.schemas.material import RegionLocation, Strict, TextLocation, TimeLocation

Color = Literal["green", "blue", "amber", "purple", "rose", "neutral"]


class Theme(Strict):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=80)
    color: Color = "green"
    color_rgb: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_codable: bool = True
    merged_ids: list[uuid.UUID] = Field(default_factory=list)
    parent_id: uuid.UUID | None = None
    memo: str = Field(default="", max_length=2000)


class SignalLocation(Strict):
    kind: Literal["signal_time"]
    start_seconds: float = Field(allow_inf_nan=False)
    end_seconds: float = Field(allow_inf_nan=False)
    channel_ids: list[str] = Field(default_factory=list)
    window_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("结束时间必须晚于开始时间。")
        return self


CodingLocation = Annotated[
    TextLocation | RegionLocation | TimeLocation | SignalLocation, Field(discriminator="kind")
]


class CodedSegment(Strict):
    id: uuid.UUID
    coder_id: str | None = Field(default=None, max_length=255)
    coder_name: str | None = Field(default=None, max_length=255)
    origin: Literal["manual", "ai_accepted", "mixed", "legacy_ai"] | None = None
    sample_id: str | None = Field(default=None, max_length=255)
    sample_title: str | None = Field(default=None, max_length=255)
    sample_collection_id: uuid.UUID | None = None
    theme_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    memo: str = Field(default="", max_length=2000)
    citation_id: uuid.UUID | None = None
    citation_revision: int | None = Field(default=None, ge=1)
    citation_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    material_id: uuid.UUID | None = None
    material_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    material_version: int | None = Field(default=None, ge=1)
    dataset_id: uuid.UUID | None = None
    dataset_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    task_id: uuid.UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    locator: CodingLocation


class LocalCoder(Strict):
    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=80)


class CodingDocument(Strict):
    ai_question: str = Field(default="", max_length=4000)
    schema_version: Literal[1] = 1
    kind: Literal["coding"] = "coding"
    title: str = "文本编码"
    themes: list[Theme] = Field(default_factory=list, max_length=500)
    segments: list[CodedSegment] = Field(default_factory=list, max_length=2000)
    undo_stack: list[int] = Field(default_factory=list, max_length=100)
    attribute_bindings: list[AttributeBinding] = Field(default_factory=list)
    attribute_filter: AttributeFilter = Field(default_factory=AttributeFilter)
    local_coders: list[LocalCoder] = Field(default_factory=list, max_length=100)
    active_coder_id: str | None = None

    @model_validator(mode="after")
    def graph(self):
        themes = {item.id: item for item in self.themes}
        if len(themes) != len(self.themes) or len({s.id for s in self.segments}) != len(
            self.segments
        ):
            raise ValueError("Duplicate coding identity.")
        for theme in self.themes:
            if not theme.name.strip():
                raise ValueError("Theme name is required.")
            visited = {theme.id}
            parent = theme.parent_id
            while parent is not None:
                if parent in visited or parent not in themes:
                    raise ValueError("Invalid theme hierarchy.")
                visited.add(parent)
                parent = themes[parent].parent_id
        for segment in self.segments:
            if (
                (not segment.theme_ids and not segment.memo.strip())
                or len(set(segment.theme_ids)) != len(segment.theme_ids)
                or not set(segment.theme_ids) <= themes.keys()
                or any(
                    not themes[value].is_codable for value in segment.theme_ids if value in themes
                )
            ):
                raise ValueError("A segment needs existing, distinct themes.")
        return self


class CodingChange(Strict):
    revision: int = Field(ge=0)
    operation: Literal[
        "add_coder",
        "select_coder",
        "question",
        "bind_attributes",
        "filter_attributes",
        "add_theme",
        "edit_theme",
        "merge_theme",
        "delete_theme",
        "move_theme",
        "import_codebook",
        "add_segment",
        "edit_segment",
        "remove_segment",
        "undo",
    ]
    theme_id: uuid.UUID | None = None
    coder_id: str | None = Field(default=None, max_length=255)
    attribute_binding: AttributeBindingRequest | None = None
    attribute_filter: AttributeFilter | None = None
    question: str = Field(default="", max_length=4000)
    target_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    name: str = Field(default="", max_length=80)
    color: Color = "green"
    color_rgb: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_codable: bool | None = None
    direction: Literal["up", "down"] | None = None
    codebook_xml: str | None = None
    memo: str = Field(default="", max_length=2000)
    segment_id: uuid.UUID | None = None
    theme_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    material_id: uuid.UUID | None = None
    material_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    material_version: int | None = Field(default=None, ge=1)
    locator: CodingLocation | None = None
    dataset_id: uuid.UUID | None = None
    dataset_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    task_id: uuid.UUID | None = None
    sample_id: str | None = Field(default=None, max_length=255)
    sample_collection_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
