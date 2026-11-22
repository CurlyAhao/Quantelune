"""ML experiment executor: split → CV → fit → evaluate → interpret → compare.

Returns a JSON-serialisable result plus the fitted final pipeline per model (so
the orchestration layer can persist artifacts). Preprocessing is fit only on
training data (inside the pipeline), preventing leakage.
"""

from __future__ import annotations

import sys
import time
import warnings
from dataclasses import replace
from hashlib import sha256

import numpy as np
import pandas as pd
import polars as pl
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.model_selection import cross_validate
from sklearn.pipeline import Pipeline

from app.ml.allocation import allocate_evaluation
from app.ml.config import MLConfig
from app.ml.interpretation import model_interpretation
from app.ml.leakage import run_leakage_checks
from app.ml.metrics import _safe, compute_metrics, primary_metric
from app.ml.models import build_estimator
from app.ml.prediction_diagnostics import prediction_diagnostics
from app.ml.preflight import valid_target_mask
from app.ml.preprocessing import build_pipeline
from app.ml.research_protocol import build_evaluation_protocol
from app.ml.resources import validate_frame_resources
from app.ml.splits import (
    uses_cross_validation,
)
from app.ml.types import (
    MAX_MODELS_PER_RUN,
    MLModelId,
    MLTaskType,
)


def _software_versions() -> dict[str, str]:
    import sklearn

    return {
        "python": sys.version.split()[0],
        "scikit_learn": sklearn.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "polars": pl.__version__,
        "ml_engine": "1.2.0",
    }


def _prepare_xy(
    df: pl.DataFrame, config: MLConfig
) -> tuple[pd.DataFrame, np.ndarray, list[str], np.ndarray]:
    """Build X (pandas DataFrame, mixed types), y, feature list, and kept row index.

    X is a DataFrame so the ColumnTransformer can select columns by name.
    `row_index` maps each kept sample back to its original dataframe row so
    group labels stay aligned after dropping rows with a null target.
    """
    feature_cols = [f for f in config.features if f in df.columns]
    frame = df.select([config.target, *feature_cols]).to_pandas()
    # Shared keep mask (null and NaN) — same rows as preflight training stats.
    keep = valid_target_mask(df.get_column(config.target))
    row_index = np.flatnonzero(keep)
    frame = frame.loc[keep]
    y = frame[config.target].to_numpy()
    X = frame[feature_cols].copy()
    # Coerce numeric columns to float; leave categorical columns as-is.
    for c in config.numeric_features():
        if c in X.columns:
            X[c] = X[c].astype(float)
    return X, y, feature_cols, row_index


def _cv_scoring(task_type: MLTaskType) -> str:
    if task_type == MLTaskType.REGRESSION:
        return "r2"
    if task_type == MLTaskType.BINARY_CLASSIFICATION:
        return "roc_auc"
    return "accuracy"


def run_ml_experiment(
    df: pl.DataFrame, config: MLConfig, *, dataset_sha256: str = ""
) -> tuple[dict, dict[str, Pipeline]]:
    """Run the full experiment for every configured model. Never raises for a
    single model failure — that model is recorded as failed and others continue.
    If no candidate completes, the overall run is failed."""
    started = time.time()
    # Compat: legacy split payloads list categorical columns only in
    # categorical_features; the engine trains on the merged feature set.
    config = replace(config, features=config.merged_features())
    validate_frame_resources(df, config)
    X, y, feature_cols, row_index = _prepare_xy(df, config)
    n = len(y)

    # Validate / refine the task type against the target.
    if config.task_type != MLTaskType.REGRESSION:
        n_classes = len(np.unique(y))
        if n_classes < 2:
            return {
                "status": "failed",
                "error_code": "ML_NEEDS_2_CLASSES",
                "error_message": "Classification needs a target with ≥2 classes.",
                "software_versions": _software_versions(),
            }, {}
        if config.task_type == MLTaskType.BINARY_CLASSIFICATION and n_classes != 2:
            return {
                "status": "failed",
                "error_code": "ML_NOT_BINARY",
                "error_message": f"binary_classification expects 2 classes, found {n_classes}.",
                "software_versions": _software_versions(),
            }, {}

    labels = sorted(np.unique(y).tolist()) if config.task_type != MLTaskType.REGRESSION else []

    allocation = allocate_evaluation(df, config, X, y, row_index)
    groups_all = allocation.groups
    train_idx, val_idx, test_idx = allocation.train, allocation.validation, allocation.test
    cv_splits = allocation.cv_folds

    X_train, y_train = X.iloc[train_idx], y[train_idx]
    X_test, y_test = X.iloc[test_idx], y[test_idx]
    X_val = X.iloc[val_idx] if val_idx is not None else None
    y_val = y[val_idx] if val_idx is not None else None

    # Leakage checks (explainable). Use kept-sample group labels + original row ids.
    leakage = run_leakage_checks(
        df,
        config,
        train_idx,
        test_idx,
        groups=groups_all,
        row_index=row_index,
    )

    model_ids = list(config.model_ids)[:MAX_MODELS_PER_RUN]
    if not model_ids:
        model_ids = (
            [MLModelId.RANDOM_FOREST]
            if config.task_type != MLTaskType.REGRESSION
            else [MLModelId.RANDOM_FOREST_REGRESSOR]
        )

    model_results = []
    fitted_pipelines: dict[str, Pipeline] = {}
    scoring = _cv_scoring(config.task_type)
    if not dataset_sha256:
        # Direct engine probes have no uploaded bytes. Their exact immutable
        # in-memory frame receives its own identity, never a guessed dataset SHA.
        dataset_sha256 = sha256(df.write_ipc(file=None).getvalue()).hexdigest()
    protocol = build_evaluation_protocol(
        config,
        dataset_sha256=dataset_sha256,
        row_index=row_index,
        train_idx=train_idx,
        validation_idx=val_idx,
        test_idx=test_idx,
        cv_splits=cv_splits,
        software_versions=_software_versions(),
    )
    protocol_id = protocol["protocol_id"]
    baseline = _baseline(
        config, X_train, y_train, X_test, y_test, labels, cv_splits, scoring, protocol_id
    )

    for model_id in model_ids:
        m_start = time.time()
        try:
            estimator = build_estimator(
                model_id,
                config.task_type,
                config.random_seed,
                config.model_params.get(model_id.value),
            )
            pipeline = build_pipeline(config, estimator)

            cv_info: dict = {
                "strategy": config.split_strategy.value,
                "n_splits": None,
                "per_fold": [],
                "mean": None,
                "std": None,
            }
            if uses_cross_validation(config):
                cv_kwargs = {"cv": cv_splits, "scoring": scoring, "error_score": "raise"}
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    cv = cross_validate(pipeline, X_train, y_train, **cv_kwargs)
                scores = cv["test_score"]
                cv_info["n_splits"] = len(scores)
                cv_info["per_fold"] = [_safe(s) for s in scores]
                cv_info["mean"] = _safe(np.mean(scores))
                cv_info["std"] = _safe(np.std(scores))

            # Final fit on the training portion.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pipeline.fit(X_train, y_train)

            # Validation metrics (train_val_test only).
            val_metrics = None
            val_diagnostics = None
            if X_val is not None and y_val is not None:
                val_pred = pipeline.predict(X_val)
                val_proba = _probabilities(pipeline, X_val, labels)
                val_metrics = compute_metrics(config.task_type, y_val, val_pred, val_proba, labels)
                val_diagnostics = prediction_diagnostics(
                    config.task_type, y_val, val_pred, val_proba, labels
                )

            # Test metrics.
            test_pred = pipeline.predict(X_test)
            test_proba = _probabilities(pipeline, X_test, labels)
            test_metrics = compute_metrics(config.task_type, y_test, test_pred, test_proba, labels)
            diagnostics = prediction_diagnostics(
                config.task_type, y_test, test_pred, test_proba, labels
            )

            interp = model_interpretation(
                pipeline,
                model_id,
                config,
                X_test=X_test,
                y_test=y_test,
                task_type=config.task_type,
                seed=config.random_seed,
            )

            overfit_gap = None
            if cv_info["mean"] is not None:
                train_score = cv_info["mean"]
                test_score = test_metrics.get(primary_metric(config.task_type))
                if test_score is not None:
                    overfit_gap = float(train_score - test_score)

            # A late prediction/interpretation failure must not serialize an
            # otherwise fitted but failed candidate into a downloadable model.
            fitted_pipelines[model_id.value] = pipeline
            model_results.append(
                {
                    "model_id": model_id.value,
                    "evaluation_protocol_id": protocol_id,
                    "status": "completed",
                    "cv": cv_info,
                    "validation_metrics": val_metrics,
                    "validation_prediction_diagnostics": val_diagnostics,
                    "test_metrics": test_metrics,
                    "interpretation": interp,
                    "prediction_diagnostics": diagnostics,
                    "overfit_gap": overfit_gap,
                    "training_seconds": round(time.time() - m_start, 3),
                }
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-model failures
            model_results.append(
                {
                    "model_id": model_id.value,
                    "evaluation_protocol_id": protocol_id,
                    "status": "failed",
                    "error_message": f"{type(exc).__name__}: {exc}"[:1000],
                    "training_seconds": round(time.time() - m_start, 3),
                }
            )

    comparison = _compare(model_results, config.task_type, protocol_id=protocol_id)
    comparison["baseline"] = baseline

    # Zero successful candidates must fail the overall run: no completed
    # status, no pipelines, therefore no serialized artifact downstream.
    if not model_results or all(m["status"] == "failed" for m in model_results):
        failures = "; ".join(
            f"{m['model_id']}: {m.get('error_message', '')}" for m in model_results
        )
        return {
            "status": "failed",
            "error_code": "ML_ALL_MODELS_FAILED",
            "error_message": (
                f"All candidate models failed. {failures}"[:1000]
                if failures
                else "No candidate models were executed."
            ),
            "task_type": config.task_type.value,
            "n_observations": n,
            "n_features": len(feature_cols),
            "features": feature_cols,
            "labels": labels,
            "split": {
                "strategy": config.split_strategy.value,
                "n_train": int(len(train_idx)),
                "n_val": int(len(val_idx)) if val_idx is not None else 0,
                "n_test": int(len(test_idx)),
                "random_seed": config.random_seed,
                "evaluation_protocol": protocol,
            },
            "leakage_checks": leakage,
            "models": model_results,
            "comparison": comparison,
            "software_versions": _software_versions(),
            "total_seconds": round(time.time() - started, 3),
        }, {}

    return {
        "status": "completed",
        "task_type": config.task_type.value,
        "n_observations": n,
        "n_features": len(feature_cols),
        "features": feature_cols,
        "labels": labels,
        "split": {
            "strategy": config.split_strategy.value,
            "n_train": int(len(train_idx)),
            "n_val": int(len(val_idx)) if val_idx is not None else 0,
            "n_test": int(len(test_idx)),
            "random_seed": config.random_seed,
            "evaluation_protocol": protocol,
        },
        "leakage_checks": leakage,
        "models": model_results,
        "comparison": comparison,
        "software_versions": _software_versions(),
        "total_seconds": round(time.time() - started, 3),
    }, fitted_pipelines


def _probabilities(pipeline: Pipeline, frame: pd.DataFrame, labels: list):
    if not hasattr(pipeline, "predict_proba"):
        return None
    classes = list(pipeline.classes_)
    if len(classes) != len(labels) or set(classes) != set(labels):
        raise ValueError("Model probability classes do not match the evaluation labels.")
    predicted = pipeline.predict_proba(frame)
    return predicted[:, [classes.index(label) for label in labels]]


def _baseline(config, X_train, y_train, X_test, y_test, labels, cv_splits, scoring, identity):
    strategy = "mean" if config.task_type == MLTaskType.REGRESSION else "prior"
    estimator = (
        DummyRegressor(strategy="mean") if strategy == "mean" else DummyClassifier(strategy="prior")
    )
    pipeline = build_pipeline(config, estimator)
    try:
        cv_info = {"per_fold": [], "mean": None, "std": None}
        if cv_splits:
            cv = cross_validate(
                pipeline, X_train, y_train, cv=cv_splits, scoring=scoring, error_score="raise"
            )
            scores = cv["test_score"]
            cv_info = {
                "per_fold": [_safe(value) for value in scores],
                "mean": _safe(np.mean(scores)),
                "std": _safe(np.std(scores)),
            }
        pipeline.fit(X_train, y_train)
        prediction = pipeline.predict(X_test)
        probability = _probabilities(pipeline, X_test, labels)
        return {
            "status": "completed",
            "strategy": strategy,
            "evaluation_protocol_id": identity,
            "cv": cv_info,
            "test_metrics": compute_metrics(
                config.task_type, y_test, prediction, probability, labels
            ),
            "prediction_diagnostics": prediction_diagnostics(
                config.task_type, y_test, prediction, probability, labels
            ),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "strategy": strategy,
            "evaluation_protocol_id": identity,
            "error_type": type(exc).__name__,
        }


def _compare(
    model_results: list[dict], task_type: MLTaskType, *, protocol_id: str | None = None
) -> dict:
    """Multi-metric comparison — ranks by the primary metric but surfaces
    trade-offs; it never declares a single objective 'best' model."""
    completed = [m for m in model_results if m["status"] == "completed"]
    primary = primary_metric(task_type)
    rows = []
    for m in completed:
        test = m["test_metrics"] or {}
        rows.append(
            {
                "model_id": m["model_id"],
                "primary_metric": primary,
                "primary_value": test.get(primary),
                "metrics": test,
                "cv_mean": (m["cv"] or {}).get("mean"),
                "cv_std": (m["cv"] or {}).get("std"),
                "overfit_gap": m.get("overfit_gap"),
                "training_seconds": m.get("training_seconds"),
                "evaluation_protocol_id": m.get("evaluation_protocol_id"),
                "comparable": bool(
                    bool(protocol_id)
                    and m.get("evaluation_protocol_id") == protocol_id
                    and isinstance(test.get(primary), (int, float))
                    and np.isfinite(test[primary])
                ),
            }
        )

    def sort_key(r: dict) -> float:
        v = r["primary_value"]
        return v if isinstance(v, (int, float)) and v is not None else float("-inf")

    ranked = sorted([row for row in rows if row["comparable"]], key=sort_key, reverse=True)
    for i, r in enumerate(ranked):
        r["rank_by_primary"] = i + 1
    ranked.extend(row for row in rows if not row["comparable"])

    return {
        "primary_metric": primary,
        "rows": ranked,
        "protocol_verified": bool(protocol_id)
        and all(m.get("evaluation_protocol_id") == protocol_id for m in completed),
        "evaluation_protocol_id": protocol_id,
        "note": (
            "Models are ranked by the primary metric only. Consider calibration, "
            "variance, overfit gap, training cost and interpretability before choosing — "
            "no single metric defines the 'best' model."
        ),
    }
