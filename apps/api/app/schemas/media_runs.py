"""Saved local media processing, using the ordinary workspace record store."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MediaRun(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["media_run"] = "media_run"
    title: str
    dataset_id: uuid.UUID
    task_id: uuid.UUID | None = None
    owner_case: dict[str, str] | None = None
    case_archive: str | None = None
    status: Literal["queued", "running", "completed", "failed", "interrupted"] = "queued"
    instance: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    progress: str = ""
    result_ids: list[str] = Field(default_factory=list)
    summary_id: str | None = None
    package_id: str | None = None
    index_id: str | None = None
    artifact_directory: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


class MediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    task_id: uuid.UUID | None = None
    sample_ids: list[str] = Field(default_factory=list)
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, gt=0)
    frames_per_video: int = Field(default=5, ge=1, le=30)
    steps: (
        list[
            Literal[
                "extract_audio",
                "mfcc",
                "rms",
                "frames",
                "resnet",
                "tfidf",
                "minilm",
                "pitch",
                "intensity",
                "formants",
                "speech_rate",
            ]
        ]
        | None
    ) = None
    audio_source: Literal["video", "independent"] = "video"
    sample_rate: int = Field(default=16000, ge=8000, le=192000)
    channels: Literal[1, 2] = 1
    audio_filter: Literal["none", "lowpass", "highpass", "bandpass"] = "none"
    filter_low_hz: float = Field(default=300, gt=0)
    filter_high_hz: float = Field(default=3400, gt=0)
    filter_transition_hz: float = Field(default=100, gt=0)
    window_ms: float = Field(default=25, gt=0, le=10000)
    hop_ms: float = Field(default=10, gt=0, le=10000)
    n_mfcc: int = Field(default=20, ge=1, le=128)
    pitch_method: Literal[
        "raw_autocorrelation",
        "filtered_autocorrelation",
        "raw_cross_correlation",
        "filtered_cross_correlation",
    ] = "raw_autocorrelation"
    pitch_max_number_of_candidates: int = Field(default=15, ge=2, le=100)
    pitch_very_accurate: bool = False
    pitch_attenuation_at_top: float = Field(default=0.03, gt=0, lt=1)
    pitch_silence_threshold: float | None = Field(default=None, ge=0, le=1)
    pitch_voicing_threshold: float | None = Field(default=None, ge=0, le=1)
    pitch_octave_cost: float | None = Field(default=None, ge=0, le=10)
    pitch_octave_jump_cost: float = Field(default=0.35, ge=0, le=10)
    pitch_voiced_unvoiced_cost: float = Field(default=0.14, ge=0, le=10)
    pitch_floor_hz: float = Field(default=75, gt=0, le=1000)
    pitch_ceiling_hz: float = Field(default=600, gt=0, le=2000)
    praat_time_step: float = Field(default=0.01, gt=0, le=1)
    intensity_minimum_pitch_hz: float = Field(default=75, gt=0, le=1000)
    intensity_time_step: float = Field(default=0.01, gt=0, le=1)
    intensity_subtract_mean: bool = True
    formant_ceiling_hz: float = Field(default=5500, gt=0, le=12000)
    formant_window_s: float = Field(default=0.025, gt=0, le=1)
    formant_preemphasis_hz: float = Field(default=50, gt=0, le=5000)
    speech_count: int | None = Field(default=None, ge=0)
    speech_count_unit: Literal["english_words", "chinese_characters"] = "english_words"
    speech_text_scope: Literal["full_media", "selected_range"] = "full_media"
    speech_duration_basis: Literal["audio_selection", "video_duration"] = "audio_selection"
    force: bool = False

    @model_validator(mode="after")
    def valid_range(self):
        filtered = self.pitch_method.startswith("filtered_")
        for key, value in {
            "pitch_silence_threshold": 0.09 if filtered else 0.03,
            "pitch_voicing_threshold": 0.50 if filtered else 0.45,
            "pitch_octave_cost": 0.055 if filtered else 0.01,
        }.items():
            if getattr(self, key) is None:
                setattr(self, key, value)
        if self.end is not None and self.end <= (self.start or 0):
            raise ValueError("结束时间应晚于开始时间。")
        if self.pitch_floor_hz >= self.pitch_ceiling_hz:
            raise ValueError("基频上限应高于下限。")
        if self.audio_filter == "bandpass" and self.filter_low_hz >= self.filter_high_hz:
            raise ValueError("滤波上限应高于下限。")
        return self


class MediaAssociationItem(BaseModel):
    sample_id: str | None = Field(default=None, max_length=255)
    primary_id: uuid.UUID | None = None
    video_id: uuid.UUID | None = None
    audio_id: uuid.UUID | None = None
    pitch_id: uuid.UUID | None = None
    pitch_offset_seconds: float | None = Field(default=None, allow_inf_nan=False)
    text_id: uuid.UUID | None = None
    case_title: str | None = Field(default=None, max_length=255)
    range_member_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    audio_offset_seconds: float | None = Field(default=None, allow_inf_nan=False)
    content_reviewed: bool = False

    @model_validator(mode="after")
    def has_media(self):
        if self.pitch_id is not None and (
            self.audio_id is None or self.pitch_offset_seconds is None
        ):
            raise ValueError("请选择原音轨，并填写 Pitch 时间与音轨时间的偏移。")
        if self.video_id is None and self.audio_id is None and self.primary_id is None:
            raise ValueError("请选择案例的首个材料。")
        if self.primary_id is not None and (self.video_id is not None or self.audio_id is not None):
            raise ValueError("普通案例的首个材料不能与媒体主样本同时指定。")
        if self.audio_offset_seconds is not None and (
            self.video_id is None or self.audio_id is None
        ):
            raise ValueError("时间偏移只适用于视频与独立音频配对。")
        if self.content_reviewed and (self.video_id is None or self.audio_id is None):
            raise ValueError("内容校对只适用于视频与独立音频配对。")
        return self


class MediaAssociation(BaseModel):
    purpose: Literal["legacy", "temporary", "case"] = "legacy"
    owner_case_collection_id: uuid.UUID | None = None
    owner_case_sample_id: str | None = Field(default=None, max_length=255)
    samples: list[MediaAssociationItem] = Field(min_length=1)
    task_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def check_purpose(self):
        if bool(self.owner_case_collection_id) != bool(self.owner_case_sample_id):
            raise ValueError("案例归属信息不完整。")
        if self.owner_case_collection_id and self.purpose != "temporary":
            raise ValueError("案例归属只适用于临时媒体处理。")
        if self.purpose == "case" and (
            len(self.samples) != 1 or not (self.samples[0].case_title or "").strip()
        ):
            raise ValueError("保存案例时请填写案例名称。")
        if self.purpose == "temporary" and any(item.case_title for item in self.samples):
            raise ValueError("临时分析不会保存案例名称。")
        return self
