"""Metric computation for classification and regression."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
    mean_absolute_error,
    mean_absolute_percentage_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)

from app.ml.types import MLTaskType


def _safe(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def classification_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: np.ndarray | None,
    labels: list,
) -> dict:
    """Compute the full classification metric set (binary + multiclass aware)."""
    binary = len(labels) == 2
    avg = "binary" if binary else "macro"
    pos_label = labels[1] if binary else None

    metrics: dict = {
        "accuracy": _safe(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": _safe(balanced_accuracy_score(y_true, y_pred)),
        "precision": _safe(
            precision_score(y_true, y_pred, average=avg, pos_label=pos_label, zero_division=0)
        ),
        "recall": _safe(
            recall_score(y_true, y_pred, average=avg, pos_label=pos_label, zero_division=0)
        ),
        "f1": _safe(f1_score(y_true, y_pred, average=avg, pos_label=pos_label, zero_division=0)),
    }
    # ROC-AUC / PR-AUC / log-loss need probabilities.
    if y_proba is not None:
        try:
            if binary:
                positive = np.asarray(y_true) == labels[1]
                metrics["roc_auc"] = _safe(roc_auc_score(positive, y_proba[:, 1]))
                metrics["pr_auc"] = _safe(average_precision_score(positive, y_proba[:, 1]))
            else:
                metrics["roc_auc"] = _safe(
                    roc_auc_score(y_true, y_proba, multi_class="ovr", average="macro")
                )
                metrics["pr_auc"] = None
            metrics["log_loss"] = _safe(log_loss(y_true, y_proba, labels=labels))
        except Exception:  # noqa: BLE001
            metrics.setdefault("roc_auc", None)
            metrics.setdefault("pr_auc", None)
            metrics.setdefault("log_loss", None)
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    metrics["confusion_matrix"] = cm.tolist()
    metrics["labels"] = [str(lab) for lab in labels]
    return metrics


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Compute the regression metric set."""
    mse = mean_squared_error(y_true, y_pred)
    metrics: dict = {
        "mae": _safe(mean_absolute_error(y_true, y_pred)),
        "mse": _safe(mse),
        "rmse": _safe(np.sqrt(mse)),
        "r2": _safe(r2_score(y_true, y_pred)),
    }
    # MAPE only when there are no zero true values (else undefined/huge).
    if np.all(np.asarray(y_true) != 0):
        metrics["mape"] = _safe(mean_absolute_percentage_error(y_true, y_pred))
    else:
        metrics["mape"] = None
    residuals = np.asarray(y_true) - np.asarray(y_pred)
    metrics["residual_std"] = _safe(float(np.std(residuals)))
    return metrics


def compute_metrics(
    task_type: MLTaskType,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: np.ndarray | None = None,
    labels: list | None = None,
) -> dict:
    if task_type == MLTaskType.REGRESSION:
        return regression_metrics(y_true, y_pred)
    return classification_metrics(y_true, y_pred, y_proba, labels or [])


def primary_metric(task_type: MLTaskType) -> str:
    """The metric used for scoring during cross-validation."""
    if task_type == MLTaskType.REGRESSION:
        return "r2"
    if task_type == MLTaskType.BINARY_CLASSIFICATION:
        return "roc_auc"
    return "accuracy"
