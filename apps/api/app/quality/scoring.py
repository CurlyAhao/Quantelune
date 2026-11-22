"""Deterministic quality scoring derived from rule findings.

The score is an *aid*, not an absolute standard. The same data, configuration
and ruleset version always produce the same score.

Model
-----
Six dimensions (completeness, validity, uniqueness, consistency, temporal,
usability), each scored 0–100 and combined with fixed weights that sum to 1.

For each dimension we start at 100 and subtract penalties. Findings are grouped
by ``rule_id`` so a single rule firing on many columns cannot deduct without
bound. Per rule::

    coverage    = min(1.0, sum of affected_ratio across that rule's findings)
    rule_penalty = SEVERITY_WEIGHT[max_severity] * (0.5 + 0.5 * coverage)
    dimension   -= rule_penalty          (floor 0)

``total = sum(dimension_score * dimension_weight)`` (floor 0, ceiling 100).
"""

from __future__ import annotations

from typing import Any

from app.quality.result import RuleResult
from app.quality.types import (
    CATEGORY_TO_DIMENSION,
    DIMENSION_WEIGHTS,
    SEVERITY_ORDER,
    SEVERITY_WEIGHT,
    Dimension,
    RuleStatus,
)

# Statuses that incur a penalty.
PENALIZED_STATUSES = {RuleStatus.WARNING, RuleStatus.FAILED}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def compute_score(results: list[RuleResult]) -> dict[str, Any]:
    """Compute the overall + per-dimension quality score from findings."""
    # Bucket penalized findings by dimension → rule_id → list of results.
    by_dim_rule: dict[Dimension, dict[str, list[RuleResult]]] = {dim: {} for dim in Dimension}
    for r in results:
        if r.status not in PENALIZED_STATUSES:
            continue
        dim = CATEGORY_TO_DIMENSION.get(r.category)
        if dim is None:
            continue
        by_dim_rule[dim].setdefault(r.rule_id, []).append(r)

    dimensions: dict[str, dict[str, Any]] = {}
    deductions: list[dict[str, Any]] = []

    for dim in Dimension:
        score = 100.0
        issues: list[str] = []
        for rule_id, findings in by_dim_rule[dim].items():
            max_sev = max(findings, key=lambda f: SEVERITY_ORDER[f.severity]).severity
            coverage = min(1.0, sum(float(f.affected_ratio or 0.0) for f in findings))
            penalty = SEVERITY_WEIGHT[max_sev] * (0.5 + 0.5 * coverage)
            score -= penalty
            issues.append(
                f"{rule_id} ({max_sev.value}, {len(findings)} finding(s), "
                f"coverage {coverage:.0%}): -{penalty:.1f}"
            )
            deductions.append(
                {
                    "dimension": dim.value,
                    "rule_id": rule_id,
                    "severity": max_sev.value,
                    "finding_count": len(findings),
                    "coverage": round(coverage, 4),
                    "penalty": round(penalty, 2),
                }
            )
        dimensions[dim.value] = {
            "score": round(_clamp(score), 2),
            "weight": DIMENSION_WEIGHTS[dim],
            "issues": issues,
        }

    total = sum(d["score"] * d["weight"] for d in dimensions.values())

    return {
        "total_score": round(_clamp(total), 2),
        "dimensions": dimensions,
        "deductions": deductions,
        "ruleset_note": (
            "Score is a deterministic heuristic for prioritisation, not an "
            "absolute measure of data correctness."
        ),
    }
