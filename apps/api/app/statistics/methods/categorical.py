"""Categorical methods: chi-square independence / goodness-of-fit and Fisher's exact."""

from __future__ import annotations

from collections import Counter

import numpy as np
from scipy import stats

from app.statistics import effect_sizes as es
from app.statistics.assumptions import check_expected_frequencies, check_sample_size
from app.statistics.base import StatisticalMethod
from app.statistics.result import SampleInfo
from app.statistics.types import MethodId


def _contingency(col_a: list, col_b: list) -> tuple[np.ndarray, list, list, int, int]:
    """Build a contingency table from two aligned categorical lists.

    Returns (table, row_labels, col_labels, n_used, n_excluded).
    """
    n = min(len(col_a), len(col_b))
    pairs = [
        (col_a[i], col_b[i])
        for i in range(n)
        if col_a[i] is not None and col_b[i] is not None and col_a[i] != "" and col_b[i] != ""
    ]
    excluded = n - len(pairs)
    rows = sorted({str(p[0]) for p in pairs})
    cols = sorted({str(p[1]) for p in pairs})
    counts = Counter((str(a), str(b)) for a, b in pairs)
    table = np.array([[counts.get((r, c), 0) for c in cols] for r in rows], dtype=float)
    return table, rows, cols, len(pairs), excluded


class ChiSquareIndependenceMethod(StatisticalMethod):
    method_id = MethodId.CHI_SQUARE_INDEPENDENCE
    name = "Chi-square test of independence"
    description = "Tests association between two categorical variables via a contingency table."
    min_variables = 2
    max_variables = 2
    requires_numeric = False

    def validate(self, ctx):
        checks = []
        for v in ctx.config.variables:
            if not ctx.has_column(v):
                from app.statistics.result import AssumptionCheck

                checks.append(
                    AssumptionCheck("column_exists", False, f"Column '{v}' does not exist.")
                )
        return checks

    def execute(self, ctx):
        result = self.base_result(ctx)
        a_name, b_name = ctx.config.variables[0], ctx.config.variables[1]
        table, rows, cols, n_used, excluded = _contingency(
            ctx.categorical(a_name), ctx.categorical(b_name)
        )
        result.sample = SampleInfo(
            original_n=n_used + excluded,
            excluded_n=excluded,
            final_n=n_used,
            reason="rows with missing category removed",
            per_variable={a_name: 0, b_name: 0},
        )
        if table.size == 0 or table.shape[0] < 2 or table.shape[1] < 2:
            return self.not_applicable(ctx, "Need a contingency table with ≥2 rows and ≥2 columns.")
        result.add_check(check_sample_size(n_used, 1))
        chi2, p, dof, expected = stats.chi2_contingency(table)
        result.add_check(check_expected_frequencies(expected))
        v = es.cramers_v(table)
        result.statistic = {"name": "chi2", "value": float(chi2), "df": int(dof)}
        result.p_value = float(p)
        result.effect_size = {"cramers_v": v}
        result.estimates = {
            "rows": rows,
            "columns": cols,
            "observed": table.tolist(),
            "expected": np.asarray(expected).tolist(),
        }
        result.limitations.append(
            "Chi-square tests association, not causation; expected counts <5 "
            "weaken the approximation."
        )
        return result


class ChiSquareGofMethod(StatisticalMethod):
    method_id = MethodId.CHI_SQUARE_GOF
    name = "Chi-square goodness-of-fit"
    description = "Tests whether one categorical variable follows specified expected proportions."
    min_variables = 1
    max_variables = 1
    requires_numeric = False

    def execute(self, ctx):
        result = self.base_result(ctx)
        name = ctx.config.variables[0]
        values = [v for v in ctx.categorical(name) if v is not None and v != ""]
        counts = Counter(str(v) for v in values)
        categories = sorted(counts.keys())
        if len(categories) < 2:
            return self.not_applicable(ctx, "Need ≥2 categories for goodness-of-fit.")
        n = len(values)
        result.sample = SampleInfo(n, 0, n, "no missing handling", {name: 0})
        props = ctx.config.expected_proportions
        if props:
            # Align provided proportions to observed categories.
            missing = [c for c in categories if c not in props]
            if missing:
                return self.not_applicable(
                    ctx, f"Expected proportions missing for categories: {', '.join(missing)}."
                )
            total_p = sum(props[c] for c in categories)
            if total_p <= 0:
                return self.not_applicable(ctx, "Expected proportions must sum to > 0.")
            expected = np.array([n * props[c] / total_p for c in categories], dtype=float)
        else:
            expected = np.full(len(categories), n / len(categories), dtype=float)
        observed = np.array([counts[c] for c in categories], dtype=float)
        result.add_check(check_expected_frequencies(expected.reshape(1, -1)))
        chi2, p = stats.chisquare(observed, f_exp=expected)
        result.statistic = {"name": "chi2", "value": float(chi2), "df": len(categories) - 1}
        result.p_value = float(p)
        result.effect_size = {"w": float(np.sqrt(float(chi2) / n)) if n else None}
        result.estimates = {
            "categories": categories,
            "observed": observed.tolist(),
            "expected": expected.tolist(),
        }
        return result


class FisherExactMethod(StatisticalMethod):
    method_id = MethodId.FISHER_EXACT
    name = "Fisher's exact test"
    description = "Exact test of association for a 2×2 table (preferred for small samples)."
    min_variables = 2
    max_variables = 2
    requires_numeric = False

    def execute(self, ctx):
        result = self.base_result(ctx)
        a_name, b_name = ctx.config.variables[0], ctx.config.variables[1]
        table, rows, cols, n_used, excluded = _contingency(
            ctx.categorical(a_name), ctx.categorical(b_name)
        )
        result.sample = SampleInfo(n_used + excluded, excluded, n_used, "rows with missing removed")
        if table.shape != (2, 2):
            return self.not_applicable(
                ctx, f"Fisher's exact test needs a 2×2 table; got {table.shape}."
            )
        alt = {"two_sided": "two-sided", "less": "less", "greater": "greater"}[
            ctx.config.alternative.value
        ]
        odds_ratio, p = stats.fisher_exact(table, alternative=alt)
        result.statistic = {"name": "odds_ratio", "value": float(odds_ratio)}
        result.p_value = float(p)
        result.effect_size = {"odds_ratio": float(odds_ratio)}
        result.estimates = {
            "rows": rows,
            "columns": cols,
            "observed": table.tolist(),
        }
        result.limitations.append(
            "Fisher's exact test is exact for fixed margins; odds ratio can be "
            "huge with zero cells."
        )
        return result


CHI_SQUARE_INDEPENDENCE = ChiSquareIndependenceMethod()
CHI_SQUARE_GOF = ChiSquareGofMethod()
FISHER_EXACT = FisherExactMethod()
