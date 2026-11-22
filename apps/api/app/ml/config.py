"""Configuration dataclass for an ML experiment run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ml.types import DEFAULT_N_SPLITS, MLModelId, MLTaskType, SplitStrategy


@dataclass
class MLConfig:
    """Everything needed to run a reproducible ML experiment."""

    task_type: MLTaskType
    target: str
    features: tuple[str, ...] = ()
    categorical_features: tuple[str, ...] = ()
    group_variable: str | None = None
    time_variable: str | None = None
    split_strategy: SplitStrategy = SplitStrategy.STRATIFIED_KFOLD
    test_size: float = 0.2
    val_size: float = 0.15
    n_splits: int = DEFAULT_N_SPLITS
    random_seed: int = 42
    model_ids: tuple[MLModelId, ...] = ()
    standardize: bool = True
    impute_numeric: str = "median"  # median | mean
    impute_categorical: str = "most_frequent"
    handle_outliers: bool = False
    feature_selection_k: int | None = None
    model_params: dict[str, dict[str, Any]] = field(default_factory=dict)

    def numeric_features(self) -> list[str]:
        return [f for f in self.features if f not in self.categorical_features]

    def merged_features(self) -> tuple[str, ...]:
        """Stable deduped union: features plus legacy split-only categoricals."""
        return tuple(dict.fromkeys([*self.features, *self.categorical_features]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type.value,
            "target": self.target,
            "features": list(self.features),
            "categorical_features": list(self.categorical_features),
            "group_variable": self.group_variable,
            "time_variable": self.time_variable,
            "split_strategy": self.split_strategy.value,
            "test_size": self.test_size,
            "val_size": self.val_size,
            "n_splits": self.n_splits,
            "random_seed": self.random_seed,
            "model_ids": [m.value for m in self.model_ids],
            "standardize": self.standardize,
            "impute_numeric": self.impute_numeric,
            "impute_categorical": self.impute_categorical,
            "handle_outliers": self.handle_outliers,
            "feature_selection_k": self.feature_selection_k,
            "model_params": self.model_params,
        }
