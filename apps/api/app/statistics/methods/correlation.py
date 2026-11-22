"""Correlation methods: Pearson, Spearman and Kendall."""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from app.statistics.assumptions import check_constant, check_normality, check_sample_size
from app.statistics.base import StatisticalMethod
from app.statistics.context import StatisticalContext, apply_missing_policy
from app.statistics.result import StatisticalMethodResult
from app.statistics.types import MIN_SAMPLE_SIZE, MethodId


def _pearson_ci(r: float, n: int, confidence: float) -> tuple[float, float] | None:
    """Fisher z-transform confidence interval for Pearson r."""
    if n < 4 or abs(r) >= 1:
        return None
    z = math.atanh(r)
    se = 1 / math.sqrt(n - 3)
    zcrit = float(stats.norm.ppf(1 - (1 - confidence) / 2))
    return (math.tanh(z - zcrit * se), math.tanh(z + zcrit * se))


class CorrelationMethod(StatisticalMethod):
    min_variables = 2
    max_variables = 2
    requires_numeric = True

    def _pair(
        self, ctx: StatisticalContext, result: StatisticalMethodResult
    ) -> tuple[np.ndarray, np.ndarray] | None:
        x_name, y_name = ctx.config.variables[0], ctx.config.variables[1]
        arrays = {x_name: ctx.numeric(x_name), y_name: ctx.numeric(y_name)}
        cleaned, info = apply_missing_policy(
            arrays,
            ctx.config.missing_policy,
            paired=True,  # Every bivariate coefficient must preserve original row pairs.
        )
        result.sample = info
        x, y = cleaned[x_name], cleaned[y_name]
        n = min(len(x), len(y))
        x, y = x[:n], y[:n]
        result.add_check(check_sample_size(n, MIN_SAMPLE_SIZE[self.method_id]))
        result.add_check(check_constant(x, x_name))
        result.add_check(check_constant(y, y_name))
        if n < MIN_SAMPLE_SIZE[self.method_id]:
            return None
        return x, y

    def _fill(self, result, x, y, r, p, n, ci, kind):
        result.statistic = {"name": f"{kind}_r", "value": float(r), "n": n}
        result.p_value = float(p)
        result.estimates = {"correlation": float(r)}
        result.confidence_interval = (
            {
                "correlation": {
                    "lower": ci[0],
                    "upper": ci[1],
                    "confidence_level": result.confidence_level,
                }
            }
            if ci
            else {}
        )
        result.effect_size = {
            "name": f"{kind}_r",
            "value": float(r),
            "interpretation": "correlation magnitude (not causal)",
        }
        result.limitations.append(
            "Correlation does not imply causation; it measures association only."
        )


class PearsonMethod(CorrelationMethod):
    method_id = MethodId.PEARSON
    version = "1.0.1"
    name = "Pearson correlation"
    description = (
        "Linear association between two numeric variables (assumes approximate normality)."
    )

    def execute(self, ctx: StatisticalContext) -> StatisticalMethodResult:
        result = self.base_result(ctx)
        pair = self._pair(ctx, result)
        if pair is None:
            return self.not_applicable(ctx, "Insufficient valid observations for Pearson r.")
        x, y = pair
        result.add_check(check_normality(x, ctx.config.variables[0]))
        result.add_check(check_normality(y, ctx.config.variables[1]))
        r, p = stats.pearsonr(x, y)
        ci = _pearson_ci(float(r), len(x), ctx.config.confidence_level)
        self._fill(result, x, y, r, p, len(x), ci, "pearson")
        result.warnings.append(
            "Pearson r captures linear relationships only; consider Spearman for "
            "monotonic/non-linear associations."
        )
        return result


class SpearmanMethod(CorrelationMethod):
    method_id = MethodId.SPEARMAN
    version = "1.0.1"
    name = "Spearman rank correlation"
    description = "Monotonic association based on ranks (robust to non-normality and outliers)."

    def execute(self, ctx: StatisticalContext) -> StatisticalMethodResult:
        result = self.base_result(ctx)
        pair = self._pair(ctx, result)
        if pair is None:
            return self.not_applicable(ctx, "Insufficient valid observations for Spearman rho.")
        x, y = pair
        rho, p = stats.spearmanr(x, y)
        self._fill(result, x, y, rho, p, len(x), None, "spearman")
        result.limitations.append(
            "Exact confidence intervals for Spearman rho are not reported here."
        )
        return result


class KendallMethod(CorrelationMethod):
    method_id = MethodId.KENDALL
    version = "1.0.1"
    name = "Kendall tau correlation"
    description = (
        "Ordinal association based on concordant/discordant pairs; robust for small samples."
    )

    def execute(self, ctx: StatisticalContext) -> StatisticalMethodResult:
        result = self.base_result(ctx)
        pair = self._pair(ctx, result)
        if pair is None:
            return self.not_applicable(ctx, "Insufficient valid observations for Kendall tau.")
        x, y = pair
        tau, p = stats.kendalltau(x, y)
        self._fill(result, x, y, tau, p, len(x), None, "kendall")
        result.limitations.append(
            "Kendall tau values are typically smaller in magnitude than Pearson/Spearman."
        )
        return result


PEARSON = PearsonMethod()
SPEARMAN = SpearmanMethod()
KENDALL = KendallMethod()
