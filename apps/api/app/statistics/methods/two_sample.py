"""Two-group comparison methods.

Covers one-sample, independent (Welch / Student) and paired t-tests plus the
non-parametric Mann–Whitney U and Wilcoxon signed-rank tests. Independent tests
take one numeric variable + a two-level grouping variable; paired tests take two
aligned numeric variables.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from app.statistics import effect_sizes as es
from app.statistics.assumptions import (
    check_constant,
    check_normality,
    check_outliers,
    check_sample_size,
    check_variance_homogeneity,
)
from app.statistics.base import StatisticalMethod
from app.statistics.context import StatisticalContext, apply_missing_policy
from app.statistics.missing_preflight import RESULT_METADATA_VERSION, require_complete_inputs
from app.statistics.types import MIN_SAMPLE_SIZE, Alternative, MethodId, MissingPolicy

_ALT_MAP = {
    Alternative.TWO_SIDED: "two-sided",
    Alternative.LESS: "less",
    Alternative.GREATER: "greater",
}


def _split_by_group(ctx: StatisticalContext, result) -> tuple[np.ndarray, np.ndarray, list] | None:
    """Split the first variable by a two-level grouping variable."""
    require_complete_inputs(ctx)
    var = ctx.config.variables[0]
    gvar = ctx.config.group_variable
    if gvar is None:
        return None
    values = ctx.numeric(var)
    groups = ctx.categorical(gvar)
    n = min(len(values), len(groups))
    values, groups = values[:n], groups[:n]
    # Keep rows with both a value and a non-null group label.
    labels = np.array([g for g in groups], dtype=object)
    mask = np.array(
        [not np.isnan(values[i]) and labels[i] is not None and labels[i] != "" for i in range(n)]
    )
    values, labels = values[mask], labels[mask]
    unique = sorted({str(lbl) for lbl in labels})
    if len(unique) != 2:
        result.warnings.append(
            f"Grouping variable '{gvar}' must have exactly 2 levels; found {len(unique)}."
        )
        return None
    a = values[np.array([str(lbl) == unique[0] for lbl in labels])]
    b = values[np.array([str(lbl) == unique[1] for lbl in labels])]
    original = int((~mask).sum()) + (len(ctx.numeric(var)) - n)
    result.sample = _sample(len(ctx.numeric(var)), original, len(a) + len(b), var, gvar)
    return a, b, unique


def _sample(original_n, excluded_n, final_n, var, gvar=None):
    from app.statistics.result import SampleInfo

    per = {var: excluded_n}
    if gvar:
        per[gvar] = 0
    return SampleInfo(original_n, excluded_n, final_n, "rows with missing value/group removed", per)


class _TwoGroupBase(StatisticalMethod):
    needs_group = True
    requires_numeric = True
    min_variables = 1
    max_variables = 1

    def _prepare(self, ctx, result):
        split = _split_by_group(ctx, result)
        if split is None:
            return None
        a, b, labels = split
        result.add_check(check_sample_size(len(a), 2))
        result.add_check(check_sample_size(len(b), 2))
        const_a = check_constant(a, f"{ctx.config.variables[0]}|{labels[0]}")
        const_b = check_constant(b, f"{ctx.config.variables[0]}|{labels[1]}")
        result.add_check(const_a)
        result.add_check(const_b)
        if len(a) < 2 or len(b) < 2:
            return None
        if not const_a.passed or not const_b.passed:
            return None
        return a, b, labels


class WelchTMethod(_TwoGroupBase):
    method_id = MethodId.WELCH_T
    version = "1.1.0"
    name = "Welch two-sample t-test"
    description = (
        "Compares two independent group means without assuming equal variances (default choice)."
    )

    def execute(self, ctx):
        result = self.base_result(ctx)
        raw = ctx.numeric(ctx.config.variables[0])
        groups = ctx.categorical(ctx.config.group_variable)
        if ctx.config.missing_policy == MissingPolicy.ERROR_ON_MISSING and (
            np.isnan(raw).any() or any(group is None or group == "" for group in groups)
        ):
            raise ValueError("error_on_missing policy: missing value or group present.")
        if np.isinf(raw).any():
            return self.not_applicable(ctx, "Welch's t-test requires finite numeric values.")
        prep = self._prepare(ctx, result)
        if prep is None:
            return self.not_applicable(ctx, "Need two groups with ≥2 valid values each.")
        a, b, labels = prep
        variance_a = np.var(a, ddof=1)
        variance_b = np.var(b, ddof=1)
        term_a, term_b = variance_a / len(a), variance_b / len(b)
        se_squared = term_a + term_b
        mean_a, mean_b = float(np.mean(a)), float(np.mean(b))
        difference = mean_a - mean_b
        if not np.isfinite(se_squared) or se_squared <= 0 or not np.isfinite(difference):
            return self.not_applicable(
                ctx, "Welch mean difference or standard error is not estimable."
            )
        df = se_squared**2 / (term_a**2 / (len(a) - 1) + term_b**2 / (len(b) - 1))
        standard_error = float(np.sqrt(se_squared))
        level = ctx.config.confidence_level
        if not np.isfinite(df) or df <= 0 or not np.isfinite(level) or not 0 < level < 1:
            return self.not_applicable(
                ctx, "Welch degrees of freedom or confidence level is invalid."
            )
        alternative = ctx.config.alternative
        probability = (1 + level) / 2 if alternative == Alternative.TWO_SIDED else level
        margin = float(stats.t.ppf(probability, df)) * standard_error
        lower_unbounded = alternative == Alternative.LESS
        upper_unbounded = alternative == Alternative.GREATER
        lower = None if lower_unbounded else difference - margin
        upper = None if upper_unbounded else difference + margin
        if any(bound is not None and not np.isfinite(bound) for bound in (lower, upper)):
            return self.not_applicable(ctx, "Welch confidence interval is not estimable.")
        result.add_check(check_normality(a, f"{ctx.config.variables[0]}|{labels[0]}"))
        result.add_check(check_normality(b, f"{ctx.config.variables[0]}|{labels[1]}"))
        result.add_check(check_variance_homogeneity(a, b))
        t, p = stats.ttest_ind(a, b, equal_var=False, alternative=_ALT_MAP[ctx.config.alternative])
        if not np.isfinite(t) or not np.isfinite(p):
            return self.not_applicable(ctx, "Welch test statistic or p-value is not estimable.")
        d = es.cohens_d(a, b)
        result.statistic = {"name": "t", "value": float(t), "df": float(df)}
        result.p_value = float(p)
        result.estimates = {
            f"mean_{labels[0]}": mean_a,
            f"mean_{labels[1]}": mean_b,
            "mean_difference": difference,
        }
        result.confidence_interval = {
            "schema_version": 1,
            "estimand": "mean_difference",
            "group_order": labels,
            "direction": "group_1_minus_group_2",
            "confidence_level": level,
            "alternative": alternative.value,
            "df": float(df),
            "standard_error": standard_error,
            "lower": lower,
            "upper": upper,
            "lower_unbounded": lower_unbounded,
            "upper_unbounded": upper_unbounded,
            "method": "welch_satterthwaite",
        }
        result.effect_size = {
            "cohens_d": d,
            "hedges_g": es.hedges_g(a, b),
            "magnitude": es.interpret_cohens_d(d),
        }
        result.descriptive_statistics = {
            labels[0]: {"n": len(a), "mean": mean_a, "std": float(np.sqrt(variance_a))},
            labels[1]: {"n": len(b), "mean": mean_b, "std": float(np.sqrt(variance_b))},
        }
        return result


class StudentTMethod(_TwoGroupBase):
    method_id = MethodId.STUDENT_T
    version = RESULT_METADATA_VERSION
    name = "Student two-sample t-test"
    description = (
        "Compares two independent group means assuming equal variances (use only when justified)."
    )

    def execute(self, ctx):
        result = self.base_result(ctx)
        prep = self._prepare(ctx, result)
        if prep is None:
            return self.not_applicable(ctx, "Need two groups with ≥2 valid values each.")
        a, b, labels = prep
        var_check = check_variance_homogeneity(a, b)
        result.add_check(var_check)
        if not var_check.passed:
            result.warnings.append(
                "Equal-variance assumption looks violated; Welch's t-test is safer."
            )
        result.add_check(check_normality(a, f"{ctx.config.variables[0]}|{labels[0]}"))
        result.add_check(check_normality(b, f"{ctx.config.variables[0]}|{labels[1]}"))
        t, p = stats.ttest_ind(a, b, equal_var=True, alternative=_ALT_MAP[ctx.config.alternative])
        df = len(a) + len(b) - 2
        mean_a, mean_b = float(np.mean(a)), float(np.mean(b))
        difference = mean_a - mean_b
        pooled_variance = ((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1)) / df
        se_squared = pooled_variance * (1 / len(a) + 1 / len(b))
        level = ctx.config.confidence_level
        if (
            not np.isfinite(se_squared)
            or se_squared <= 0
            or not np.isfinite(difference)
            or not np.isfinite(level)
            or not 0 < level < 1
        ):
            return self.not_applicable(
                ctx, "Student mean difference, standard error or confidence level is not estimable."
            )
        standard_error = float(np.sqrt(se_squared))
        alternative = ctx.config.alternative
        probability = (1 + level) / 2 if alternative == Alternative.TWO_SIDED else level
        margin = float(stats.t.ppf(probability, df)) * standard_error
        lower_unbounded = alternative == Alternative.LESS
        upper_unbounded = alternative == Alternative.GREATER
        lower = None if lower_unbounded else difference - margin
        upper = None if upper_unbounded else difference + margin
        if (
            not np.isfinite(t)
            or not np.isfinite(p)
            or any(bound is not None and not np.isfinite(bound) for bound in (lower, upper))
        ):
            return self.not_applicable(
                ctx, "Student statistic, p-value or confidence interval is not estimable."
            )
        d = es.cohens_d(a, b)
        result.statistic = {"name": "t", "value": float(t), "df": float(df)}
        result.p_value = float(p)
        result.estimates = {"mean_difference": difference}
        result.confidence_interval = dict(
            schema_version=1,
            estimand="mean_difference",
            group_order=labels,
            direction="group_1_minus_group_2",
            confidence_level=level,
            alternative=alternative.value,
            df=float(df),
            standard_error=standard_error,
            lower=lower,
            upper=upper,
            lower_unbounded=lower_unbounded,
            upper_unbounded=upper_unbounded,
            method="student_pooled",
        )
        result.descriptive_statistics = {
            label: dict(n=len(group), mean=float(np.mean(group)), std=float(np.std(group, ddof=1)))
            for label, group in zip(labels, (a, b), strict=True)
        }
        result.effect_size = {
            "cohens_d": d,
            "hedges_g": es.hedges_g(a, b),
            "magnitude": es.interpret_cohens_d(d),
        }
        return result


class MannWhitneyMethod(_TwoGroupBase):
    method_id = MethodId.MANN_WHITNEY
    version = RESULT_METADATA_VERSION
    name = "Mann–Whitney U test"
    description = (
        "Non-parametric comparison of two independent groups (ranks; no normality assumption)."
    )

    def execute(self, ctx):
        result = self.base_result(ctx)
        prep = self._prepare(ctx, result)
        if prep is None:
            return self.not_applicable(ctx, "Need two groups with ≥2 valid values each.")
        a, b, labels = prep
        result.add_check(check_outliers(a, f"{ctx.config.variables[0]}|{labels[0]}"))
        result.add_check(check_outliers(b, f"{ctx.config.variables[0]}|{labels[1]}"))
        ties = np.unique(np.concatenate((a, b))).size < len(a) + len(b)
        # Resolve SciPy's documented one-dimensional auto selection explicitly,
        # then record the method actually passed to the numerical implementation.
        p_method = "asymptotic" if min(len(a), len(b)) > 8 or ties else "exact"
        u, p = stats.mannwhitneyu(
            a, b, alternative=_ALT_MAP[ctx.config.alternative], method=p_method, use_continuity=True
        )
        rb = es.rank_biserial(float(u), len(a), len(b))
        result.statistic = dict(
            name="U",
            value=float(u),
            group_1=labels[0],
            group_2=labels[1],
            U_for_group=labels[0],
            p_value_selection="auto",
            p_value_method=p_method,
            continuity_correction=p_method == "asymptotic",
            ties_present=bool(ties),
            tie_correction=p_method == "asymptotic" and bool(ties),
        )
        result.descriptive_statistics = {
            label: dict(n=len(group)) for label, group in zip(labels, (a, b), strict=True)
        }
        result.p_value = float(p)
        result.effect_size = {"rank_biserial": rb}
        result.estimates = {
            f"median_{labels[0]}": float(np.median(a)),
            f"median_{labels[1]}": float(np.median(b)),
        }
        result.limitations.append(
            "Mann–Whitney tests stochastic dominance / distribution shift, not medians per se."
        )
        return result


class OneSampleTMethod(StatisticalMethod):
    method_id = MethodId.ONE_SAMPLE_T
    name = "One-sample t-test"
    description = "Tests whether a numeric variable's mean differs from a reference value (μ₀)."
    min_variables = 1
    max_variables = 1
    requires_numeric = True

    def execute(self, ctx):
        result = self.base_result(ctx)
        name = ctx.config.variables[0]
        raw = ctx.numeric(name)
        valid = raw[~np.isnan(raw)]
        result.sample = _sample(len(raw), len(raw) - len(valid), len(valid), name)
        result.add_check(check_sample_size(len(valid), MIN_SAMPLE_SIZE[self.method_id]))
        result.add_check(check_constant(valid, name))
        result.add_check(check_normality(valid, name))
        if len(valid) < 2:
            return self.not_applicable(ctx, "Need ≥2 valid values for a one-sample t-test.")
        mu = ctx.config.mu
        t, p = stats.ttest_1samp(valid, mu, alternative=_ALT_MAP[ctx.config.alternative])
        df = len(valid) - 1
        # Cohen's d against the reference value.
        sd = float(np.std(valid, ddof=1))
        d = (float(np.mean(valid)) - mu) / sd if sd > 0 else float("nan")
        result.statistic = {"name": "t", "value": float(t), "df": float(df)}
        result.p_value = float(p)
        result.estimates = {
            "mean": float(np.mean(valid)),
            "mu0": mu,
            "mean_minus_mu0": float(np.mean(valid)) - mu,
        }
        result.effect_size = {"cohens_d": d, "magnitude": es.interpret_cohens_d(d)}
        return result


class PairedTMethod(StatisticalMethod):
    method_id = MethodId.PAIRED_T
    name = "Paired t-test"
    description = (
        "Compares two paired/repeated measurements (provide two aligned numeric variables)."
    )
    min_variables = 2
    max_variables = 2
    requires_numeric = True

    def execute(self, ctx):
        result = self.base_result(ctx)
        x_name, y_name = ctx.config.variables[0], ctx.config.variables[1]
        arrays = {x_name: ctx.numeric(x_name), y_name: ctx.numeric(y_name)}
        cleaned, info = apply_missing_policy(arrays, ctx.config.missing_policy, paired=True)
        x, y = cleaned[x_name], cleaned[y_name]
        n = min(len(x), len(y))
        x, y = x[:n], y[:n]
        info.final_n = n
        result.sample = info
        result.add_check(check_sample_size(n, MIN_SAMPLE_SIZE[self.method_id]))
        diff = x - y
        result.add_check(check_normality(diff, f"{x_name}-{y_name}"))
        if n < 2:
            return self.not_applicable(ctx, "Need ≥2 complete pairs for a paired t-test.")
        t, p = stats.ttest_rel(x, y, alternative=_ALT_MAP[ctx.config.alternative])
        df = n - 1
        d = es.cohens_d(x, y, paired=True)
        result.statistic = {"name": "t", "value": float(t), "df": float(df)}
        result.p_value = float(p)
        result.estimates = {"mean_difference": float(np.mean(diff))}
        result.effect_size = {"cohens_d_paired": d, "magnitude": es.interpret_cohens_d(d)}
        return result


class WilcoxonMethod(StatisticalMethod):
    method_id = MethodId.WILCOXON
    name = "Wilcoxon signed-rank test"
    description = "Non-parametric test for two paired measurements (ranks of differences)."
    min_variables = 2
    max_variables = 2
    requires_numeric = True

    def execute(self, ctx):
        result = self.base_result(ctx)
        x_name, y_name = ctx.config.variables[0], ctx.config.variables[1]
        arrays = {x_name: ctx.numeric(x_name), y_name: ctx.numeric(y_name)}
        cleaned, info = apply_missing_policy(arrays, ctx.config.missing_policy, paired=True)
        x, y = cleaned[x_name], cleaned[y_name]
        n = min(len(x), len(y))
        x, y = x[:n], y[:n]
        info.final_n = n
        result.sample = info
        diff = x - y
        nonzero = diff[diff != 0]
        result.add_check(check_sample_size(len(nonzero), MIN_SAMPLE_SIZE[self.method_id]))
        if len(nonzero) < 2:
            return self.not_applicable(ctx, "Need ≥2 non-zero paired differences.")
        w, p = stats.wilcoxon(x, y, alternative=_ALT_MAP[ctx.config.alternative])
        # Matched-pairs rank-biserial effect size.
        rb = 1 - (2 * float(w)) / (len(nonzero) * (len(nonzero) + 1) / 2)
        result.statistic = {"name": "W", "value": float(w)}
        result.p_value = float(p)
        result.effect_size = {"rank_biserial": rb}
        result.estimates = {"median_difference": float(np.median(diff))}
        return result


ONE_SAMPLE_T = OneSampleTMethod()
WELCH_T = WelchTMethod()
STUDENT_T = StudentTMethod()
PAIRED_T = PairedTMethod()
MANN_WHITNEY = MannWhitneyMethod()
WILCOXON = WilcoxonMethod()
