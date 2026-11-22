"""Multi-group comparison: one-way ANOVA, Welch ANOVA and Kruskal–Wallis."""

from __future__ import annotations

import numpy as np
from scipy import stats

from app.statistics import effect_sizes as es
from app.statistics.assumptions import (
    check_group_variance_homogeneity,
    check_normality,
    check_sample_size,
)
from app.statistics.base import StatisticalMethod
from app.statistics.context import StatisticalContext
from app.statistics.missing_preflight import (
    REJECT_MISSING_VERSION,
    RESULT_METADATA_VERSION,
    require_complete_inputs,
)
from app.statistics.result import SampleInfo
from app.statistics.types import MethodId


def _groups(ctx: StatisticalContext, result) -> tuple[list[np.ndarray], list[str], int] | None:
    """Split the first numeric variable by the grouping variable (≥2 groups)."""
    require_complete_inputs(ctx)
    var = ctx.config.variables[0]
    gvar = ctx.config.group_variable
    values = ctx.numeric(var)
    labels = ctx.categorical(gvar)
    n = min(len(values), len(labels))
    valid = [
        (values[i], str(labels[i]))
        for i in range(n)
        if not np.isnan(values[i]) and labels[i] is not None and labels[i] != ""
    ]
    excluded = (len(values) - n) + (n - len(valid))
    names = sorted({lbl for _, lbl in valid})
    if len(names) < 2:
        result.warnings.append(f"Grouping variable '{gvar}' needs ≥2 levels; found {len(names)}.")
        return None
    groups = [np.array([v for v, lbl in valid if lbl == name]) for name in names]
    result.sample = SampleInfo(
        original_n=len(values),
        excluded_n=excluded,
        final_n=len(valid),
        reason="rows with missing value/group removed",
        per_variable={var: 0, gvar: 0},
    )
    return groups, names, len(valid)


class _AnovaBase(StatisticalMethod):
    version = REJECT_MISSING_VERSION
    needs_group = True
    requires_numeric = True
    min_variables = 1
    max_variables = 1


class OneWayAnovaMethod(_AnovaBase):
    method_id = MethodId.ONE_WAY_ANOVA
    name = "One-way ANOVA"
    description = (
        "Compares means across ≥2 independent groups (assumes normality & equal variances)."
    )

    def execute(self, ctx):
        result = self.base_result(ctx)
        prep = _groups(ctx, result)
        if prep is None:
            return self.not_applicable(ctx, "Need ≥2 groups with valid values.")
        groups, names, n_total = prep
        for g, name in zip(groups, names):
            result.add_check(check_sample_size(len(g), 2))
            result.add_check(check_normality(g, f"{ctx.config.variables[0]}|{name}"))
        if any(len(g) < 2 for g in groups):
            return self.not_applicable(ctx, "Each group needs ≥2 values.")
        result.add_check(check_group_variance_homogeneity(groups))
        f, p = stats.f_oneway(*groups)
        k = len(groups)
        df_between = k - 1
        df_within = n_total - k
        # Mean square within for omega².
        ss_within = sum(float(np.sum((g - g.mean()) ** 2)) for g in groups)
        ms_within = ss_within / df_within if df_within > 0 else float("nan")
        eta2 = es.eta_squared(float(f), df_between, df_within)
        omega2 = es.omega_squared(float(f), df_between, df_within, ms_within)
        result.statistic = {
            "name": "F",
            "value": float(f),
            "df_between": df_between,
            "df_within": df_within,
        }
        result.p_value = float(p)
        result.effect_size = {"eta_squared": eta2, "omega_squared": omega2}
        result.estimates = {name: float(np.mean(g)) for name, g in zip(names, groups)}
        result.descriptive_statistics = {
            name: {"n": len(g), "mean": float(np.mean(g)), "std": float(np.std(g, ddof=1))}
            for name, g in zip(names, groups)
        }
        result.limitations.append(
            "A significant omnibus F does not say which groups differ; post-hoc tests "
            "(with multiple-comparison correction) are needed."
        )
        return result


class WelchAnovaMethod(_AnovaBase):
    method_id = MethodId.WELCH_ANOVA
    version = RESULT_METADATA_VERSION
    name = "Welch ANOVA"
    description = "One-way ANOVA robust to unequal variances and unequal group sizes."

    def execute(self, ctx):
        result = self.base_result(ctx)
        prep = _groups(ctx, result)
        if prep is None:
            return self.not_applicable(ctx, "Need ≥2 groups with valid values.")
        groups, names, n_total = prep
        for g in groups:
            if len(g) < 2 or float(np.var(g, ddof=1)) == 0:
                return self.not_applicable(
                    ctx, "Welch ANOVA needs ≥2 values with non-zero variance per group."
                )
        # Deterministic Welch ANOVA (Welch 1951); scipy's f_oneway has no
        # equal-variance option, so we implement it directly.
        f, p, df2 = _welch_anova(groups)
        k = len(groups)
        result.statistic = {
            "name": "F_welch",
            "value": float(f),
            "df_between": k - 1,
            "df_within": df2,
        }
        result.p_value = float(p)
        result.estimates = {name: float(np.mean(g)) for name, g in zip(names, groups)}
        result.limitations.append(
            "Welch ANOVA does not assume equal variances; report a robust effect size cautiously."
        )
        result.descriptive_statistics = {
            name: dict(n=len(g), mean=float(np.mean(g)), std=float(np.std(g, ddof=1)))
            for name, g in zip(names, groups, strict=True)
        }
        return result


def _welch_anova(groups: list[np.ndarray]) -> tuple[float, float, float]:
    """Manual Welch ANOVA (Welch 1951) as a robust fallback."""
    ns = np.array([len(g) for g in groups], dtype=float)
    means = np.array([float(np.mean(g)) for g in groups])
    vars_ = np.array([float(np.var(g, ddof=1)) for g in groups])
    w = ns / vars_
    sum_w = float(w.sum())
    mean_weighted = float((w * means).sum() / sum_w)
    k = len(groups)
    numerator = float((w * (means - mean_weighted) ** 2).sum() / (k - 1))
    lam = float((3 / (k * k - 1)) * ((1 - w / sum_w) ** 2 / (ns - 1)).sum())
    denom = 1 + (2 * (k - 2) / (k * k - 1)) * float(((1 - w / sum_w) ** 2 / (ns - 1)).sum())
    f = numerator / denom if denom else float("nan")
    df1 = k - 1
    # lam already equals 3*S/(k²-1); the denominator df is its reciprocal.
    df2 = 1 / lam if lam else float("nan")
    p = float(stats.f.sf(f, df1, df2))
    return f, p, df2


class KruskalWallisMethod(_AnovaBase):
    method_id = MethodId.KRUSKAL_WALLIS
    version = RESULT_METADATA_VERSION
    name = "Kruskal–Wallis H test"
    description = "Non-parametric comparison of ≥2 independent groups (rank-based)."

    def execute(self, ctx):
        result = self.base_result(ctx)
        prep = _groups(ctx, result)
        if prep is None:
            return self.not_applicable(ctx, "Need ≥2 groups with valid values.")
        groups, names, n_total = prep
        if any(len(g) < 1 for g in groups):
            return self.not_applicable(ctx, "Each group needs ≥1 value.")
        h, p = stats.kruskal(*groups)
        # Epsilon-squared effect size.
        epsilon2 = (
            (float(h) - len(groups) + 1) / (n_total - len(groups))
            if n_total > len(groups)
            else float("nan")
        )
        result.statistic = {"name": "H", "value": float(h), "df": len(groups) - 1}
        result.p_value = float(p)
        result.effect_size = {"epsilon_squared": epsilon2}
        result.descriptive_statistics = {
            name: dict(n=len(g)) for name, g in zip(names, groups, strict=True)
        }
        result.estimates = {name: float(np.median(g)) for name, g in zip(names, groups)}
        result.limitations.append(
            "Kruskal–Wallis tests distribution differences; follow up with Dunn's "
            "test (corrected) for pairwise comparisons."
        )
        return result


ONE_WAY_ANOVA = OneWayAnovaMethod()
WELCH_ANOVA = WelchAnovaMethod()
KRUSKAL_WALLIS = KruskalWallisMethod()
