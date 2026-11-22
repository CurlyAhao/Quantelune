"""Model interpretation: coefficients, feature importances, permutation importance."""

from __future__ import annotations

import numpy as np
from sklearn.inspection import permutation_importance
from sklearn.pipeline import Pipeline

from app.ml.config import MLConfig
from app.ml.models import is_linear_model, is_tree_model
from app.ml.types import MLModelId, MLTaskType


def transformed_feature_names(pipeline: Pipeline) -> list[str]:
    try:
        names = pipeline[:-1].get_feature_names_out()
        return [str(n) for n in names]
    except Exception:  # noqa: BLE001
        return []


def _rollup_to_original(names: list[str], values: np.ndarray) -> dict[str, float]:
    """Aggregate one-hot-expanded columns back to their original feature."""
    out: dict[str, float] = {}
    for name, val in zip(names, values):
        original = name.split("__", 1)[1] if "__" in name else name
        # num/cat prefixes from ColumnTransformer.
        for prefix in ("num__", "cat__"):
            if original.startswith(prefix):
                original = original[len(prefix) :]
        out[original] = out.get(original, 0.0) + abs(float(val))
    return out


def original_transformed_names(pipeline: Pipeline) -> list[str]:
    """Use fitted transformer allocations, not ambiguous one-hot name parsing."""
    preprocess = pipeline.named_steps.get("preprocess")
    if preprocess is None:
        return []
    names: list[str] = []
    for name, transformer, columns in preprocess.transformers_:
        if name == "remainder" or transformer == "drop":
            continue
        if name == "num":
            names.extend(str(value) for value in transformer.get_feature_names_out(columns))
        elif name == "cat":
            actual = transformer.named_steps["impute"].get_feature_names_out(columns)
            categories = transformer.named_steps["encode"].categories_
            names.extend(str(column) for column, values in zip(actual, categories) for _ in values)
    selector = pipeline.named_steps.get("select")
    if selector is not None:
        names = [names[int(index)] for index in selector.get_support(indices=True)]
    return names


def _sum_original(names: list[str], values: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in zip(names, values):
        out[name] = out.get(name, 0.0) + abs(float(value))
    return out


def model_interpretation(
    pipeline: Pipeline,
    model_id: MLModelId,
    config: MLConfig,
    X_test=None,  # noqa: ANN001
    y_test: np.ndarray | None = None,
    task_type: MLTaskType = MLTaskType.REGRESSION,
    seed: int = 42,
) -> dict:
    """Return coefficients / importances + (optional) permutation importance."""
    model = pipeline.named_steps["model"]
    names = transformed_feature_names(pipeline)
    original_names = original_transformed_names(pipeline)
    result: dict = {"method": None, "features": {}, "original_features": {}, "permutation": {}}

    if is_linear_model(model_id) and hasattr(model, "coef_"):
        coef = np.asarray(model.coef_).ravel()
        if names and len(names) == len(coef):
            result["method"] = "coefficient"
            result["features"] = {n: float(c) for n, c in zip(names, coef)}
            result["original_features"] = _sum_original(original_names, coef)
    elif is_tree_model(model_id) and hasattr(model, "feature_importances_"):
        imp = np.asarray(model.feature_importances_).ravel()
        if names and len(names) == len(imp):
            result["method"] = "feature_importance"
            result["features"] = {n: float(i) for n, i in zip(names, imp)}
            result["original_features"] = _sum_original(original_names, imp)

    # Permutation importance on the held-out test set (model-agnostic).
    if X_test is not None and y_test is not None and len(y_test) > 0:
        try:
            scoring = "r2" if task_type == MLTaskType.REGRESSION else "accuracy"
            perm = permutation_importance(
                pipeline,
                X_test,
                y_test,
                n_repeats=5,
                random_state=seed,
                scoring=scoring,
                n_jobs=1,
            )
            original_inputs = list(config.merged_features())
            if len(original_inputs) == len(perm.importances_mean):
                perm_orig = dict(zip(original_inputs, (float(v) for v in perm.importances_mean)))
                # Keep top 15 by mean importance.
                top = sorted(perm_orig.items(), key=lambda kv: kv[1], reverse=True)[:15]
                result["permutation"] = {k: float(v) for k, v in top}
        except Exception:  # noqa: BLE001
            result["permutation"] = {}

    return result
