"""Optional OHLCV (crypto/financial candle) domain rules.

These rules only activate when the dataset's columns can be confidently mapped
to the canonical OHLCV roles (timestamp, open, high, low, close, volume). They
never apply stock-market calendar assumptions (e.g. weekend gaps) — crypto
trades 24/7, so only pure gap/consistency checks are used.
"""

from __future__ import annotations

import polars as pl

from app.quality.base import DatasetRule
from app.quality.context import RuleContext
from app.quality.result import RuleResult, make_evidence
from app.quality.types import Category, RuleStatus, Severity

# Name patterns used to auto-detect OHLCV roles (lowercase substring match).
ROLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "datetime", "date", "time", "day"),
    "open": ("open",),
    "high": ("high",),
    "low": ("low",),
    "close": ("close", "last", "price"),
    "volume": ("volume", "vol"),
}


def detect_ohlcv_mapping(columns: list[str]) -> dict[str, str]:
    """Best-effort mapping of canonical OHLCV role → actual column name.

    A role is only mapped when exactly-ish one column matches; ambiguous roles
    are left unmapped so rules skip safely.
    """
    mapping: dict[str, str] = {}
    lowered = {c: c.lower() for c in columns}
    for role, patterns in ROLE_PATTERNS.items():
        matches = [orig for orig, low in lowered.items() if any(p in low for p in patterns)]
        # Prefer an exact-ish match (column name == pattern) when available.
        exact = [m for m in matches if any(p == lowered[m] for p in patterns)]
        chosen = exact or matches
        if len(chosen) == 1:
            mapping[role] = chosen[0]
    return mapping


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole > 0 else 0.0


def _coerce_datetime(s: pl.Series) -> pl.Series | None:
    """Normalise a timestamp-like series (string/date/datetime) to Datetime.

    Returns ``None`` if the values cannot be interpreted as timestamps.
    """
    if s.dtype == pl.Date:
        return s.cast(pl.Datetime("ms"))
    if s.dtype == pl.Datetime:
        return s
    if s.dtype == pl.String:
        stripped = s.str.strip_chars()
        for parser in (
            lambda x: x.str.to_datetime(strict=False),
            lambda x: x.str.to_date(strict=False).cast(pl.Datetime("ms")),
        ):
            try:
                parsed = parser(stripped)
            except Exception:  # noqa: BLE001
                parsed = None
            if parsed is not None and parsed.is_not_null().any():
                return parsed
        return None
    return None


class OhlcvRule(DatasetRule):
    """Base for OHLCV rules: declares required roles and handles skipping."""

    category = Category.DOMAIN
    required_roles: tuple[str, ...] = ()
    needs_dataframe = True

    def _mapped(self, ctx: RuleContext, role: str) -> str | None:
        return ctx.ohlcv_mapping.get(role)

    def _skip_if_unmapped(self, ctx: RuleContext) -> RuleResult | None:
        missing = [r for r in self.required_roles if not self._mapped(ctx, r)]
        if missing:
            return self.result(
                status=RuleStatus.SKIPPED,
                title=f"{self.name}: OHLCV fields not identified",
                summary=(
                    "This optional rule needs "
                    f"{', '.join(self.required_roles)}; missing/unmapped: "
                    f"{', '.join(missing)}."
                ),
            )
        return None


class OhlcConsistencyRule(OhlcvRule):
    rule_id = "OHLC_CONSISTENCY"
    name = "OHLC consistency"
    description = "Checks high ≥ low, high ≥ open/close and low ≤ open/close for each row."
    default_severity = Severity.HIGH
    required_roles = ("open", "high", "low", "close")
    version = "1"

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        skip = self._skip_if_unmapped(ctx)
        if skip:
            return [skip]
        o = ctx.series(ctx.ohlcv_mapping["open"])
        h = ctx.series(ctx.ohlcv_mapping["high"])
        low = ctx.series(ctx.ohlcv_mapping["low"])
        c = ctx.series(ctx.ohlcv_mapping["close"])
        if o is None or h is None or low is None or c is None:
            return [skip]
        n = min(o.len(), h.len(), low.len(), c.len())
        if n == 0:
            return [skip]
        violation = ((h < low) | (h < o) | (h < c) | (low > o) | (low > c)).fill_null(False)
        count = int(violation.sum())
        ratio = _ratio(count, n)
        if count > 0:
            return [
                self.result(
                    status=RuleStatus.FAILED,
                    title="OHLC rows violate consistency",
                    summary=(
                        f"{count} row(s) ({ratio:.1%}) break high≥low / "
                        "high≥open,close / low≤open,close."
                    ),
                    affected_columns=[ctx.ohlcv_mapping[r] for r in self.required_roles],
                    affected_count=count,
                    affected_ratio=ratio,
                    observed_value={"violation_count": count},
                    remediation=(
                        "Correct or remove rows where high<low, high<open/close, or low>open/close."
                    ),
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="OHLC values are consistent",
                summary=f"All {n} rows satisfy high≥low and the open/close bounds.",
                observed_value={"violation_count": 0},
            )
        ]


class NonPositivePriceRule(OhlcvRule):
    rule_id = "NON_POSITIVE_PRICE"
    name = "Non-positive price"
    description = "Flags OHLC price values that are ≤ 0."
    default_severity = Severity.HIGH
    required_roles = ("close",)
    version = "1"

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        roles = [r for r in ("open", "high", "low", "close") if self._mapped(ctx, r)]
        if not roles:
            return [self._skip_if_unmapped(ctx)]
        total_bad = 0
        total_cells = 0
        cols: list[str] = []
        for role in roles:
            s = ctx.series(ctx.ohlcv_mapping[role])
            if s is None:
                continue
            vals = s.drop_nulls().cast(pl.Float64, strict=False)
            total_cells += vals.len()
            bad = int((vals <= 0).sum())
            total_bad += bad
            if bad:
                cols.append(ctx.ohlcv_mapping[role])
        if total_cells == 0:
            return [self._skip_if_unmapped(ctx)]
        ratio = _ratio(total_bad, total_cells)
        if total_bad > 0:
            return [
                self.result(
                    status=RuleStatus.FAILED,
                    title="Non-positive price values",
                    summary=f"{total_bad} price value(s) are ≤ 0 across {', '.join(cols)}.",
                    affected_columns=cols,
                    affected_count=total_bad,
                    affected_ratio=ratio,
                    observed_value={"non_positive_count": total_bad},
                    remediation="Prices must be positive; fix or drop affected rows.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="All prices are positive",
                summary=f"Checked {total_cells} price value(s).",
                observed_value={"non_positive_count": 0},
            )
        ]


class NegativeVolumeRule(OhlcvRule):
    rule_id = "NEGATIVE_VOLUME"
    name = "Negative volume"
    description = "Flags negative volume values."
    default_severity = Severity.MEDIUM
    required_roles = ("volume",)
    version = "1"

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        skip = self._skip_if_unmapped(ctx)
        if skip:
            return [skip]
        s = ctx.series(ctx.ohlcv_mapping["volume"])
        if s is None:
            return [skip]
        vals = s.drop_nulls().cast(pl.Float64, strict=False)
        neg = int((vals < 0).sum())
        if vals.len() == 0:
            return [skip]
        ratio = _ratio(neg, vals.len())
        if neg > 0:
            return [
                self.result(
                    status=RuleStatus.FAILED,
                    title="Negative volume values",
                    summary=f"{neg} volume value(s) are negative.",
                    affected_columns=[ctx.ohlcv_mapping["volume"]],
                    affected_count=neg,
                    affected_ratio=ratio,
                    observed_value={"negative_count": neg},
                    remediation="Volume cannot be negative; fix or drop affected rows.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="No negative volume",
                summary=f"Checked {vals.len()} volume value(s).",
                observed_value={"negative_count": 0},
            )
        ]


class DuplicateMarketTimestampRule(OhlcvRule):
    rule_id = "DUPLICATE_MARKET_TIMESTAMP"
    name = "Duplicate market timestamp"
    description = "Flags repeated timestamps (same period recorded more than once)."
    default_severity = Severity.MEDIUM
    required_roles = ("timestamp",)
    version = "1"

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        skip = self._skip_if_unmapped(ctx)
        if skip:
            return [skip]
        s = ctx.series(ctx.ohlcv_mapping["timestamp"])
        if s is None:
            return [skip]
        non_null = s.drop_nulls()
        if non_null.len() == 0:
            return [skip]
        dup = non_null.len() - non_null.n_unique()
        ratio = _ratio(int(dup), non_null.len())
        if dup > 0:
            return [
                self.result(
                    status=RuleStatus.WARNING,
                    title="Duplicate timestamps",
                    summary=f"{dup} repeated timestamp(s) ({ratio:.1%}).",
                    affected_columns=[ctx.ohlcv_mapping["timestamp"]],
                    affected_count=int(dup),
                    affected_ratio=ratio,
                    observed_value={"duplicate_count": int(dup)},
                    remediation="Deduplicate so each period appears once.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="No duplicate timestamps",
                summary=f"{non_null.len()} unique timestamp(s).",
                observed_value={"duplicate_count": 0},
            )
        ]


class MissingTimePeriodsRule(OhlcvRule):
    rule_id = "MISSING_TIME_PERIODS"
    name = "Missing time periods"
    description = (
        "For daily data, flags gaps larger than one day between consecutive "
        "timestamps. Makes no weekend/holiday assumptions (crypto trades 24/7)."
    )
    default_severity = Severity.LOW
    required_roles = ("timestamp",)
    version = "1"
    needs_dataframe = True
    limitations = "Assumes an expected daily cadence; only reports gap counts."

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        skip = self._skip_if_unmapped(ctx)
        if skip:
            return [skip]
        s = ctx.series(ctx.ohlcv_mapping["timestamp"])
        if s is None:
            return [skip]
        dt = _coerce_datetime(s)
        if dt is None:
            return [skip]
        dt = dt.drop_nulls().sort()
        if dt.len() < 2:
            return [skip]
        numeric_days = dt.cast(pl.Int64) // 86_400_000  # ms → days
        gaps = numeric_days.diff().drop_nulls()
        # A gap > 1 day means at least one missing daily period.
        missing = gaps.filter(gaps > 1)
        missing_periods = int((missing - 1).sum()) if missing.len() else 0
        if missing_periods > 0:
            return [
                self.result(
                    status=RuleStatus.WARNING,
                    title="Missing daily periods",
                    summary=f"About {missing_periods} daily period(s) appear to be missing.",
                    affected_columns=[ctx.ohlcv_mapping["timestamp"]],
                    affected_count=missing_periods,
                    affected_ratio=_ratio(int(missing.len()), gaps.len()),
                    observed_value={"missing_periods": missing_periods},
                    remediation="Backfill or note the missing periods before time-series work.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="No missing daily periods",
                summary="Timestamps form a contiguous daily sequence.",
                observed_value={"missing_periods": 0},
            )
        ]


class ExtremeReturnRule(OhlcvRule):
    rule_id = "EXTREME_RETURN"
    name = "Extreme returns"
    description = (
        "Flags period-over-period returns whose magnitude exceeds a threshold. "
        "Informational — large moves can be genuine."
    )
    default_severity = Severity.INFO
    required_roles = ("close",)
    version = "1"
    needs_dataframe = True
    limitations = "A high return is not an error by itself; review before acting."

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        skip = self._skip_if_unmapped(ctx)
        if skip:
            return [skip]
        s = ctx.series(ctx.ohlcv_mapping["close"])
        if s is None:
            return [skip]
        prices = s.cast(pl.Float64, strict=False)
        prev = prices.shift(1)
        ret = ((prices - prev) / prev).abs()
        threshold = ctx.thresholds.extreme_return_ratio
        extreme = ret.filter(ret.is_finite() & (ret > threshold))
        count = int(extreme.len())
        denom = ret.drop_nulls().len()
        if count > 0:
            return [
                self.result(
                    status=RuleStatus.WARNING,
                    severity=Severity.INFO,
                    title="Extreme period returns",
                    summary=f"{count} return(s) exceed {threshold:.0%} magnitude. Review.",
                    affected_columns=[ctx.ohlcv_mapping["close"]],
                    affected_count=count,
                    affected_ratio=_ratio(count, denom),
                    threshold={"extreme_return_ratio": threshold},
                    observed_value={"extreme_count": count},
                    evidence=make_evidence(
                        samples=[round(float(v), 4) for v in extreme.head(5).to_list()]
                    ),
                    remediation="Verify extreme moves are real and not data errors.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="No extreme returns",
                summary=f"No period return exceeds {threshold:.0%}.",
                threshold={"extreme_return_ratio": threshold},
                observed_value={"extreme_count": 0},
            )
        ]


class ZeroVolumeRule(OhlcvRule):
    rule_id = "ZERO_VOLUME"
    name = "Zero-volume streak"
    description = "Flags consecutive zero-volume bars beyond a configured streak length."
    default_severity = Severity.INFO
    required_roles = ("volume",)
    version = "1"
    needs_dataframe = True

    def evaluate(self, ctx: RuleContext) -> list[RuleResult]:
        skip = self._skip_if_unmapped(ctx)
        if skip:
            return [skip]
        s = ctx.series(ctx.ohlcv_mapping["volume"])
        if s is None:
            return [skip]
        vals = s.cast(pl.Float64, strict=False).fill_null(0.0)
        streak_len = int(ctx.thresholds.zero_volume_streak)
        is_zero = (vals == 0).to_list()
        max_streak = 0
        current = 0
        for z in is_zero:
            current = current + 1 if z else 0
            max_streak = max(max_streak, current)
        if max_streak >= streak_len:
            return [
                self.result(
                    status=RuleStatus.WARNING,
                    severity=Severity.INFO,
                    title="Long zero-volume streak",
                    summary=(
                        f"{max_streak} consecutive zero-volume bar(s) (threshold {streak_len})."
                    ),
                    affected_columns=[ctx.ohlcv_mapping["volume"]],
                    affected_count=max_streak,
                    affected_ratio=_ratio(int((vals == 0).sum()), vals.len()),
                    threshold={"zero_volume_streak": streak_len},
                    observed_value={"max_zero_streak": max_streak},
                    remediation="Check whether zero-volume periods reflect missing data.",
                )
            ]
        return [
            self.result(
                status=RuleStatus.PASSED,
                title="No long zero-volume streak",
                summary=f"Longest zero-volume streak is {max_streak} (< {streak_len}).",
                threshold={"zero_volume_streak": streak_len},
                observed_value={"max_zero_streak": max_streak},
            )
        ]


OHLCV_RULES = [
    OhlcConsistencyRule(),
    NonPositivePriceRule(),
    NegativeVolumeRule(),
    DuplicateMarketTimestampRule(),
    MissingTimePeriodsRule(),
    ExtremeReturnRule(),
    ZeroVolumeRule(),
]
