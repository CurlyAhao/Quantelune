"""Fixed ML resource policy; user parameters cannot enlarge execution limits."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.ml.config import MLConfig
from app.ml.types import MAX_FOLDS, MAX_MODELS_PER_RUN, MLTaskType

MIB = 1024 * 1024
MAX_ROWS = 50_000
MAX_FEATURES = 128
MAX_ENCODED_COLUMNS = 512
MAX_CLASSES = 32
MAX_FILE_BYTES = 64 * MIB
MAX_DENSE_BYTES = 128 * MIB


class MLResourceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkerLimits:
    wall_seconds: float = 300.0
    memory_bytes: int = 2 * 1024 * MIB

    def __post_init__(self) -> None:
        if not math.isfinite(self.wall_seconds) or not 0 < self.wall_seconds <= 300:
            raise ValueError("ML wall limit must be finite and at most 300 seconds.")
        if not 32 * MIB <= self.memory_bytes <= 2 * 1024 * MIB:
            raise ValueError("ML memory limit must be between 32 MiB and 2 GiB.")


# Cost-bearing parameters have explicit finite ceilings. Every other permitted
# parameter is still validated against its estimator's public get_params keys.
_INT_COST_LIMITS = {
    "n_estimators": (1, 500),
    "max_iter": (1, 5000),
    "max_depth": (1, 32),
    "max_leaf_nodes": (2, 10_000),
    "n_neighbors": (1, 100),
    "degree": (1, 5),
    "leaf_size": (1, 1000),
}


def _literal(value: Any, depth: int = 0) -> bool:
    if depth > 2:
        return False
    if value is None or isinstance(value, (str, bool, int)):
        return not isinstance(value, str) or len(value) <= 128
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return len(value) <= MAX_CLASSES and all(
            isinstance(k, (str, int)) and _literal(v, depth + 1) for k, v in value.items()
        )
    return False


def validate_config_resources(config: MLConfig) -> None:
    if config.feature_selection_k is not None and (
        isinstance(config.feature_selection_k, bool)
        or not isinstance(config.feature_selection_k, int)
        or not 1 <= config.feature_selection_k <= MAX_ENCODED_COLUMNS
    ):
        raise MLResourceError("ML_FEATURE_SELECTION_INVALID", "Feature selection k must be 1–512.")
    if len(config.merged_features()) > MAX_FEATURES:
        raise MLResourceError("ML_FEATURE_LIMIT", "ML supports at most 128 source features.")
    if len(config.model_ids) > MAX_MODELS_PER_RUN or not 2 <= config.n_splits <= MAX_FOLDS:
        raise MLResourceError("ML_CONFIG_LIMIT", "ML model or fold count exceeds the fixed limit.")
    if not isinstance(config.random_seed, int) or not 0 <= config.random_seed <= 2**32 - 1:
        raise MLResourceError("ML_SEED_INVALID", "ML seed must be an unsigned 32-bit integer.")
    for model_name, params in config.model_params.items():
        if not isinstance(params, dict) or len(params) > 32:
            raise MLResourceError(
                "ML_PARAMETER_INVALID", "ML parameters must be a bounded mapping."
            )
        for key, value in params.items():
            if not isinstance(key, str) or not _literal(value):
                raise MLResourceError(
                    "ML_PARAMETER_INVALID", "ML parameter must be a finite literal."
                )
            if key == "n_jobs" and value not in (None, 1):
                raise MLResourceError(
                    "ML_PARALLELISM_LIMIT", "ML workers use one computation thread."
                )
            if key in _INT_COST_LIMITS and value is not None:
                low, high = _INT_COST_LIMITS[key]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not low <= value <= high
                ):
                    raise MLResourceError(
                        "ML_PARAMETER_LIMIT", "ML cost parameter exceeds its bound."
                    )
            if key == "cache_size" and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 1 <= value <= 256
            ):
                raise MLResourceError("ML_PARAMETER_LIMIT", "ML cache size must be 1–256 MiB.")
        # No model class, callable, or arbitrary parameter namespace is accepted.
        from app.ml.models import build_estimator
        from app.ml.types import MLModelId

        try:
            model_id = MLModelId(model_name)
            supported = build_estimator(model_id, config.task_type, config.random_seed).get_params(
                deep=False
            )
            # SVM is a calibrated wrapper; its parameters belong to its SVC.
            if model_id.value == "svm":
                supported = supported["estimator"].get_params(deep=False)
        except (ValueError, KeyError) as exc:
            raise MLResourceError("ML_PARAMETER_INVALID", "Unknown ML parameter model.") from exc
        if set(params) - set(supported):
            raise MLResourceError("ML_PARAMETER_INVALID", "Unknown estimator parameter.")


def validate_frame_resources(df: Any, config: MLConfig) -> None:
    """Check actual parsed dimensions and conservative dense encoding cost."""
    validate_config_resources(config)
    if df.height > MAX_ROWS:
        raise MLResourceError("ML_ROW_LIMIT", "ML supports at most 50000 rows.")
    columns = len(config.numeric_features())
    for name in config.categorical_features:
        if name in df.columns:
            columns += int(df.get_column(name).n_unique()) + 1
    if columns > MAX_ENCODED_COLUMNS or df.height * columns * 8 > MAX_DENSE_BYTES:
        raise MLResourceError("ML_ENCODING_LIMIT", "ML encoding exceeds the finite matrix budget.")
    if config.task_type != MLTaskType.REGRESSION and config.target in df.columns:
        if df.get_column(config.target).drop_nulls().n_unique() > MAX_CLASSES:
            raise MLResourceError("ML_CLASS_LIMIT", "ML supports at most 32 target classes.")
