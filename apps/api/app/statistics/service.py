"""Executor: validate then run a statistical method with error isolation."""

from __future__ import annotations

import logging
import warnings

import numpy as np
import polars as pl

from app.statistics.context import MethodConfig, StatisticalContext
from app.statistics.registry import METHOD_REGISTRY
from app.statistics.result import StatisticalMethodResult
from app.statistics.types import MethodStatus

logger = logging.getLogger("statforge.statistics")


def run_statistical_analysis(
    df: pl.DataFrame,
    config: MethodConfig,
    dataset_sha256: str = "",
) -> StatisticalMethodResult:
    """Run a single statistical method against a dataframe.

    Applicability failures produce a ``not_applicable`` result; unexpected
    exceptions produce a ``failed`` result. Either way the call never raises.
    """
    method = METHOD_REGISTRY.get(config.method_id)
    ctx = StatisticalContext(df=df, config=config, dataset_sha256=dataset_sha256)

    # Applicability checks — if any required check fails, skip cleanly.
    checks = method.validate(ctx)
    blocking = [c for c in checks if not c.passed]

    try:
        if blocking:
            result = method.not_applicable(ctx, "; ".join(c.detail for c in blocking))
            result.assumption_checks = checks
            return result

        # Degenerate numeric input can trip numpy/scipy RuntimeWarnings; results
        # remain JSON-safe via json_safe, so we surface them quietly rather than
        # letting them pollute output.
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore", RuntimeWarning)
            result = method.execute(ctx)
        # Merge validation checks that the method didn't already add.
        existing = {c.name for c in result.assumption_checks}
        for c in checks:
            if c.name not in existing:
                result.add_check(c)
        return result
    except ValueError as exc:
        # Known domain errors (e.g. error_on_missing policy violation).
        logger.info("Statistical method %s aborted: %s", config.method_id.value, exc)
        return _failed(method, ctx, "METHOD_ABORTED", str(exc))
    except Exception as exc:  # noqa: BLE001 - isolate any unexpected failure
        logger.exception("Statistical method %s failed", config.method_id.value)
        return _failed(method, ctx, "METHOD_FAILED", f"{type(exc).__name__}: {exc}")


def _failed(method, ctx: StatisticalContext, code: str, message: str) -> StatisticalMethodResult:
    result = method.base_result(ctx)
    result.status = MethodStatus.FAILED
    result.error_code = code
    result.error_message = message[:2000]
    return result
