"""Requests for viewing native candidates and saving a new selected pitch path."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PitchBrowse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sample_id: str
    point: float | None = None
    frame_index: int | None = Field(default=None, ge=1)
    start: float | None = None
    end: float | None = None

    @model_validator(mode="after")
    def ordered(self):
        if (self.start is None) != (self.end is None) or (
            self.start is not None and self.end <= self.start
        ):
            raise ValueError("结束时间应晚于开始时间。")
        return self


class PitchChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_index: int = Field(ge=1)
    candidate_index: int = Field(ge=0)


class PitchPathParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    silence_threshold: float = Field(ge=0, le=1)
    voicing_threshold: float = Field(ge=0, le=1)
    octave_cost: float = Field(ge=0)
    octave_jump_cost: float = Field(ge=0)
    voiced_unvoiced_cost: float = Field(ge=0)
    ceiling: float = Field(gt=0)


class PitchRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str
    changes: list[PitchChoice] = Field(default_factory=list)
    action: Literal["edit", "undo", "reselect"] = "edit"
    parameters: PitchPathParameters | None = None

    @model_validator(mode="after")
    def choices(self):
        if self.action == "edit" and not self.changes:
            raise ValueError("请先选择一个候选或无声。")
        if self.action != "edit" and self.changes:
            raise ValueError("请先保存手工选择，再执行重选或撤销。")
        if (self.action == "reselect") != (self.parameters is not None):
            raise ValueError("重选路径需要本次采用的阈值和代价。")
        if len({item.frame_index for item in self.changes}) != len(self.changes):
            raise ValueError("同一帧只能选择一个候选。")
        return self
