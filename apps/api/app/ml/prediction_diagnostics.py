"""Pure bounded aggregates from actual, already aligned model predictions.

Probability columns follow the supplied complete class-label order. Calibration
uses ten [lower, upper) bins (the last includes 1). ``positive_fraction`` denotes
the positive-label event for binary tasks and top-label correctness for multiclass.
Regression residuals are observed minus predicted. No per-row data is returned.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve

from app.ml.types import MLTaskType


def _vector(values: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(values)
    if array.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional array.")
    return array


def _label(value: Any) -> str | int | float | bool:
    if isinstance(value, np.generic):
        value = value.item()
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("Class labels must be finite JSON scalar values.")
    if isinstance(value, (int, float)) and not np.isfinite(value):
        raise ValueError("Class labels must be finite JSON scalar values.")
    return value


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _probabilities(values: np.ndarray, n_samples: int, n_classes: int) -> np.ndarray:
    try:
        proba = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Probabilities must be a finite numeric matrix.") from exc
    if proba.shape != (n_samples, n_classes):
        raise ValueError("Probability rows and columns must align with samples and labels.")
    if not np.all(np.isfinite(proba)) or np.any(proba < 0) or np.any(proba > 1):
        raise ValueError("Probabilities must be finite values in [0, 1].")
    if not np.allclose(proba.sum(axis=1), 1, rtol=1e-6, atol=1e-8):
        raise ValueError("Each probability row must sum to one.")
    return proba


def _calibration(scores: np.ndarray, positive: np.ndarray, mode: str) -> dict[str, Any]:
    edges = np.linspace(0, 1, 11)
    bins = []
    for index in range(10):
        mask = (scores >= edges[index]) & (
            scores <= edges[index + 1] if index == 9 else scores < edges[index + 1]
        )
        count = int(mask.sum())
        bins.append(
            {
                "lower": float(edges[index]),
                "upper": float(edges[index + 1]),
                "n": count,
                "mean_predicted_probability": float(scores[mask].mean()) if count else None,
                "positive_fraction": float(positive[mask].mean()) if count else None,
            }
        )
    return {"status": "available", "mode": mode, "bins": bins}


def _curve(
    first: np.ndarray,
    second: np.ndarray,
    thresholds: list[float | None],
    names: tuple[str, str],
    max_points: int,
) -> dict[str, Any]:
    original = len(first)
    indices = (
        np.linspace(0, original - 1, max_points, dtype=int)
        if original > max_points
        else np.arange(original)
    )
    return {
        "status": "available",
        names[0]: [float(first[index]) for index in indices],
        names[1]: [float(second[index]) for index in indices],
        "thresholds": [thresholds[index] for index in indices],
        "n_points_original": original,
        "downsampled": original > max_points,
    }


def _classification(
    task_type: MLTaskType,
    actual: np.ndarray,
    predicted: np.ndarray,
    probabilities: np.ndarray | None,
    labels: list,
    max_points: int,
) -> dict[str, Any]:
    classes = [_label(value) for value in labels]
    binary = task_type == MLTaskType.BINARY_CLASSIFICATION
    if len(set(classes)) != len(classes) or len(classes) < 2 or (binary and len(classes) != 2):
        raise ValueError("Classification needs a complete, unique class-label list.")
    # Encode by the supplied labels rather than sorting: string positive labels
    # and arbitrary probability-column order remain explicit and consistent.
    label_to_index = {label: index for index, label in enumerate(classes)}
    try:
        actual_indices = np.array([label_to_index[_label(value)] for value in actual], dtype=int)
        predicted_indices = np.array(
            [label_to_index[_label(value)] for value in predicted], dtype=int
        )
    except KeyError as exc:
        raise ValueError("All observed and predicted classes must occur in labels.") from exc
    proba = (
        None if probabilities is None else _probabilities(probabilities, len(actual), len(classes))
    )
    if len(actual) == 0:
        return _unavailable("empty_evaluation_set")
    result: dict[str, Any] = {
        "labels": classes,
        "confusion_matrix": confusion_matrix(
            actual_indices, predicted_indices, labels=np.arange(len(classes))
        ).tolist(),
    }
    if proba is None:
        result["status"] = "partial"
        result["calibration"] = _unavailable("probabilities_unavailable")
        if binary:
            result["roc"] = _unavailable("probabilities_unavailable")
            result["pr"] = _unavailable("probabilities_unavailable")
        return result
    result["status"] = "available"
    if not binary:
        result["calibration"] = _calibration(
            proba.max(axis=1), proba.argmax(axis=1) == actual_indices, "top_label_correctness"
        )
        return result
    positive = actual_indices == 1
    scores = proba[:, 1]
    result["calibration"] = _calibration(scores, positive, "binary_positive_class")
    if len(np.unique(actual_indices)) != 2:
        result["status"] = "partial"
        result["roc"] = _unavailable("single_class_test_set")
        result["pr"] = _unavailable("single_class_test_set")
        return result
    fpr, tpr, roc_thresholds = roc_curve(positive, scores, pos_label=True, drop_intermediate=False)
    precision, recall, pr_thresholds = precision_recall_curve(positive, scores, pos_label=True)
    result["roc"] = _curve(
        fpr,
        tpr,
        [float(value) if np.isfinite(value) else None for value in roc_thresholds],
        ("fpr", "tpr"),
        max_points,
    )
    # sklearn PR has one endpoint without a threshold; null represents that
    # absence before selecting matching point and threshold indices.
    result["pr"] = _curve(
        precision,
        recall,
        [*[float(value) for value in pr_thresholds], None],
        ("precision", "recall"),
        max_points,
    )
    return result


def _regression(actual: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    try:
        observed = np.asarray(actual, dtype=float)
        fitted = np.asarray(predicted, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Regression observations and predictions must be numeric.") from exc
    if not np.all(np.isfinite(observed)) or not np.all(np.isfinite(fitted)):
        raise ValueError("Regression observations and predictions must be finite.")
    try:
        with np.errstate(over="raise", invalid="raise"):
            residual = observed - fitted
            mse = float(np.square(residual).mean())
            counts, edges = np.histogram(residual, bins=20)
            probabilities = np.array([0, 0.25, 0.5, 0.75, 1])
            aggregate = {
                "histogram": {"edges": edges.tolist(), "counts": counts.tolist()},
                "quantiles": {
                    "probabilities": probabilities.tolist(),
                    "y_true": np.quantile(observed, probabilities).tolist(),
                    "y_pred": np.quantile(fitted, probabilities).tolist(),
                    "residual": np.quantile(residual, probabilities).tolist(),
                },
                "errors": {
                    "mae": float(np.abs(residual).mean()),
                    "mse": mse,
                    "rmse": float(np.sqrt(mse)),
                    "mean_residual": float(residual.mean()),
                    "residual_std": float(residual.std()),
                },
            }
    except (FloatingPointError, OverflowError) as exc:
        raise ValueError("Regression aggregates exceed finite numeric range.") from exc
    return {"status": "available", "regression": aggregate}


def prediction_diagnostics(
    task_type: MLTaskType,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: np.ndarray | None,
    labels: list,
    *,
    max_points: int = 201,
) -> dict[str, Any]:
    """Project actual predictions into strict JSON; never fit or infer a model.

    Invalid shapes, labels or finite numeric domains raise ValueError. Empty
    evaluation sets are unavailable; missing probabilities or a binary test set
    containing one class remain partial with the genuine confusion matrix.
    """
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or not 2 <= max_points <= 201
    ):
        raise ValueError("max_points must be an integer between 2 and 201.")
    actual = _vector(y_true, "y_true")
    predicted = _vector(y_pred, "y_pred")
    if len(actual) != len(predicted):
        raise ValueError("Observed and predicted samples must align.")
    result: dict[str, Any] = {
        "version": 1,
        "kind": task_type.value,
        "n_evaluated": len(actual),
    }
    if task_type == MLTaskType.REGRESSION:
        if y_proba is not None:
            raise ValueError("Regression diagnostics do not accept class probabilities.")
        result.update(
            _unavailable("empty_evaluation_set")
            if len(actual) == 0
            else _regression(actual, predicted)
        )
    else:
        result.update(_classification(task_type, actual, predicted, y_proba, labels, max_points))
    json.dumps(result, allow_nan=False)
    return result
