"""Rule abstractions: the ``Rule`` interface plus dataset/column base classes.

A rule is a small, self-describing object. The executor drives every rule
through :meth:`Rule.evaluate`, which returns zero or more ``RuleResult``.
Column-level rules iterate applicable columns themselves via ``ColumnRule``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.quality.context import RuleContext
from app.quality.result import RuleResult
from app.quality.types import Category, RuleLevel, RuleStatus, Severity

# Coarse base types a rule may declare applicability for. ``None`` means "all".
APPLICABLE_ALL = None


class Rule(ABC):
    """Base class for all quality rules."""

    #: Stable identifier, e.g. ``HIGH_MISSINGNESS``.
    rule_id: str = ""
    name: str = ""
    description: str = ""
    category: Category = Category.VALIDITY
    default_severity: Severity = Severity.MEDIUM
    level: RuleLevel = RuleLevel.DATASET
    #: Base types this rule applies to (column rules). ``None`` = all columns.
    applicable_types: list[str] | None = APPLICABLE_ALL
    version: str = "1"
    #: Whether the rule needs the raw DataFrame (vs. just profile stats).
    needs_dataframe: bool = False
    #: Whether results remain valid under Week-3 sampling.
    supports_sampling: bool = True
    limitations: str = ""

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        """Run the rule and return its findings (may be empty)."""

    # -- helpers for subclasses -------------------------------------------

    def result(
        self,
        *,
        status: RuleStatus,
        title: str,
        summary: str,
        severity: Severity | None = None,
        affected_columns: list[str] | None = None,
        affected_count: int = 0,
        affected_ratio: float = 0.0,
        threshold: dict[str, Any] | None = None,
        observed_value: Any = None,
        evidence: dict[str, Any] | None = None,
        remediation: str = "",
    ) -> RuleResult:
        """Build a ``RuleResult`` pre-filled with this rule's metadata."""
        return RuleResult(
            rule_id=self.rule_id,
            rule_name=self.name,
            description=self.description,
            status=status,
            severity=severity or self.default_severity,
            category=self.category,
            title=title,
            summary=summary,
            affected_columns=affected_columns or [],
            affected_count=affected_count,
            affected_ratio=affected_ratio,
            threshold=threshold,
            observed_value=observed_value,
            evidence=evidence or {},
            remediation=remediation,
            rule_version=self.version,
            limitations=self.limitations,
        )


class DatasetRule(Rule):
    """A rule that inspects the dataset as a whole (one result)."""

    level = RuleLevel.DATASET


class ColumnRule(Rule):
    """A rule evaluated independently for each applicable column."""

    level = RuleLevel.COLUMN

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        applicable = [c for c in ctx.columns if self.applies_to(c)]
        if not applicable:
            # Rule has nothing to inspect → a single explicit skipped result.
            targets = ", ".join(self.applicable_types) if self.applicable_types else "specific"
            return [
                self.result(
                    status=RuleStatus.SKIPPED,
                    title=f"{self.name}: no applicable columns",
                    summary=(f"This rule targets {targets} columns, but the dataset has none."),
                )
            ]

        results: list[RuleResult] = []
        for col in applicable:
            finding = self.evaluate_column(ctx, col)
            if finding is not None:
                results.append(finding)
        return results

    def applies_to(self, col: dict[str, Any]) -> bool:
        """Whether this rule should run for the given column profile."""
        if self.applicable_types is None:
            return True
        base = col.get("_base") or _base_from_profile(col)
        inferred = col.get("inferred_type")
        return base in self.applicable_types or inferred in self.applicable_types

    @abstractmethod
    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        """Evaluate one column; return a finding or ``None`` if it passes."""


def _base_from_profile(col: dict[str, Any]) -> str:
    """Derive a coarse base type from a Week-3 column profile."""
    if col.get("numeric_stats") is not None:
        return "numeric"
    if col.get("datetime_stats") is not None:
        return "datetime"
    if col.get("categorical_stats") is not None:
        return "string"
    physical = str(col.get("physical_type", "")).lower()
    if "bool" in physical:
        return "boolean"
    inferred = col.get("inferred_type", "")
    if inferred in ("integer", "float"):
        return "numeric"
    if inferred in ("date", "datetime"):
        return "datetime"
    return "string"
