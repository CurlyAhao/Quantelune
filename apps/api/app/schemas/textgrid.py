"""Praat time tiers, separate from thematic coded excerpts."""

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def seconds(value):
    text = str(value)
    try:
        number = Decimal(text)
    except ArithmeticError:
        raise ValueError("时间须填写秒数。") from None
    if not number.is_finite():
        raise ValueError("时间须为有限数值。")
    return text


class GridItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str | None = None
    text: str
    _time = field_validator("start", mode="before")(seconds)

    @field_validator("end", mode="before")
    @classmethod
    def end_time(cls, value):
        return seconds(value) if value is not None else None


class GridTier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["IntervalTier", "TextTier"]
    name: str
    start: str
    end: str
    items: list[GridItem]
    theme_id: uuid.UUID | None = None
    generation: dict[str, Any] | None = None
    _times = field_validator("start", "end", mode="before")(seconds)

    @model_validator(mode="after")
    def ordered(self):
        if Decimal(self.end) <= Decimal(self.start):
            raise ValueError("层的结束时间须晚于开始时间。")
        if self.type == "IntervalTier":
            for index, item in enumerate(self.items):
                if item.end is None or Decimal(item.end) <= Decimal(item.start):
                    raise ValueError("区间结束时间须晚于开始时间。")
                if index and Decimal(self.items[index - 1].end) != Decimal(item.start):
                    raise ValueError("相邻区间必须共用同一边界。")
        else:
            for index, item in enumerate(self.items):
                if item.end is not None:
                    raise ValueError("点标注只有一个时间。")
                if index and Decimal(self.items[index - 1].start) >= Decimal(item.start):
                    raise ValueError("点标注须按时间排列，不能重叠。")
        return self


class TextGridDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["textgrid"] = "textgrid"
    schema_version: Literal[1] = 1
    title: str
    material_id: uuid.UUID
    material_sha256: str
    material_version: int
    duration: str
    start: str
    end: str
    offset: str = "0"
    tiers: list[GridTier]
    imported_text: str
    imported_binary_base64: str | None = None
    _times = field_validator("start", "end", "offset", "duration", mode="before")(seconds)


class TextGridChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    action: Literal[
        "edit",
        "add_tier",
        "rename_tier",
        "move_tier",
        "delete_tier",
        "split_interval",
        "range_interval",
        "merge_previous",
        "merge_next",
        "add_point",
        "delete_point",
        "restore",
    ] = "edit"
    tier: int = Field(default=0, ge=0)
    item: int | None = Field(default=None, ge=0)
    text: str | None = None
    start: str | None = None
    end: str | None = None
    theme_id: uuid.UUID | None = None
    name: str | None = None
    tier_type: Literal["IntervalTier", "TextTier"] | None = None
    position: int | None = Field(default=None, ge=0)
    text_split: int | None = Field(default=None, ge=0)
    clock_shift: str = "0"
    restore_revision: int | None = Field(default=None, ge=1)

    @field_validator("start", "end", "clock_shift", mode="before")
    @classmethod
    def times(cls, value):
        return seconds(value) if value is not None else None


class TextGridCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None


class IntensitySegmentation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    dataset_id: uuid.UUID
    package_id: uuid.UUID | None = None
    sample_id: str | None = None
    record_id: uuid.UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    start: float | None = None
    end: float | None = None
    minimum_pitch: float = Field(default=75, gt=0, le=1000)
    time_step: float = Field(default=0.01, gt=0, le=1)
    subtract_mean: bool = True
    threshold_db: float = Field(default=-25, lt=0)
    minimum_silence: float = Field(default=0.1, ge=0)
    minimum_sounding: float = Field(default=0.05, ge=0)

    @model_validator(mode="after")
    def valid_range(self):
        if (self.start is None) != (self.end is None) or (
            self.start is not None and self.end <= self.start
        ):
            raise ValueError("请填写完整且有效的开始、结束时间。")
        if (self.record_id is None) != (self.revision is None):
            raise ValueError("时间标注版本不完整，请重新打开。")
        return self
