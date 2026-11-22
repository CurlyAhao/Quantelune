"""Temporal (date/datetime) quality rules."""

from __future__ import annotations

from typing import Any

import polars as pl

from app.quality.base import ColumnRule
from app.quality.context import RuleContext
from app.quality.result import RuleResult, make_evidence
from app.quality.types import Category, RuleStatus, Severity

_DATE_NAME_HINTS = ("date", "time", "timestamp", "day", "month", "year", "created", "updated")


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole > 0 else 0.0


def _parse_ok_mask(s: pl.Series) -> pl.Series:
    """Boolean mask of which string values parse as a date/datetime.

    Robust to Polars raising when no format can be inferred — in that case no
    value parses (all-False mask).
    """
    n = s.len()
    try:
        dt = s.str.to_datetime(strict=False)
    except Exception:  # noqa: BLE001 - inference may raise on odd inputs
        dt = None
    try:
        d = s.str.to_date(strict=False)
    except Exception:  # noqa: BLE001
        d = None

    if dt is None and d is None:
        return pl.Series("ok", [False] * n)
    ok = pl.Series("ok", [False] * n)
    if dt is not None:
        ok = ok | dt.is_not_null()
    if d is not None:
        ok = ok | d.is_not_null()
    return ok


class InvalidDatetimeRule(ColumnRule):
    rule_id = "INVALID_DATETIME"
    name = "Invalid datetime values"
    description = (
        "Flags string columns that look date-like but contain values that fail date parsing."
    )
    category = Category.VALIDITY
    default_severity = Severity.MEDIUM
    applicable_types = ["string"]
    version = "1"
    needs_dataframe = True
    limitations = (
        "Date-likeness is inferred heuristically; ambiguous formats may be missed or over-flagged."
    )

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        series = ctx.series(col["name"])
        if series is None or col["count"] == 0:
            return None
        s = series.drop_nulls().cast(pl.String).str.strip_chars()
        s = s.filter(s != "")
        if s.len() == 0:
            return None

        ok = _parse_ok_mask(s)
        ok_count = int(ok.sum())
        ok_frac = _ratio(ok_count, s.len())

        name_hint = any(h in col["name"].lower() for h in _DATE_NAME_HINTS)
        # Treat as date-intended if most values parse or the name suggests it.
        date_intended = ok_frac >= 0.5 or name_hint
        if not date_intended:
            return None

        invalid_count = s.len() - ok_count
        invalid_frac = _ratio(invalid_count, s.len())
        threshold = ctx.thresholds.invalid_datetime_ratio
        if invalid_count > 0 and invalid_frac > threshold:
            bad = s.filter(~ok).head(5).to_list()
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.MEDIUM,
                title=f"Column '{col['name']}' has invalid datetime values",
                summary=(
                    f"{invalid_count} value(s) ({invalid_frac:.0%}) look date-like "
                    f"but fail parsing (threshold {threshold:.0%})."
                ),
                affected_columns=[col["name"]],
                affected_count=invalid_count,
                affected_ratio=invalid_frac,
                threshold={"invalid_datetime_ratio": threshold},
                observed_value={"invalid_fraction": round(invalid_frac, 4)},
                evidence=make_evidence(samples=bad),
                remediation="Standardise the date format or coerce invalid entries to null.",
            )
        return None


class DuplicateTimestampsRule(ColumnRule):
    rule_id = "DUPLICATE_TIMESTAMPS"
    name = "Duplicate timestamps"
    description = "Flags datetime columns with repeated timestamp values."
    category = Category.TEMPORAL
    default_severity = Severity.LOW
    applicable_types = ["date", "datetime"]
    version = "1"
    limitations = "Duplicate timestamps can be valid (multiple events per period)."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        dt = col.get("datetime_stats")
        if not dt or col["count"] == 0:
            return None
        dup = int(dt.get("duplicate_count", 0) or 0)
        if dup == 0:
            return None
        ratio = _ratio(dup, col["count"])
        threshold = ctx.thresholds.duplicate_timestamp_ratio
        if ratio > threshold:
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.LOW,
                title=f"Column '{col['name']}' has duplicate timestamps",
                summary=f"{dup} repeated timestamp(s) ({ratio:.1%}, threshold {threshold:.0%}).",
                affected_columns=[col["name"]],
                affected_count=dup,
                affected_ratio=ratio,
                threshold={"duplicate_timestamp_ratio": threshold},
                observed_value=ratio,
                remediation="If one row per period is expected, deduplicate or aggregate.",
            )
        return None


class NonMonotonicTimeRule(ColumnRule):
    rule_id = "NON_MONOTONIC_TIME"
    name = "Non-monotonic time series"
    description = (
        "Flags datetime columns that are not sorted, when the data may be expected "
        "to be time-ordered. This is a hint, not an error."
    )
    category = Category.TEMPORAL
    default_severity = Severity.INFO
    applicable_types = ["date", "datetime"]
    version = "1"
    limitations = "Unsorted time columns are often legitimate; this only prompts a check."

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        dt = col.get("datetime_stats")
        if not dt or col["count"] < 2:
            return None
        if dt.get("is_monotonic", False):
            return None
        return self.result(
            status=RuleStatus.WARNING,
            severity=Severity.INFO,
            title=f"Column '{col['name']}' is not time-sorted",
            summary="Timestamps are not in ascending or descending order.",
            affected_columns=[col["name"]],
            affected_count=col["count"],
            affected_ratio=1.0,
            observed_value={"is_monotonic": False},
            remediation="If time order matters, sort by this column before time-series analysis.",
        )


class IrregularTimeIntervalRule(ColumnRule):
    rule_id = "IRREGULAR_TIME_INTERVAL"
    name = "Irregular time intervals"
    description = (
        "Flags datetime columns where the gaps between consecutive (sorted) "
        "timestamps are inconsistent — a sign of missing periods."
    )
    category = Category.TEMPORAL
    default_severity = Severity.LOW
    applicable_types = ["date", "datetime"]
    version = "1"
    needs_dataframe = True
    limitations = (
        "Crypto/24-7 data should not be judged by business-calendar gaps; the rule "
        "only measures gap regularity, not calendar expectations."
    )

    def evaluate_column(self, ctx: RuleContext, col: dict[str, Any]) -> RuleResult | None:
        series = ctx.series(col["name"])
        if series is None or col["count"] < 3:
            return None
        s = series.drop_nulls().sort()
        if s.len() < 3:
            return None
        # Convert to a uniform numeric (seconds) for interval comparison.
        if s.dtype == pl.Date:
            numeric = s.cast(pl.Datetime("ms")).cast(pl.Int64) // 1000
        else:
            numeric = s.cast(pl.Int64) // 1_000_000  # ns/µs → approx seconds scale
        diffs = numeric.diff().drop_nulls()
        if diffs.len() == 0:
            return None
        # Modal interval = the most common gap.
        mode_rows = diffs.value_counts(sort=True)
        modal_gap = mode_rows.row(0, named=True)[diffs.name]
        irregular = diffs.filter(diffs != modal_gap).len()
        ratio = _ratio(int(irregular), diffs.len())
        threshold = ctx.thresholds.irregular_interval_ratio
        if ratio > threshold:
            return self.result(
                status=RuleStatus.WARNING,
                severity=Severity.LOW,
                title=f"Column '{col['name']}' has irregular intervals",
                summary=(
                    f"{ratio:.0%} of gaps differ from the modal interval "
                    f"(threshold {threshold:.0%}); possible missing periods."
                ),
                affected_columns=[col["name"]],
                affected_count=int(irregular),
                affected_ratio=ratio,
                threshold={"irregular_interval_ratio": threshold},
                observed_value={"irregular_fraction": round(ratio, 4)},
                remediation="Check for missing time periods if a regular cadence is expected.",
            )
        return None


TEMPORAL_RULES = [
    InvalidDatetimeRule(),
    DuplicateTimestampsRule(),
    NonMonotonicTimeRule(),
    IrregularTimeIntervalRule(),
]
