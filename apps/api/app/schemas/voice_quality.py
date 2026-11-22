"""Voice measurements on an explicit original-waveform range."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VoiceQualityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    pitch_floor: float = Field(default=100, ge=20, le=2000)
    pitch_ceiling: float = Field(default=650, ge=50, le=5000)
    time_step: float = Field(default=0.01, gt=0, le=1)
    period_floor: float = Field(default=0.0001, gt=0, le=1)
    period_ceiling: float = Field(default=0.02, gt=0, le=1)
    maximum_period_factor: float = Field(default=1.3, ge=1, le=10)
    maximum_amplitude_factor: float = Field(default=1.6, ge=1, le=10)
    package_id: uuid.UUID | None = None
    sample_id: str | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("请选择起止时间有效的区间。")
        if self.pitch_ceiling <= self.pitch_floor or self.period_ceiling <= self.period_floor:
            raise ValueError("上限必须大于下限。")
        if bool(self.package_id) != bool(self.sample_id):
            raise ValueError("请重新打开当前声音结果。")
        return self


class VoiceQualityRun(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    kind: Literal["voice_quality"] = "voice_quality"
    title: str
    source_dataset_id: uuid.UUID
    source_sha256: str
    context_dataset_id: uuid.UUID
    parameters: dict[str, Any]
    started_at: str
    status: Literal["running", "completed", "failed"] = "running"
    method: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: dict[str, str] = Field(default_factory=dict)
    artifact_directory: str
    error_message: str | None = None
