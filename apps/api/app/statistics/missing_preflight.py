"""Versioned rejection semantics for methods that previously deleted missing rows.

Constants stay cheap to import from the live research capability guard. Numeric
libraries are loaded only while executing a method against the actual frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.statistics.context import StatisticalContext

REJECT_MISSING_VERSION = "1.1.0"
WELCH_ANOVA_VERSION = "1.1.1"
RESULT_METADATA_VERSION = "1.2.0"
RESULT_METADATA_METHODS = frozenset(
    {"student_t", "mann_whitney", "welch_anova", "kruskal_wallis", "linear_regression"}
)
NEW_REJECT_MISSING_METHODS = frozenset(
    {
        "student_t",
        "mann_whitney",
        "one_way_anova",
        "welch_anova",
        "kruskal_wallis",
        "linear_regression",
    }
)


def recorded_reject_missing_supported(method: str, version: str) -> bool:
    return method in NEW_REJECT_MISSING_METHODS and (
        version == REJECT_MISSING_VERSION
        or method == "welch_anova"
        and version == WELCH_ANOVA_VERSION
        or method in RESULT_METADATA_METHODS
        and version == RESULT_METADATA_VERSION
    )


def require_complete_inputs(ctx: StatisticalContext) -> None:
    """Reject invalid participating values before a method can delete or fit rows."""
    if (
        ctx.config.method_id.value not in NEW_REJECT_MISSING_METHODS
        or ctx.config.missing_policy.value != "error_on_missing"
    ):
        return
    import numpy as np
    import pandas as pd

    regression = ctx.config.method_id.value == "linear_regression"
    if regression:
        target = ctx.config.target
        numeric = [name for name in ctx.config.variables if name != target]
        categorical = [
            name
            for name in ctx.config.categorical_variables
            if name != target and name not in numeric
        ]
        numeric = [target, *numeric]
    else:
        numeric = list(ctx.config.variables[:1])
        categorical = [ctx.config.group_variable]
    for name in [*numeric, *categorical]:
        if not name or not ctx.has_column(name):
            raise ValueError("error_on_missing requires every participating column to exist")
    for name in numeric:
        # OLS uses pandas coercion in _build_frame and for its target; check the
        # same conversion before _design_matrix has a chance to drop rows.
        values = (
            pd.to_numeric(ctx.df[name].to_pandas(), errors="coerce").to_numpy(dtype=float)
            if regression
            else ctx.numeric(name)
        )
        if not np.isfinite(values).all():
            raise ValueError(f"error_on_missing: missing or non-finite numeric values in '{name}'")
    for name in categorical:
        values = ctx.categorical(name)
        if any(pd.isna(value) or (not regression and value == "") for value in values):
            raise ValueError(f"error_on_missing: missing categorical/group values in '{name}'")
