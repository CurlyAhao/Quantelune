"""Field-level quality rules."""

from __future__ import annotations

from typing import Any

import polars as pl

from app.quality.base import ColumnRule
from app.quality.context import RuleContext
from app.quality.result import RuleResult, make_evidence
from app.quality.types import Category, RuleStatus, Severity


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole > 0 else 0.0


class EmptyColumnRule(ColumnRule):
    rule_id = "EMPTY_COLUMN"
    name = "Empty column"
    description = "Flags a column whose values are all null."
    category = Category.COMPLETENESS
    default_severity = Severity.CRITICAL
    applicable_types = None  # any column
    version = "1"

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        if ctx.row_count > 0 and col["count"] == 0:
            return self.result(
                status=RuleStatus.FAILED,
                title=f"Column '{col['name']}' is empty",
                summary="Every value in this column is null.",
                severity=Severity.CRITICAL,
                affected_columns=[col["name"]],
                affected_count=ctx.row_count,
                affected_ratio=1.0,
                observed_value={"null_rate": 1.0},
                remediation="Drop the column or check the extraction that produced it.",
            )
        return None


class HighMissingnessRule(ColumnRule):
    rule_id = "HIGH_MISSINGNESS"
    name = "High column missingness"
    description = "Flags columns whose missing rate crosses warning/high thresholds."
    category = Category.COMPLETENESS
    default_severity = Severity.MEDIUM
    applicable_types = None
    version = "1"
    limitations = "Missingness is not always an error (e.g. optional fields)."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        null_rate = float(col.get("null_rate", 0.0) or 0.0)
        high = ctx.thresholds.high_missingness
        warn = ctx.thresholds.warning_missingness
        if null_rate > high:
            return self.result(
                status=RuleStatus.FAILED,
                severity=Severity.HIGH,
                title=f"Column '{col['name']}' is mostly missing",
                summary=f"{null_rate:.1%} missing (high threshold {high:.0%}).",
                affected_columns=[col["name"]],
                affected_count=int(col.get("null_count", 0)),
                affected_ratio=null_rate,
                threshold={"high_missingness": high, "warning_missingness": warn},
                observed_value=null_rate,
                remediation="Investigate the source of missingness before imputing or dropping.",
            )
        if null_rate > warn:
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.MEDIUM,
                title=f"Column '{col['name']}' has notable missingness",
                summary=f"{null_rate:.1%} missing (warning threshold {warn:.0%}).",
                affected_columns=[col["name"]],
                affected_count=int(col.get("null_count", 0)),
                affected_ratio=null_rate,
                threshold={"high_missingness": high, "warning_missingness": warn},
                observed_value=null_rate,
                remediation="Consider how missing values affect downstream analysis.",
            )
        return None


class ConstantColumnRule(ColumnRule):
    rule_id = "CONSTANT_COLUMN"
    name = "Constant column"
    description = "Flags a column with a single distinct non-null value (no information)."
    category = Category.UNIQUENESS
    default_severity = Severity.LOW
    applicable_types = None
    version = "1"

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        if col["count"] > 0 and col["unique_count"] == 1:
            sample = col.get("sample_values", [])[:1]
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.LOW,
                title=f"Column '{col['name']}' is constant",
                summary=f"All {col['count']} non-null values equal {sample[0] if sample else '?'}."
                if sample
                else "All non-null values are identical.",
                affected_columns=[col["name"]],
                affected_count=col["count"],
                affected_ratio=1.0,
                observed_value={"unique_count": 1, "value": sample[0] if sample else None},
                remediation="Constant columns carry no signal; consider removing them.",
            )
        return None


class NearConstantColumnRule(ColumnRule):
    rule_id = "NEAR_CONSTANT_COLUMN"
    name = "Near-constant column"
    description = "Flags columns dominated by a single value above a frequency threshold."
    category = Category.UNIQUENESS
    default_severity = Severity.INFO
    applicable_types = None
    version = "1"
    needs_dataframe = True

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        if col["count"] == 0 or col["unique_count"] <= 1:
            return None  # constant/empty handled elsewhere
        series = ctx.series(col["name"])
        if series is None:
            return None
        non_null = series.drop_nulls()
        if non_null.len() == 0:
            return None
        counts = non_null.value_counts(sort=True)
        value_col = counts.columns[0]
        top_row = counts.row(0, named=True)
        top_value = top_row[value_col]
        top_count = int(top_row["count"])
        ratio = _ratio(top_count, non_null.len())
        threshold = ctx.thresholds.near_constant_ratio
        if ratio > threshold:
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.INFO,
                title=f"Column '{col['name']}' is near-constant",
                summary=(
                    f"Value '{top_value}' makes up {ratio:.1%} of non-null values "
                    f"(threshold {threshold:.0%})."
                ),
                affected_columns=[col["name"]],
                affected_count=top_count,
                affected_ratio=ratio,
                threshold={"near_constant_ratio": threshold},
                observed_value={"top_value": str(top_value), "top_ratio": round(ratio, 4)},
                evidence=make_evidence(samples=[top_value]),
                remediation="A near-constant column may add little information; review its use.",
            )
        return None


class HighCardinalityRule(ColumnRule):
    rule_id = "HIGH_CARDINALITY"
    name = "High cardinality"
    description = "Flags text/categorical columns with a very high unique-value ratio."
    category = Category.CONSISTENCY
    default_severity = Severity.LOW
    applicable_types = ["string", "categorical"]
    version = "1"
    limitations = "High cardinality is common for free text and identifiers; not an error."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        if col["count"] == 0:
            return None
        unique_rate = float(col.get("unique_rate", 0.0) or 0.0)
        threshold = ctx.thresholds.high_cardinality_ratio
        # Skip likely identifiers — they have their own rule.
        if unique_rate > ctx.thresholds.identifier_ratio:
            return None
        if unique_rate > threshold:
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.LOW,
                title=f"Column '{col['name']}' has high cardinality",
                summary=(
                    f"{unique_rate:.1%} unique values (threshold {threshold:.0%}); "
                    "may be free text."
                ),
                affected_columns=[col["name"]],
                affected_count=int(col.get("unique_count", 0)),
                affected_ratio=unique_rate,
                threshold={"high_cardinality_ratio": threshold},
                observed_value=unique_rate,
                remediation=(
                    "If categorical, consider grouping rare values; if text, consider NLP features."
                ),
            )
        return None


class PossibleIdentifierRule(ColumnRule):
    rule_id = "POSSIBLE_IDENTIFIER"
    name = "Possible identifier"
    description = "Flags columns that are almost entirely unique (likely an ID/key)."
    category = Category.UNIQUENESS
    default_severity = Severity.INFO
    applicable_types = None
    version = "1"
    limitations = "Heuristic based on uniqueness; confirm the column is really an identifier."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        if col["count"] == 0:
            return None
        unique_rate = float(col.get("unique_rate", 0.0) or 0.0)
        threshold = ctx.thresholds.identifier_ratio
        if unique_rate >= threshold:
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.INFO,
                title=f"Column '{col['name']}' may be an identifier",
                summary=f"{unique_rate:.1%} unique values (≥ {threshold:.0%}).",
                affected_columns=[col["name"]],
                affected_count=int(col.get("unique_count", 0)),
                affected_ratio=unique_rate,
                threshold={"identifier_ratio": threshold},
                observed_value=unique_rate,
                remediation="Identifiers usually should not be used as model features.",
            )
        return None


class MixedTypesRule(ColumnRule):
    rule_id = "MIXED_TYPES"
    name = "Mixed types"
    description = (
        "Flags string columns that mix numeric-looking and non-numeric values, "
        "which often indicates a parsing or data-entry problem."
    )
    category = Category.VALIDITY
    default_severity = Severity.MEDIUM
    applicable_types = ["string"]
    version = "1"
    needs_dataframe = True
    limitations = "Detection is pattern-based; some legitimate columns mix formats."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        series = ctx.series(col["name"])
        if series is None or col["count"] == 0:
            return None
        s = series.drop_nulls().cast(pl.String)
        if s.len() == 0:
            return None
        stripped = s.str.strip_chars()
        non_empty = stripped.filter(stripped != "")
        if non_empty.len() == 0:
            return None
        # A value "looks numeric" if it casts to Float64 (strict=False → null on failure).
        as_float = non_empty.cast(pl.Float64, strict=False)
        numeric_count = int(as_float.is_not_null().sum())
        non_numeric_count = non_empty.len() - numeric_count
        total = non_empty.len()
        numeric_frac = _ratio(numeric_count, total)
        # Mixed when a meaningful share is numeric AND a meaningful share is not.
        threshold = ctx.thresholds.mixed_type_ratio
        if threshold < numeric_frac < (1.0 - threshold):
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.MEDIUM,
                title=f"Column '{col['name']}' mixes types",
                summary=(
                    f"{numeric_frac:.0%} of values look numeric while {non_numeric_count} do not."
                ),
                affected_columns=[col["name"]],
                affected_count=non_numeric_count,
                affected_ratio=min(numeric_frac, 1 - numeric_frac),
                threshold={"mixed_type_ratio": threshold},
                observed_value={
                    "numeric_fraction": round(numeric_frac, 4),
                    "non_numeric_count": non_numeric_count,
                },
                remediation="Split or clean the column so each column holds one type.",
            )
        return None


class NumericOutliersRule(ColumnRule):
    rule_id = "NUMERIC_OUTLIERS"
    name = "Numeric outliers"
    description = (
        "Flags numeric columns with a high IQR-based outlier rate. Outliers are a "
        "prompt to review — not automatically errors."
    )
    category = Category.DISTRIBUTION
    default_severity = Severity.INFO
    applicable_types = ["numeric", "integer", "float"]
    version = "1"
    limitations = "IQR outliers assume a roughly symmetric distribution; review, don't auto-drop."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        stats = col.get("numeric_stats")
        if not stats:
            return None
        outlier_rate = float(stats.get("outlier_rate", 0.0) or 0.0)
        threshold = ctx.thresholds.outlier_ratio
        if outlier_rate > threshold:
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.INFO,
                title=f"Column '{col['name']}' has many outliers",
                summary=(
                    f"{outlier_rate:.1%} outliers by the IQR rule "
                    f"(threshold {threshold:.0%}). Needs review."
                ),
                affected_columns=[col["name"]],
                affected_count=int(stats.get("outlier_count", 0)),
                affected_ratio=outlier_rate,
                threshold={"outlier_ratio": threshold},
                observed_value=outlier_rate,
                remediation="Inspect outliers; decide whether they are valid extremes or errors.",
            )
        return None


class NonFiniteNumericRule(ColumnRule):
    rule_id = "NON_FINITE_NUMERIC"
    name = "Non-finite numeric values"
    description = "Flags numeric columns containing NaN, +Inf or -Inf."
    category = Category.VALIDITY
    default_severity = Severity.MEDIUM
    applicable_types = ["numeric", "integer", "float"]
    version = "1"
    needs_dataframe = True

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        series = ctx.series(col["name"])
        if series is None:
            return None
        as_float = series.drop_nulls().cast(pl.Float64, strict=False)
        if as_float.len() == 0:
            return None
        nan_count = int(as_float.is_nan().sum())
        inf_count = int(as_float.is_infinite().sum())
        total = as_float.len()
        non_finite = nan_count + inf_count
        ratio = _ratio(non_finite, total)
        threshold = ctx.thresholds.non_finite_ratio
        if non_finite > 0 and ratio > threshold:
            return self.result(
                status=RuleStatus.FAILED,
                severity=Severity.MEDIUM,
                title=f"Column '{col['name']}' has non-finite values",
                summary=f"{nan_count} NaN and {inf_count} infinite values.",
                affected_columns=[col["name"]],
                affected_count=non_finite,
                affected_ratio=ratio,
                threshold={"non_finite_ratio": threshold},
                observed_value={"nan": nan_count, "inf": inf_count},
                remediation="Replace or remove non-finite values; many models cannot handle them.",
            )
        return None


class InvalidRangeRule(ColumnRule):
    rule_id = "INVALID_RANGE"
    name = "Out-of-range numeric values"
    description = (
        "Flags numeric columns with values outside an explicit configured "
        "[numeric_min, numeric_max] range. Disabled unless bounds are configured."
    )
    category = Category.VALIDITY
    default_severity = Severity.MEDIUM
    applicable_types = ["numeric", "integer", "float"]
    version = "1"
    needs_dataframe = True
    limitations = "Only active when numeric_min < numeric_max is explicitly configured."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        lo = ctx.thresholds.numeric_min
        hi = ctx.thresholds.numeric_max
        if not lo < hi:
            return None  # range check not configured → skip silently
        series = ctx.series(col["name"])
        if series is None:
            return None
        vals = (
            series.drop_nulls()
            .cast(pl.Float64, strict=False)
            .filter(series.drop_nulls().cast(pl.Float64, strict=False).is_finite())
        )
        if vals.len() == 0:
            return None
        out = vals.filter((vals < lo) | (vals > hi))
        count = int(out.len())
        if count == 0:
            return None
        ratio = _ratio(count, vals.len())
        return self.result(
            status=RuleStatus.WARNING,
            severity=Severity.MEDIUM,
            title=f"Column '{col['name']}' has out-of-range values",
            summary=f"{count} values fall outside [{lo}, {hi}].",
            affected_columns=[col["name"]],
            affected_count=count,
            affected_ratio=ratio,
            threshold={"numeric_min": lo, "numeric_max": hi},
            observed_value={"min": float(vals.min()), "max": float(vals.max())},
            evidence=make_evidence(samples=out.head(5).to_list()),
            remediation="Verify the configured range matches the column's valid domain.",
        )


COLUMN_RULES = [
    EmptyColumnRule(),
    HighMissingnessRule(),
    ConstantColumnRule(),
    NearConstantColumnRule(),
    HighCardinalityRule(),
    PossibleIdentifierRule(),
    MixedTypesRule(),
    NumericOutliersRule(),
    NonFiniteNumericRule(),
    InvalidRangeRule(),
]
