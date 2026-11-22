"""Saved spectra of a bounded source-waveform selection."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpectrumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["cursor", "selection", "spectrogram"] = "cursor"
    point: float | None = None
    start: float | None = None
    end: float | None = None
    window_ms: float = Field(default=25, gt=0, le=10000)
    max_frequency_hz: float = Field(default=5000, gt=0)
    time_step_ms: float = Field(default=2, gt=0)
    frequency_step_hz: float = Field(default=20, gt=0)
    package_id: uuid.UUID | None = None
    sample_id: str | None = None

    @model_validator(mode="after")
    def range_required(self):
        if self.mode == "cursor" and self.point is None:
            raise ValueError("请先选择时间光标。")
        if self.mode != "cursor" and (
            self.start is None or self.end is None or self.end <= self.start
        ):
            raise ValueError("请选择起止时间有效的区间。")
        if bool(self.package_id) != bool(self.sample_id):
            raise ValueError("请重新打开当前声音结果。")
        return self


class SpectralQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time: float | None = None
    frequency: float | None = Field(default=None, ge=0)
    lower_hz: float | None = Field(default=None, ge=0)
    upper_hz: float | None = Field(default=None, gt=0)
    save: bool = False


class SpectrumRun(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    kind: Literal["audio_spectrum"] = "audio_spectrum"
    title: str
    source_dataset_id: uuid.UUID
    source_sha256: str
    context_dataset_id: uuid.UUID
    parameters: dict[str, Any]
    started_at: str
    status: Literal["running", "completed", "failed"] = "running"
    method: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: dict[str, str] = Field(default_factory=dict)
    artifact_directory: str
    error_message: str | None = None
