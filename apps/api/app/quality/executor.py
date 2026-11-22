"""Rule executor: runs every registered rule, isolating per-rule failures.

A single rule raising an exception never aborts the run — it is captured as a
finding with ``status=error`` so the overall report remains available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.quality.context import RuleContext
from app.quality.registry import RuleRegistry
from app.quality.result import RuleResult
from app.quality.types import RuleStatus, Severity

logger = logging.getLogger("statforge.quality")


@dataclass
class ExecutionOutcome:
    """Aggregate result of executing the full rule set once."""

    results: list[RuleResult] = field(default_factory=list)
    total_rules: int = 0
    passed: int = 0
    warning: int = 0
    failed: int = 0
    error: int = 0
    skipped: int = 0
    truncated: bool = False

    def counts(self) -> dict[str, int]:
        return {
            "total_rules": self.total_rules,
            "passed": self.passed,
            "warning": self.warning,
            "failed": self.failed,
            "error": self.error,
            "skipped": self.skipped,
        }


class RuleExecutor:
    def __init__(self, registry: RuleRegistry) -> None:
        self.registry = registry

    def run(self, ctx: RuleContext) -> ExecutionOutcome:
        outcome = ExecutionOutcome()
        outcome.total_rules = len(self.registry)
        max_findings = max(1, int(ctx.thresholds.max_findings))

        for rule in self.registry.all():
            try:
                findings = rule.evaluate(ctx)
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                logger.exception("Rule %s failed", rule.rule_id)
                findings = [
                    RuleResult(
                        rule_id=rule.rule_id,
                        rule_name=rule.name,
                        description=rule.description,
                        status=RuleStatus.ERROR,
                        severity=Severity.LOW,
                        category=rule.category,
                        title=f"{rule.name}: evaluation error",
                        summary=(
                            "This rule raised an unexpected error and was skipped. "
                            "It does not affect other rules."
                        ),
                        evidence={"error": f"{type(exc).__name__}: {exc}"[:500]},
                        rule_version=rule.version,
                    )
                ]

            for finding in findings:
                _tally(outcome, finding.status)
                outcome.results.append(finding)

        # Cap the number of persisted findings (keep the most severe first).
        if len(outcome.results) > max_findings:
            outcome.results = _prioritize(outcome.results, max_findings)
            outcome.truncated = True

        return outcome


_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}
_STATUS_RANK = {
    RuleStatus.FAILED: 0,
    RuleStatus.ERROR: 1,
    RuleStatus.WARNING: 2,
    RuleStatus.PASSED: 3,
    RuleStatus.SKIPPED: 4,
}


def _prioritize(results: list[RuleResult], limit: int) -> list[RuleResult]:
    """Keep the most actionable findings: failed/error first, then by severity."""
    ordered = sorted(
        results,
        key=lambda r: (
            _STATUS_RANK.get(r.status, 9),
            _SEVERITY_RANK.get(r.severity, 9),
            -float(r.affected_ratio or 0.0),
        ),
    )
    return ordered[:limit]


def _tally(outcome: ExecutionOutcome, status: RuleStatus) -> None:
    if status is RuleStatus.PASSED:
        outcome.passed += 1
    elif status is RuleStatus.WARNING:
        outcome.warning += 1
    elif status is RuleStatus.FAILED:
        outcome.failed += 1
    elif status is RuleStatus.ERROR:
        outcome.error += 1
    elif status is RuleStatus.SKIPPED:
        outcome.skipped += 1


def category_summary(results: list[RuleResult]) -> dict[str, dict[str, int]]:
    """Group finding counts by category and status (for UI overview)."""
    summary: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = summary.setdefault(r.category.value, {})
        bucket[r.status.value] = bucket.get(r.status.value, 0) + 1
    return summary
