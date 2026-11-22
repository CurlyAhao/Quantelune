"""Execution context and missing-value handling for statistical methods."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from app.statistics.result import SampleInfo
from app.statistics.types import Alternative, MethodId, MissingPolicy


@dataclass(frozen=True)
class MethodConfig:
    """Input configuration for a single statistical method.

    All fields are validated by the request schema before reaching here.
    """

    method_id: MethodId
    variables: tuple[str, ...] = ()
    group_variable: str | None = None
    paired: bool = False
    mu: float = 0.0
    expected_proportions: dict[str, float] | None = None
    alternative: Alternative = Alternative.TWO_SIDED
    confidence_level: float = 0.95
    missing_policy: MissingPolicy = MissingPolicy.LISTWISE_DELETE
    # Regression-specific (ignored by non-regression methods).
    target: str | None = None
    categorical_variables: tuple[str, ...] = ()
    reference_categories: dict[str, str] | None = None
    standardize: bool = False
    alpha: float = 1.0
    l1_ratio: float = 0.5
    evaluation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "method_id": self.method_id.value,
            "variables": list(self.variables),
            "group_variable": self.group_variable,
            "paired": self.paired,
            "mu": self.mu,
            "expected_proportions": self.expected_proportions,
            "alternative": self.alternative.value,
            "confidence_level": self.confidence_level,
            "missing_policy": self.missing_policy.value,
            "target": self.target,
            "categorical_variables": list(self.categorical_variables),
            "reference_categories": self.reference_categories,
            "standardize": self.standardize,
            "alpha": self.alpha,
            "l1_ratio": self.l1_ratio,
        }
        if self.evaluation is not None:
            value["evaluation"] = self.evaluation
        return value


@dataclass
class StatisticalContext:
    """Holds the dataframe and configuration; offers numeric/categorical access."""

    df: pl.DataFrame
    config: MethodConfig
    dataset_sha256: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def columns(self) -> list[str]:
        return list(self.df.columns)

    def has_column(self, name: str) -> bool:
        return name in self.df.columns

    def numeric(self, name: str) -> np.ndarray:
        """Return a column coerced to float64 (non-numeric → NaN), as ndarray."""
        if name not in self.df.columns:
            return np.array([], dtype=np.float64)
        s = self.df.get_column(name)
        if s.dtype == pl.String:
            s = s.cast(pl.Float64, strict=False)
        elif not s.dtype.is_numeric():
            s = s.cast(pl.Float64, strict=False)
        arr = s.cast(pl.Float64, strict=False).to_numpy().astype(np.float64)
        return arr

    def categorical(self, name: str) -> list[Any]:
        """Return a column as a list of python values (None preserved)."""
        if name not in self.df.columns:
            return []
        return self.df.get_column(name).to_list()

    def is_numeric_column(self, name: str) -> bool:
        if name not in self.df.columns:
            return False
        dtype = self.df.get_column(name).dtype
        if dtype.is_numeric():
            return True
        if dtype == pl.String:
            # Heuristic: try casting; numeric if most values parse.
            cast = self.df.get_column(name).cast(pl.Float64, strict=False)
            non_null = cast.is_not_null().sum()
            return non_null > 0
        return False


def apply_missing_policy(
    arrays: dict[str, np.ndarray],
    policy: MissingPolicy,
    *,
    paired: bool = False,
) -> tuple[dict[str, np.ndarray], SampleInfo]:
    """Apply a missing-value policy to a set of aligned arrays.

    Returns the cleaned arrays and a :class:`SampleInfo` describing exactly
    what was excluded and why. Raises ``ValueError`` for ``error_on_missing``
    when any value is missing.
    """
    if not arrays:
        return arrays, SampleInfo(0, 0, 0, "no variables")

    lengths = [len(a) for a in arrays.values()]
    original_n = max(lengths) if lengths else 0

    if policy == MissingPolicy.ERROR_ON_MISSING:
        missing_total = int(sum(int(np.isnan(a).sum()) for a in arrays.values()))
        if missing_total > 0:
            raise ValueError(f"error_on_missing policy: {missing_total} missing value(s) present.")
        per_var = {k: int(np.isnan(a).sum()) for k, a in arrays.items()}
        return arrays, SampleInfo(original_n, 0, original_n, "no missing values", per_var)

    if policy == MissingPolicy.PAIRWISE_DELETE and not paired and len(arrays) >= 1:
        # Each variable keeps its own valid values (used by correlation matrices
        # computed pairwise). For single-variable methods this equals listwise.
        cleaned = {k: a[~np.isnan(a)] for k, a in arrays.items()}
        per_var = {k: int(len(a) - len(cleaned[k])) for k, a in arrays.items()}
        excluded = max(per_var.values()) if per_var else 0
        final_n = max((len(v) for v in cleaned.values()), default=0)
        return cleaned, SampleInfo(
            original_n, excluded, final_n, "pairwise deletion per variable", per_var
        )

    # Listwise deletion: drop any row where ANY involved variable is missing.
    n = min(lengths) if lengths else 0
    mask = np.ones(n, dtype=bool)
    for a in arrays.values():
        mask &= ~np.isnan(a[:n])
    cleaned = {k: a[:n][mask] for k, a in arrays.items()}
    excluded = int(n - mask.sum())
    per_var = {k: int(np.isnan(a[:n]).sum()) for k, a in arrays.items()}
    return cleaned, SampleInfo(original_n, excluded, int(mask.sum()), "listwise deletion", per_var)
