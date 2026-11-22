"""Descriptive statistics for one or more numeric variables."""

from __future__ import annotations

import numpy as np
from scipy import stats

from app.statistics.assumptions import check_constant, check_outliers
from app.statistics.base import StatisticalMethod
from app.statistics.context import StatisticalContext
from app.statistics.result import SampleInfo, StatisticalMethodResult
from app.statistics.types import MethodId, MissingPolicy


def _mean_ci(values: np.ndarray, confidence: float) -> tuple[float, float] | None:
    n = len(values)
    if n < 2:
        return None
    mean = float(np.mean(values))
    se = float(stats.sem(values))
    if se == 0 or not np.isfinite(se):
        return None
    alpha = 1 - confidence
    t_crit = float(stats.t.ppf(1 - alpha / 2, df=n - 1))
    return (mean - t_crit * se, mean + t_crit * se)


def describe_one(values: np.ndarray, name: str, confidence: float) -> dict:
    """Full descriptive summary for a single numeric array (NaN already removed)."""
    n = len(values)
    if n == 0:
        return {"name": name, "count": 0}
    q1, median, q3 = (float(v) for v in np.percentile(values, [25, 50, 75]))
    ci = _mean_ci(values, confidence)
    return {
        "name": name,
        "count": n,
        "mean": float(np.mean(values)),
        "median": median,
        "variance": float(np.var(values, ddof=1)) if n > 1 else None,
        "std": float(np.std(values, ddof=1)) if n > 1 else None,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "skewness": float(stats.skew(values, bias=False)) if n > 2 else None,
        "kurtosis": float(stats.kurtosis(values, bias=False)) if n > 3 else None,
        "mean_ci": {"lower": ci[0], "upper": ci[1], "confidence_level": confidence} if ci else None,
    }


class DescriptiveMethod(StatisticalMethod):
    method_id = MethodId.DESCRIPTIVE
    version = "1.0.2"
    name = "Descriptive statistics"
    description = (
        "Count, central tendency, dispersion, quartiles, skewness, kurtosis and a "
        "confidence interval for the mean of each numeric variable."
    )
    min_variables = 1
    max_variables = 50
    requires_numeric = True
    allow_constant = True

    def execute(self, ctx: StatisticalContext) -> StatisticalMethodResult:
        result = self.base_result(ctx)
        confidence = ctx.config.confidence_level
        summaries: dict[str, dict] = {}
        missing_counts: dict[str, int] = {}
        grouping = ctx.config.group_variable
        groups = None
        if grouping:
            if not ctx.has_column(grouping):
                raise ValueError(f"Grouping column '{grouping}' does not exist.")
            groups = ctx.categorical(grouping)
        total_original = 0
        total_excluded = 0
        for name in ctx.config.variables:
            raw = ctx.numeric(name)
            total_original = max(total_original, len(raw))
            valid_mask = ~np.isnan(raw)
            if groups is not None:
                valid_mask &= np.array(
                    [
                        value is not None and not (isinstance(value, float) and np.isnan(value))
                        for value in groups
                    ]
                )
            missing = int((~valid_mask).sum())
            missing_counts[name] = missing
            total_excluded = max(total_excluded, missing)
            valid = raw[valid_mask]
            if groups is None:
                summaries[name] = describe_one(valid, name, confidence)
            else:
                for group in dict.fromkeys(
                    value
                    for value in groups
                    if value is not None and not (isinstance(value, float) and np.isnan(value))
                ):
                    group_mask = np.array([value == group for value in groups])
                    values = raw[valid_mask & group_mask]
                    label = f"{grouping}={group} · {name}"
                    summaries[label] = describe_one(values, label, confidence)
                    missing_counts[label] = int((group_mask & ~valid_mask).sum())
            result.add_check(check_constant(valid, name))
            result.add_check(check_outliers(valid, name))

        result.sample = SampleInfo(
            original_n=total_original,
            excluded_n=total_excluded,
            final_n=total_original - total_excluded,
            reason="missing values excluded per variable and grouping column"
            if groups is not None
            else "missing values excluded per variable",
            per_variable=missing_counts,
        )
        result.descriptive_statistics = {"variables": summaries}
        if grouping:
            result.descriptive_statistics["group_variable"] = grouping
        # Surface the first variable's mean CI in the CI field for convenience.
        first = summaries.get(ctx.config.variables[0], {})
        if first.get("mean_ci"):
            result.confidence_interval = {
                "mean": first["mean_ci"],
            }
        result.limitations.append(
            "Descriptive statistics summarise the sample only; they make no "
            "inferential claim about a population."
        )
        if ctx.config.missing_policy == MissingPolicy.PAIRWISE_DELETE:
            result.warnings.append(
                "pairwise_delete has no effect for univariate descriptive statistics."
            )
        return result


DESCRIPTIVE = DescriptiveMethod()
