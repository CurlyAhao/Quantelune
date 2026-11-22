"""Execution context handed to every rule."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

from app.quality.thresholds import ThresholdConfig


@dataclass
class RuleContext:
    """Everything a rule may need to evaluate.

    ``profile`` is the Week-3 profile result dict (overview / columns /
    missingness / correlations / quality). ``df`` is the (possibly sampled)
    Polars DataFrame for rules that must touch raw values.
    """

    df: pl.DataFrame
    profile: dict[str, Any]
    thresholds: ThresholdConfig
    dataset_sha256: str = ""
    sampled: bool = False
    sample_size: int | None = None
    # Optional OHLCV field mapping (column name per canonical role).
    ohlcv_mapping: dict[str, str] = field(default_factory=dict)

    @property
    def overview(self) -> dict[str, Any]:
        return self.profile.get("overview", {})

    @property
    def columns(self) -> list[dict[str, Any]]:
        return self.profile.get("columns", [])

    @property
    def row_count(self) -> int:
        return int(self.overview.get("row_count", self.df.height))

    @property
    def column_count(self) -> int:
        return int(self.overview.get("column_count", self.df.width))

    def column_by_name(self, name: str) -> dict[str, Any] | None:
        for col in self.columns:
            if col.get("name") == name:
                return col
        return None

    def series(self, name: str) -> pl.Series | None:
        if name in self.df.columns:
            return self.df[name]
        return None
