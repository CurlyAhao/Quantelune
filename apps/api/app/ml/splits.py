"""Data-splitting helpers: held-out test split + cross-validation splitters.

Splits respect the task structure — stratification for classification, temporal
ordering for time-series data (no random split that would leak the future).
"""

from __future__ import annotations

import numpy as np
from sklearn.model_selection import (
    GroupKFold,
    KFold,
    StratifiedKFold,
    TimeSeriesSplit,
    train_test_split,
)

from app.ml.config import MLConfig
from app.ml.types import MLTaskType, SplitStrategy


def is_temporal(config: MLConfig) -> bool:
    """A run is temporal only when the split strategy is time-series.

    A time column with a *random* split is treated as a leakage risk (flagged by
    the leakage checks) rather than silently coerced into a temporal split.
    """
    return config.split_strategy == SplitStrategy.TIME_SERIES_SPLIT


def held_out_test_split(
    n: int,
    config: MLConfig,
    y: np.ndarray,
    groups: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (train_idx, test_idx) for the final held-out test set.

    Group K-Fold isolates entities: entire groups are assigned to train or test.
    Row-wise random holdout would leak entities into final metrics.
    """
    indices = np.arange(n)
    if is_temporal(config):
        # Test set is the last `test_size` fraction (no shuffling).
        cut = int(round(n * (1 - config.test_size)))
        cut = max(1, min(cut, n - 1))
        return indices[:cut], indices[cut:]

    if config.split_strategy == SplitStrategy.GROUP_KFOLD:
        if groups is None:
            raise ValueError(
                "Group K-Fold held-out split requires group labels; "
                "refusing row-wise holdout that would cross entities."
            )
        unique = np.unique(groups)
        if unique.size < 2:
            raise ValueError(
                "Group K-Fold held-out split needs at least 2 distinct groups; "
                "refusing row-wise holdout that would cross entities."
            )
        rng = np.random.RandomState(config.random_seed)
        shuffled = unique.copy()
        rng.shuffle(shuffled)
        n_test_groups = int(round(unique.size * config.test_size))
        n_test_groups = max(1, min(n_test_groups, unique.size - 1))
        test_group_set = set(shuffled[:n_test_groups].tolist())
        train_mask = np.array([g not in test_group_set for g in groups], dtype=bool)
        test_mask = ~train_mask
        train_idx = indices[train_mask]
        test_idx = indices[test_mask]
        if train_idx.size == 0 or test_idx.size == 0:
            raise ValueError("Group K-Fold held-out split produced an empty train or test set.")
        return np.sort(train_idx), np.sort(test_idx)

    stratify = y if config.task_type != MLTaskType.REGRESSION else None
    try:
        train_idx, test_idx = train_test_split(
            indices,
            test_size=config.test_size,
            random_state=config.random_seed,
            stratify=stratify,
        )
    except ValueError:
        # Stratification can fail with rare classes; fall back to a plain split.
        train_idx, test_idx = train_test_split(
            indices, test_size=config.test_size, random_state=config.random_seed
        )
    return np.sort(train_idx), np.sort(test_idx)


def validation_split(
    train_idx: np.ndarray, config: MLConfig, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Split a validation set out of the training portion (train_val_test only)."""
    relative = config.val_size / (config.val_size + (1 - config.test_size - config.val_size))
    stratify = y[train_idx] if config.task_type != MLTaskType.REGRESSION else None
    try:
        tr, va = train_test_split(
            train_idx,
            test_size=relative,
            random_state=config.random_seed,
            stratify=stratify,
        )
    except ValueError:
        tr, va = train_test_split(train_idx, test_size=relative, random_state=config.random_seed)
    return np.sort(tr), np.sort(va)


def make_cv_splitter(config: MLConfig, y_train: np.ndarray, groups_train: np.ndarray | None):
    """Build a cross-validation splitter for the training portion."""
    n_splits = max(2, min(config.n_splits, 10))
    strategy = config.split_strategy

    if strategy == SplitStrategy.KFOLD:
        return KFold(n_splits=n_splits, shuffle=True, random_state=config.random_seed)
    if strategy == SplitStrategy.STRATIFIED_KFOLD and config.task_type != MLTaskType.REGRESSION:
        unique, counts = np.unique(y_train, return_counts=True)
        if len(unique) < 2:
            raise ValueError("Stratified K-Fold requires at least two classes in y_train.")
        min_class = int(counts.min())
        if min_class < n_splits:
            raise ValueError(
                f"Stratified K-Fold n_splits={n_splits} exceeds smallest class count "
                f"{min_class}; refusing silent fold reduction."
            )
        return StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=config.random_seed)
    if strategy == SplitStrategy.GROUP_KFOLD:
        if groups_train is None:
            raise ValueError(
                "Group K-Fold requires group labels; refusing fallback to plain K-Fold."
            )
        n_groups = len(np.unique(groups_train))
        if n_groups < n_splits:
            raise ValueError(
                f"Group K-Fold n_splits={n_splits} exceeds distinct groups {n_groups}; "
                "refusing silent fold reduction."
            )
        return GroupKFold(n_splits=n_splits)
    if strategy == SplitStrategy.TIME_SERIES_SPLIT or is_temporal(config):
        return TimeSeriesSplit(n_splits=n_splits)
    # Default for regression / plain kfold.
    return KFold(n_splits=n_splits, shuffle=True, random_state=config.random_seed)


def uses_cross_validation(config: MLConfig) -> bool:
    return config.split_strategy in {
        SplitStrategy.KFOLD,
        SplitStrategy.STRATIFIED_KFOLD,
        SplitStrategy.GROUP_KFOLD,
        SplitStrategy.TIME_SERIES_SPLIT,
    }
