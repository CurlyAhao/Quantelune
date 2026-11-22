"""Unified, JSON-safe result structure for a statistical method."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.statistics.types import MethodStatus


def json_safe(value: Any) -> Any:
    """Recursively convert NaN / ±Inf to ``None`` so results serialise cleanly.

    Never raises on unexpected types — falls back to ``str``.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    # numpy scalars, decimals, etc.
    try:
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return str(value)


@dataclass
class AssumptionCheck:
    """A single deterministic assumption / applicability check."""

    name: str
    passed: bool
    detail: str
    value: Any = None
    threshold: Any = None

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "name": self.name,
                "passed": self.passed,
                "detail": self.detail,
                "value": self.value,
                "threshold": self.threshold,
            }
        )


@dataclass
class SampleInfo:
    """Missing-value accounting — makes exclusions explicit, never silent."""

    original_n: int
    excluded_n: int
    final_n: int
    reason: str = ""
    per_variable: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "original_n": self.original_n,
                "excluded_n": self.excluded_n,
                "final_n": self.final_n,
                "reason": self.reason,
                "per_variable": self.per_variable,
            }
        )


@dataclass
class StatisticalMethodResult:
    """The unified result every method must return."""

    method: str
    method_version: str = "1.0.0"
    status: MethodStatus = MethodStatus.COMPLETED
    sample: SampleInfo | None = None
    estimates: dict[str, Any] = field(default_factory=dict)
    statistic: dict[str, Any] = field(default_factory=dict)
    p_value: float | None = None
    alternative: str = "two_sided"
    confidence_level: float = 0.95
    confidence_interval: dict[str, Any] = field(default_factory=dict)
    effect_size: dict[str, Any] = field(default_factory=dict)
    descriptive_statistics: dict[str, Any] = field(default_factory=dict)
    assumption_checks: list[AssumptionCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    software_versions: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def add_check(self, check: AssumptionCheck) -> None:
        self.assumption_checks.append(check)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "method": self.method,
                "method_version": self.method_version,
                "status": self.status.value,
                "sample": self.sample.to_dict() if self.sample else None,
                "estimates": self.estimates,
                "statistic": self.statistic,
                "p_value": self.p_value,
                "alternative": self.alternative,
                "confidence_level": self.confidence_level,
                "confidence_interval": self.confidence_interval,
                "effect_size": self.effect_size,
                "descriptive_statistics": self.descriptive_statistics,
                "assumption_checks": [c.to_dict() for c in self.assumption_checks],
                "warnings": self.warnings,
                "limitations": self.limitations,
                "software_versions": self.software_versions,
                "diagnostics": self.diagnostics,
                "error_code": self.error_code,
                "error_message": self.error_message,
            }
        )
