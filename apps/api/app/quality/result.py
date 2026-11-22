"""Structured result objects produced by rules."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.quality.types import Category, RuleStatus, Severity

MAX_EVIDENCE_SAMPLES = 10


def _json_safe(value: Any) -> Any:
    """Recursively convert NaN/Inf to None so results are JSON-serializable."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@dataclass
class RuleResult:
    """The unified, stable output of a single rule evaluation."""

    rule_id: str
    status: RuleStatus
    severity: Severity
    category: Category
    title: str
    summary: str
    affected_columns: list[str] = field(default_factory=list)
    affected_count: int = 0
    affected_ratio: float = 0.0
    threshold: dict[str, Any] | None = None
    observed_value: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: str = ""
    rule_version: str = "1"
    rule_name: str = ""
    description: str = ""
    limitations: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "rule_id": self.rule_id,
                "rule_name": self.rule_name,
                "description": self.description,
                "status": self.status.value,
                "severity": self.severity.value,
                "category": self.category.value,
                "title": self.title,
                "summary": self.summary,
                "affected_columns": self.affected_columns,
                "affected_count": self.affected_count,
                "affected_ratio": self.affected_ratio,
                "threshold": self.threshold,
                "observed_value": self.observed_value,
                "evidence": self.evidence,
                "remediation": self.remediation,
                "rule_version": self.rule_version,
                "limitations": self.limitations,
                "created_at": self.created_at,
            }
        )


def make_evidence(
    *,
    samples: list[Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded evidence payload (capped sample list)."""
    evidence: dict[str, Any] = {}
    if samples is not None:
        capped = samples[:MAX_EVIDENCE_SAMPLES]
        evidence["samples"] = [str(s) for s in capped]
        evidence["sample_count_shown"] = len(capped)
    if extra:
        evidence.update(extra)
    return evidence
