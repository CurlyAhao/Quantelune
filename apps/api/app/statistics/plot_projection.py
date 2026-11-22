"""Pure, finite projections of samples already authorised and filtered upstream.

These functions do not apply missing-value policies or calculate statistical
inference. Display limits never change the full distribution summary or the
ordered row identities bound by the scatter selection hash.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from numbers import Integral, Real
from typing import Any

import numpy as np

PROJECTION_VERSION = "plot-projection-v1"
SCATTER_SELECTION_RULE = "sha256(seed:row_index)-rank-v1"
HISTOGRAM_CLOSURE = "left-closed-right-open,last-right-closed"


def _integer(
    value: int, name: str, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer.")
    result = int(value)
    if minimum is not None and result < minimum or maximum is not None and result > maximum:
        raise ValueError(f"{name} is outside the supported range.")
    return result


def _finite_float(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number.")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be a finite real number.") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite real number.")
    return result


def distribution_summary(
    values: Sequence[float], *, bins: int = 12, outlier_limit: int = 200
) -> dict[str, Any]:
    """Summarise every observation using linear quartiles and Tukey fences.

    Histogram bins are equal-width, left-closed/right-open, with the final bin
    right-closed. Constant ranges use NumPy's fixed half-unit expansion. Only
    the displayed outlier values are truncated, in their original input order.
    """
    bins = _integer(bins, "bins", minimum=1, maximum=100)
    outlier_limit = _integer(outlier_limit, "outlier_limit", minimum=0, maximum=200)
    sample = np.asarray([_finite_float(value, "value") for value in values], dtype=float)
    if sample.size == 0:
        raise ValueError("The distribution sample must not be empty.")

    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            counts, edges = np.histogram(sample, bins=bins)
            q1, median, q3 = (
                float(value) for value in np.quantile(sample, [0.25, 0.5, 0.75], method="linear")
            )
    except (FloatingPointError, OverflowError, ValueError) as error:
        raise ValueError("The distribution cannot produce a finite projection.") from error

    iqr = q3 - q1
    lower_fence, upper_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    edge_values = edges.tolist()
    if (
        not all(math.isfinite(value) for value in [q1, median, q3, lower_fence, upper_fence])
        or not all(math.isfinite(value) for value in edge_values)
        or not all(left < right for left, right in zip(edge_values, edge_values[1:]))
        or int(counts.sum()) != int(sample.size)
    ):
        raise ValueError("The distribution cannot produce a finite projection.")

    outlier_mask = (sample < lower_fence) | (sample > upper_fence)
    inside_fences = sample[~outlier_mask]
    outliers = sample[outlier_mask][:outlier_limit].tolist()
    return {
        "n": int(sample.size),
        "histogram": {
            "edges": edge_values,
            "counts": counts.tolist(),
            "bins": bins,
            "closure": HISTOGRAM_CLOSURE,
        },
        "box": {
            "q1": q1,
            "median": median,
            "q3": q3,
            "lower_whisker": float(inside_fences.min()),
            "upper_whisker": float(inside_fences.max()),
            "lower_fence": lower_fence,
            "upper_fence": upper_fence,
            "outlier_count": int(np.count_nonzero(outlier_mask)),
            "outliers": outliers,
            "outliers_displayed": len(outliers),
        },
        "algorithm_version": PROJECTION_VERSION,
    }


def select_scatter(
    rows: Sequence[tuple[int, float, float]], *, limit: int = 2000, seed: int = 1729
) -> dict[str, Any]:
    """Select display rows by SHA256 rank, preserving original row order.

    The selection hash binds all valid input row identities, including rows
    outside the display limit. Duplicate coordinates remain separate points.
    """
    limit = _integer(limit, "limit", minimum=1, maximum=2000)
    seed = _integer(seed, "seed")
    validated: list[tuple[int, float, float]] = []
    previous_index = -1
    for row in rows:
        try:
            row_index, x, y = row
        except (TypeError, ValueError) as error:
            raise ValueError("Each scatter row must contain row_index, x, and y.") from error
        row_index = _integer(row_index, "row_index", minimum=0)
        if row_index <= previous_index:
            raise ValueError("Scatter row indices must be unique and strictly increasing.")
        validated.append((row_index, _finite_float(x, "x"), _finite_float(y, "y")))
        previous_index = row_index
    if not validated:
        raise ValueError("The scatter sample must not be empty.")

    identities = [row[0] for row in validated]
    selection_sha256 = hashlib.sha256(
        json.dumps(identities, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    displayed = validated
    if len(validated) > limit:
        ranked = sorted(
            validated,
            key=lambda row: (
                hashlib.sha256(f"{seed}:{row[0]}".encode()).digest(),
                row[0],
            ),
        )
        displayed = sorted(ranked[:limit], key=lambda row: row[0])

    return {
        "points": [{"row_index": index, "x": x, "y": y} for index, x, y in displayed],
        "total_n": len(validated),
        "displayed_n": len(displayed),
        "seed": seed,
        "rule": SCATTER_SELECTION_RULE,
        "selection_sha256": selection_sha256,
        "algorithm_version": PROJECTION_VERSION,
    }
