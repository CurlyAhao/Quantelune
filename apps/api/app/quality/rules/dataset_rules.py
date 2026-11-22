"""Dataset-level quality rules."""

from __future__ import annotations

from app.quality.base import DatasetRule
from app.quality.context import RuleContext
from app.quality.result import RuleResult, make_evidence
from app.quality.types import Category, RuleStatus, Severity


class EmptyDatasetRule(DatasetRule):
    rule_id = "EMPTY_DATASET"
    name = "Empty dataset"
    description = "Flags a dataset that has zero rows or zero columns."
    category = Category.STRUCTURE
    default_severity = Severity.CRITICAL
    version = "1"
    limitations = "Only checks counts; does not judge whether the shape is appropriate."

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        rows = ctx.row_count
        cols = ctx.column_count
        if rows == 0 or cols == 0:
            return [
                self.result(
                    status=RuleStatus.FAILED,
                    title="Dataset is empty",
                    summary=f"The dataset has {rows} rows and {cols} columns.",
                    affected_count=rows,
                    affected_ratio=1.0,
                    observed_value={"row_count": rows, "column_count": cols},
                    threshold={"min_rows": 1, "min_columns": 1},
                    evidence=make_evidence(extra={"row_count": rows, "column_count": cols}),
                    remediation="Verify the upload succeeded and the source file contains data.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="Dataset is not empty",
                summary=f"{rows} rows × {cols} columns.",
                observed_value={"row_count": rows, "column_count": cols},
            )
        ]


class DuplicateRowsRule(DatasetRule):
    rule_id = "DUPLICATE_ROWS"
    name = "Duplicate rows"
    description = (
        "Flags when the proportion of fully duplicated rows exceeds a threshold. "
        "Duplicates may be legitimate (e.g. repeated measurements) — review before removing."
    )
    category = Category.UNIQUENESS
    default_severity = Severity.MEDIUM
    version = "1"
    supports_sampling = True
    limitations = (
        "Uses the profiled (possibly sampled) duplicate rate; on sampled data this "
        "is an estimate, not an exact full-data count."
    )

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        overview = ctx.overview
        rate = float(overview.get("duplicate_row_rate", 0.0) or 0.0)
        count = int(overview.get("duplicate_row_count", 0) or 0)
        threshold = ctx.thresholds.duplicate_row_rate
        if rate > threshold:
            return [
                self.result(
                    status=RuleStatus.FAILED,
                    title="High duplicate-row rate",
                    summary=(
                        f"{rate:.1%} of rows are exact duplicates (threshold {threshold:.0%})."
                    ),
                    affected_count=count,
                    affected_ratio=rate,
                    threshold={"duplicate_row_rate": threshold},
                    observed_value=rate,
                    evidence=make_evidence(extra={"duplicate_rows": count}),
                    remediation=(
                        "Confirm duplicates are unintended, then consider deduplication. "
                        "Do not drop rows that represent legitimate repeated observations."
                    ),
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="Duplicate-row rate within threshold",
                summary=f"{rate:.1%} duplicate rows (threshold {threshold:.0%}).",
                affected_count=count,
                affected_ratio=rate,
                threshold={"duplicate_row_rate": threshold},
                observed_value=rate,
            )
        ]


class TooManyColumnsRule(DatasetRule):
    rule_id = "TOO_MANY_COLUMNS"
    name = "Too many columns"
    description = "Flags datasets wider than a configurable column limit."
    category = Category.STRUCTURE
    default_severity = Severity.LOW
    version = "1"
    limitations = "The limit is a usability heuristic, not a correctness rule."

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        cols = ctx.column_count
        limit = int(ctx.thresholds.max_columns)
        if cols > limit:
            return [
                self.result(
                    status=RuleStatus.WARNING,
                    title="Very wide dataset",
                    summary=f"{cols} columns exceed the configured limit of {limit}.",
                    affected_count=cols,
                    affected_ratio=1.0,
                    threshold={"max_columns": limit},
                    observed_value=cols,
                    remediation="Consider splitting the dataset or dropping unused columns.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="Column count within limit",
                summary=f"{cols} columns (limit {limit}).",
                threshold={"max_columns": limit},
                observed_value=cols,
            )
        ]


class HighOverallMissingnessRule(DatasetRule):
    rule_id = "HIGH_OVERALL_MISSINGNESS"
    name = "High overall missingness"
    description = "Flags when the dataset-wide missing-value rate is high."
    category = Category.COMPLETENESS
    default_severity = Severity.HIGH
    version = "1"
    limitations = "Overall rate can hide per-column structure; see per-column rules."

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        completeness = float(ctx.overview.get("completeness", 1.0) or 0.0)
        missing_rate = 1.0 - completeness
        threshold = ctx.thresholds.high_overall_missingness
        if missing_rate > threshold:
            return [
                self.result(
                    status=RuleStatus.FAILED,
                    title="High overall missingness",
                    summary=(
                        f"{missing_rate:.1%} of all cells are missing (threshold {threshold:.0%})."
                    ),
                    affected_count=int(ctx.overview.get("total_nulls", 0) or 0),
                    affected_ratio=missing_rate,
                    threshold={"high_overall_missingness": threshold},
                    observed_value=missing_rate,
                    remediation="Investigate why so much data is missing before analysis.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="Overall missingness acceptable",
                summary=f"{missing_rate:.1%} missing (threshold {threshold:.0%}).",
                affected_ratio=missing_rate,
                threshold={"high_overall_missingness": threshold},
                observed_value=missing_rate,
            )
        ]


DATASET_RULES = [
    EmptyDatasetRule(),
    DuplicateRowsRule(),
    TooManyColumnsRule(),
    HighOverallMissingnessRule(),
]
