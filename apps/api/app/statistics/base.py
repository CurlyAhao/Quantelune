"""Method abstraction shared by every statistical method."""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod

import numpy as np
import polars as pl
import scipy
import statsmodels

from app.statistics.context import StatisticalContext
from app.statistics.result import AssumptionCheck, StatisticalMethodResult
from app.statistics.types import MethodId, MethodStatus


def software_versions() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "statsmodels": statsmodels.__version__,
        "polars": pl.__version__,
    }


class StatisticalMethod(ABC):
    """A deterministic statistical method.

    Subclasses declare ``method_id`` plus variable requirements and implement
    :meth:`execute`. :meth:`validate` returns applicability findings; the
    executor uses them to skip a method cleanly rather than crash.
    """

    method_id: MethodId
    version: str = "1.0.0"
    name: str = ""
    description: str = ""
    min_variables: int = 1
    max_variables: int = 1
    needs_group: bool = False
    requires_numeric: bool = True
    # Inferential methods cannot run on a constant (zero-variance) variable;
    # descriptive summaries set this True since a constant column is fine.
    allow_constant: bool = False

    @staticmethod
    def _is_constant(ctx: StatisticalContext, name: str) -> bool:
        arr = ctx.numeric(name)
        finite = arr[np.isfinite(arr)]
        return finite.size > 0 and float(np.nanstd(finite)) == 0.0

    def base_result(self, ctx: StatisticalContext) -> StatisticalMethodResult:
        return StatisticalMethodResult(
            method=self.method_id.value,
            method_version=self.version,
            alternative=ctx.config.alternative.value,
            confidence_level=ctx.config.confidence_level,
            software_versions=software_versions(),
        )

    def _effective_variable_count(self, ctx: StatisticalContext) -> int:
        """Number of predictors supplied; regression adds categorical predictors."""
        return len(ctx.config.variables)

    def validate(self, ctx: StatisticalContext) -> list[AssumptionCheck]:
        """Default applicability checks; methods may extend."""
        checks: list[AssumptionCheck] = []
        variables = list(ctx.config.variables)
        n_predictors = self._effective_variable_count(ctx)
        if n_predictors < self.min_variables:
            checks.append(
                AssumptionCheck(
                    name="min_variables",
                    passed=False,
                    detail=f"{self.name} needs at least {self.min_variables} predictor(s).",
                    value=n_predictors,
                    threshold=self.min_variables,
                )
            )
        for v in variables:
            if not ctx.has_column(v):
                checks.append(
                    AssumptionCheck(
                        name="column_exists",
                        passed=False,
                        detail=f"Column '{v}' does not exist.",
                    )
                )
            elif self.requires_numeric and not ctx.is_numeric_column(v):
                checks.append(
                    AssumptionCheck(
                        name="numeric_type",
                        passed=False,
                        detail=f"Column '{v}' is not numeric.",
                    )
                )
            elif self.requires_numeric and not self.allow_constant and self._is_constant(ctx, v):
                checks.append(
                    AssumptionCheck(
                        name="non_constant",
                        passed=False,
                        detail=(
                            f"Column '{v}' is constant (zero variance); {self.name} is undefined."
                        ),
                    )
                )
        if self.needs_group and not ctx.config.group_variable:
            checks.append(
                AssumptionCheck(
                    name="group_variable",
                    passed=False,
                    detail=f"{self.name} requires a grouping variable.",
                )
            )
        return checks

    @abstractmethod
    def execute(self, ctx: StatisticalContext) -> StatisticalMethodResult:
        """Run the method and return a fully-populated result."""

    def not_applicable(self, ctx: StatisticalContext, reason: str) -> StatisticalMethodResult:
        result = self.base_result(ctx)
        result.status = MethodStatus.NOT_APPLICABLE
        result.warnings.append(reason)
        return result
