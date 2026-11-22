"""Deterministic assumption / applicability checks.

These produce *informational* checks. A normality test, for example, never
mechanically decides which method to use — it only reports a hint with the
well-known caveat that large samples make such tests overly sensitive.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from app.statistics.result import AssumptionCheck

# Normality tests become unreliable / overly sensitive beyond this size.
NORMALITY_MAX_N = 5000


def check_sample_size(n: int, minimum: int) -> AssumptionCheck:
    passed = n >= minimum
    return AssumptionCheck(
        name="sample_size",
        passed=passed,
        detail=(
            f"n={n} meets the minimum of {minimum}."
            if passed
            else f"n={n} is below the minimum of {minimum}."
        ),
        value=n,
        threshold=minimum,
    )


def check_constant(values: np.ndarray, name: str) -> AssumptionCheck:
    finite = values[np.isfinite(values)]
    is_constant = finite.size > 0 and float(np.nanstd(finite)) == 0.0
    return AssumptionCheck(
        name="non_constant",
        passed=not is_constant,
        detail=(
            f"'{name}' is constant (zero variance); many statistics are undefined."
            if is_constant
            else f"'{name}' varies."
        ),
        value={"std": float(np.nanstd(finite)) if finite.size else 0.0},
    )


def check_normality(values: np.ndarray, name: str) -> AssumptionCheck:
    """Shapiro–Wilk on a capped sample, with an explicit sensitivity caveat."""
    finite = values[np.isfinite(values)]
    if finite.size < 3:
        return AssumptionCheck(
            name="normality_hint",
            passed=True,
            detail=f"Too few values in '{name}' to assess normality.",
            value=None,
        )
    sample = finite[:NORMALITY_MAX_N] if finite.size > NORMALITY_MAX_N else finite
    try:
        stat, p = stats.shapiro(sample)
    except Exception:  # noqa: BLE001 - scipy can fail on degenerate input
        return AssumptionCheck(
            name="normality_hint",
            passed=True,
            detail=f"Normality of '{name}' could not be assessed.",
        )
    caveat = (
        " With large samples normality tests are overly sensitive; inspect "
        "plots and robustness rather than relying on this p-value."
        if finite.size > 50
        else ""
    )
    return AssumptionCheck(
        name="normality_hint",
        passed=bool(p >= 0.05),
        detail=(f"Shapiro–Wilk W={float(stat):.4f}, p={float(p):.4g} for '{name}'.{caveat}"),
        value={"statistic": float(stat), "p_value": float(p), "n": int(finite.size)},
        threshold=0.05,
    )


def check_variance_homogeneity(group_a: np.ndarray, group_b: np.ndarray) -> AssumptionCheck:
    """Levene's test for equality of variances (informational)."""
    a = group_a[np.isfinite(group_a)]
    b = group_b[np.isfinite(group_b)]
    if a.size < 2 or b.size < 2:
        return AssumptionCheck(
            name="equal_variance_hint",
            passed=True,
            detail="Too few values to assess variance homogeneity.",
        )
    try:
        stat, p = stats.levene(a, b, center="median")
    except Exception:  # noqa: BLE001
        return AssumptionCheck(
            name="equal_variance_hint",
            passed=True,
            detail="Variance homogeneity could not be assessed.",
        )
    return AssumptionCheck(
        name="equal_variance_hint",
        passed=bool(p >= 0.05),
        detail=(
            f"Levene W={float(stat):.4f}, p={float(p):.4g}; "
            + (
                "no evidence of variance differences was detected."
                if p >= 0.05
                else "variances differ — prefer Welch's correction."
            )
        ),
        value={"statistic": float(stat), "p_value": float(p)},
        threshold=0.05,
    )


def check_group_variance_homogeneity(groups: list[np.ndarray]) -> AssumptionCheck:
    """Assess all supplied groups; incomplete coverage is explicitly unassessed."""
    finite = [group[np.isfinite(group)] for group in groups]
    coverage = {
        "group_count": len(groups),
        "finite_counts": [int(group.size) for group in finite],
        "assessed": False,
    }

    def not_assessed(reason: str) -> AssumptionCheck:
        return AssumptionCheck(
            name="equal_variance_hint",
            passed=False,
            detail=f"Levene test not assessed for all {len(groups)} groups: {reason}",
            value=coverage,
            threshold=0.05,
        )

    if len(finite) < 2 or any(group.size < 2 for group in finite):
        return not_assessed("each of at least two groups needs two finite observations.")
    try:
        statistic, p_value = stats.levene(*finite, center="median")
    except Exception:  # noqa: BLE001
        return not_assessed("the variance test could not be evaluated.")
    if not np.isfinite(statistic) or not np.isfinite(p_value):
        return not_assessed("the variance test returned a non-finite result.")
    return AssumptionCheck(
        name="equal_variance_hint",
        passed=bool(p_value >= 0.05),
        detail=(
            f"Levene W={float(statistic):.4f}, p={float(p_value):.4g} across "
            f"all {len(groups)} groups; "
            + (
                "no evidence of variance differences was detected."
                if p_value >= 0.05
                else "evidence against equal variances; consider Welch ANOVA."
            )
        ),
        value={
            **coverage,
            "assessed": True,
            "statistic": float(statistic),
            "p_value": float(p_value),
        },
        threshold=0.05,
    )


def check_expected_frequencies(expected: np.ndarray) -> AssumptionCheck:
    """Chi-square validity: expected cell counts should mostly exceed 5."""
    expected = np.asarray(expected, dtype=float)
    total = expected.size
    below = int((expected < 5).sum())
    if total == 0:
        return AssumptionCheck(
            name="expected_frequencies", passed=False, detail="Empty contingency table."
        )
    passed = below <= 0.2 * total and float(expected.min()) >= 1
    detail = f"{below}/{total} cells have expected count < 5" + (
        "" if passed else "; chi-square approximation may be poor — consider Fisher's exact test."
    )
    return AssumptionCheck(
        name="expected_frequencies",
        passed=passed,
        detail=detail,
        value={"cells_below_5": below, "min_expected": float(expected.min())},
        threshold=5,
    )


def check_outliers(values: np.ndarray, name: str) -> AssumptionCheck:
    """Count extreme outliers via the 1.5/3.0 × IQR fences."""
    finite = values[np.isfinite(values)]
    if finite.size < 4:
        return AssumptionCheck(
            name="extreme_outliers", passed=True, detail=f"Too few values in '{name}'."
        )
    q1, q3 = np.percentile(finite, [25, 75])
    iqr = q3 - q1
    if iqr == 0:
        return AssumptionCheck(
            name="extreme_outliers", passed=True, detail=f"No spread in '{name}'."
        )
    lower, upper = q1 - 3 * iqr, q3 + 3 * iqr
    n_extreme = int(((finite < lower) | (finite > upper)).sum())
    return AssumptionCheck(
        name="extreme_outliers",
        passed=n_extreme == 0,
        detail=(
            f"{n_extreme} extreme outlier(s) beyond 3×IQR in '{name}'."
            if n_extreme
            else f"No extreme outliers in '{name}'."
        ),
        value={"extreme_count": n_extreme},
    )
