"""Finite edits to a saved rendered figure, without data or calculation changes."""

from pydantic import BaseModel, ConfigDict, Field


class PlotStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    colors: dict[str, str] = Field(default_factory=dict, max_length=32)
    font_family: str | None = Field(default=None, min_length=1, max_length=100)
    font_size: float | None = Field(default=None, ge=6, le=48)
    line_width: float | None = Field(default=None, ge=0.2, le=12)
    bar_width_scale: float | None = Field(default=None, gt=0, le=1)
    width_inches: float | None = Field(default=None, ge=2, le=24)
    height_inches: float | None = Field(default=None, ge=2, le=24)
    title: str | None = Field(default=None, max_length=200)
    xlabel: str | None = Field(default=None, max_length=200)
    ylabel: str | None = Field(default=None, max_length=200)
    grid: bool | None = None
    tight_layout: bool = False
    dpi: int | None = Field(default=None, ge=72, le=600)
