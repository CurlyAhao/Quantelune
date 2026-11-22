"""Deterministic effect-size computations.

Effect sizes are reported alongside p-values so results are not reduced to
significance alone. Each function returns a plain float (NaN-safe upstream).
"""

from __future__ import annotations

import math

import numpy as np


def _safe(value: float) -> float:
    return float(value) if np.isfinite(value) else float("nan")


def cohens_d(group1: np.ndarray, group2: np.ndarray, *, paired: bool = False) -> float:
    """Cohen's d (pooled-SD standardised mean difference)."""
    if paired:
        if len(group1) != len(group2):
            return float("nan")
        diff = group1 - group2
        sd = float(np.std(diff, ddof=1))
        return _safe(float(np.mean(diff)) / sd) if sd > 0 else float("nan")
    n1, n2 = len(group1), len(group2)
    if n1 < 2 or n2 < 2:
        return float("nan")
    v1 = float(np.var(group1, ddof=1))
    v2 = float(np.var(group2, ddof=1))
    pooled = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    diff = float(np.mean(group1) - np.mean(group2))
    return _safe(diff / pooled) if pooled > 0 else float("nan")


def hedges_g(group1: np.ndarray, group2: np.ndarray) -> float:
    """Hedges' g — small-sample bias-corrected Cohen's d."""
    d = cohens_d(group1, group2)
    n1, n2 = len(group1), len(group2)
    df = n1 + n2 - 2
    if df <= 0 or not np.isfinite(d):
        return float("nan")
    correction = 1 - 3 / (4 * df - 1)
    return _safe(d * correction)


def rank_biserial(u_statistic: float, n1: int, n2: int) -> float:
    """Rank-biserial correlation from a Mann–Whitney U statistic."""
    if n1 <= 0 or n2 <= 0:
        return float("nan")
    return _safe(2 * u_statistic / (n1 * n2) - 1)


def cramers_v(contingency: np.ndarray) -> float:
    """Cramér's V from a contingency table and its chi-square statistic."""
    table = np.asarray(contingency, dtype=float)
    n = float(table.sum())
    if n == 0:
        return float("nan")
    row_sum = table.sum(axis=1, keepdims=True)
    col_sum = table.sum(axis=0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        expected = row_sum @ col_sum / n
        chi2 = float(np.where(expected > 0, (table - expected) ** 2 / expected, 0).sum())
    r, k = table.shape
    denom = min(r - 1, k - 1)
    if denom <= 0:
        return float("nan")
    return _safe(math.sqrt(chi2 / (n * denom)))


def eta_squared(f_statistic: float, df_between: int, df_within: int) -> float:
    """Eta squared (η²) from a one-way ANOVA F statistic."""
    total_df = df_between + df_within
    if total_df <= 0:
        return float("nan")
    return _safe((f_statistic * df_between) / (f_statistic * df_between + df_within))


def omega_squared(f_statistic: float, df_between: int, df_within: int, ms_within: float) -> float:
    """Omega squared (ω²) — less biased than η²."""
    denom = f_statistic * df_between + df_within + 1
    if denom <= 0 or ms_within <= 0:
        return float("nan")
    num = (f_statistic - 1) * df_between * ms_within
    return _safe(num / (denom * ms_within))


def interpret_cohens_d(d: float) -> str:
    """Conventional (Cohen 1988) magnitude label — heuristic, not a rule."""
    if not np.isfinite(d):
        return "undefined"
    a = abs(d)
    if a < 0.2:
        return "negligible"
    if a < 0.5:
        return "small"
    if a < 0.8:
        return "medium"
    return "large"
