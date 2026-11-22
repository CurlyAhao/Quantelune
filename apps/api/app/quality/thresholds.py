"""Centralized, validated threshold configuration for the rule engine.

All tunable rule parameters live here — no scattered hard-coded magic numbers.
A ``ThresholdConfig`` carries the defaults; callers may produce an overridden
copy for a single run via :meth:`ThresholdConfig.with_overrides`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

# ── Default values ───────────────────────────────────────────────────────────

DEFAULTS: dict[str, float] = {
    # Missingness
    "high_missingness": 0.80,
    "warning_missingness": 0.30,
    "high_overall_missingness": 0.30,
    # Uniqueness / duplication
    "duplicate_row_rate": 0.05,
    "near_constant_ratio": 0.95,
    "high_cardinality_ratio": 0.90,
    "identifier_ratio": 0.98,
    # Validity
    "mixed_type_ratio": 0.05,
    "invalid_datetime_ratio": 0.05,
    "non_finite_ratio": 0.0,  # any non-finite value is flagged
    # Distribution
    "outlier_ratio": 0.10,
    # Structure
    "max_columns": 500,
    # Temporal
    "irregular_interval_ratio": 0.10,
    "duplicate_timestamp_ratio": 0.05,
    # Numeric range (optional; only enforced when a rule is given explicit bounds)
    "numeric_min": 0.0,
    "numeric_max": 0.0,
    # OHLCV / domain
    "extreme_return_ratio": 0.50,  # |return| above this is flagged (informational)
    "zero_volume_streak": 3,  # consecutive zero-volume bars
    # Sampling / resource limits
    "max_sample_values": 5,
    "max_top_values": 20,
    "max_findings": 500,
}

# Keys that are counts/integers rather than ratios.
INTEGER_KEYS = {
    "max_columns",
    "zero_volume_streak",
    "max_sample_values",
    "max_top_values",
    "max_findings",
}


class ThresholdValidationError(ValueError):
    """Raised when a threshold override is out of range or unknown."""


@dataclass(frozen=True)
class ThresholdConfig:
    """Immutable bag of rule thresholds."""

    high_missingness: float = DEFAULTS["high_missingness"]
    warning_missingness: float = DEFAULTS["warning_missingness"]
    high_overall_missingness: float = DEFAULTS["high_overall_missingness"]
    duplicate_row_rate: float = DEFAULTS["duplicate_row_rate"]
    near_constant_ratio: float = DEFAULTS["near_constant_ratio"]
    high_cardinality_ratio: float = DEFAULTS["high_cardinality_ratio"]
    identifier_ratio: float = DEFAULTS["identifier_ratio"]
    mixed_type_ratio: float = DEFAULTS["mixed_type_ratio"]
    invalid_datetime_ratio: float = DEFAULTS["invalid_datetime_ratio"]
    non_finite_ratio: float = DEFAULTS["non_finite_ratio"]
    outlier_ratio: float = DEFAULTS["outlier_ratio"]
    max_columns: int = int(DEFAULTS["max_columns"])
    irregular_interval_ratio: float = DEFAULTS["irregular_interval_ratio"]
    duplicate_timestamp_ratio: float = DEFAULTS["duplicate_timestamp_ratio"]
    numeric_min: float = DEFAULTS["numeric_min"]
    numeric_max: float = DEFAULTS["numeric_max"]
    extreme_return_ratio: float = DEFAULTS["extreme_return_ratio"]
    zero_volume_streak: int = int(DEFAULTS["zero_volume_streak"])
    max_sample_values: int = int(DEFAULTS["max_sample_values"])
    max_top_values: int = int(DEFAULTS["max_top_values"])
    max_findings: int = int(DEFAULTS["max_findings"])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_overrides(self, overrides: dict[str, Any] | None) -> ThresholdConfig:
        """Return a new config with validated overrides applied.

        Raises ``ThresholdValidationError`` on unknown keys or out-of-range
        values. Ratio thresholds must lie in [0, 1]; integer thresholds must be
        positive.
        """
        if not overrides:
            return self

        current = asdict(self)
        known = set(current.keys())

        for key, value in overrides.items():
            if key not in known:
                raise ThresholdValidationError(f"Unknown threshold '{key}'")
            try:
                num = float(value)
            except (TypeError, ValueError) as exc:
                raise ThresholdValidationError(
                    f"Threshold '{key}' must be numeric, got {value!r}"
                ) from exc

            if key in INTEGER_KEYS:
                if num < 0 or not float(num).is_integer():
                    raise ThresholdValidationError(
                        f"Threshold '{key}' must be a non-negative integer, got {value!r}"
                    )
                current[key] = int(num)
            else:
                if not 0.0 <= num <= 1.0:
                    raise ThresholdValidationError(
                        f"Threshold '{key}' must be between 0 and 1, got {value!r}"
                    )
                current[key] = num

        return ThresholdConfig(**current)


def default_config() -> ThresholdConfig:
    return ThresholdConfig()


def all_field_names() -> list[str]:
    return [f.name for f in fields(ThresholdConfig)]
