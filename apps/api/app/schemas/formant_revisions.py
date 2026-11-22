"""Parameters shared by the saved FormantPath comparison and revision actions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FormantPathParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    middle_ceiling_hz: float = Field(default=5000, gt=0)
    ceiling_step: float = Field(default=0.05, gt=0, le=1)
    steps_each_side: int = Field(default=2, ge=0, le=10)
    time_step_s: float = Field(default=0.005, gt=0, le=1)
    window_s: float = Field(default=0.025, gt=0, le=1)
    preemphasis_hz: float = Field(default=50, ge=0)


class FormantBrowse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sample_id: str
    start: float | None = None
    end: float | None = None
    point: float | None = None
    frame_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if (self.start is None) != (self.end is None) or (
            self.start is not None and self.end <= self.start
        ):
            raise ValueError("结束时间应晚于开始时间。")
        return self


class FormantRevision(FormantBrowse):
    action: Literal["generate", "apply", "auto"]
    parameters: FormantPathParameters | None = None
    candidate_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def action_values(self):
        if self.action == "generate" and self.parameters is None:
            raise ValueError("请填写候选上限和测量参数。")
        if self.action == "apply" and (self.candidate_index is None or self.start is None):
            raise ValueError("请先选择时间范围和候选。")
        return self
