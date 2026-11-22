"""Pure, versioned projection of an actual ML evaluation allocation.

Only canonical hashes of original row IDs leave this module. Holdout indices are
relative to kept rows; CV indices are relative to the final training portion.
Candidate estimators and their parameters are deliberately outside the protocol.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from hashlib import sha256
from typing import Any

import numpy as np

from app.ml.config import MLConfig
from app.ml.metrics import primary_metric


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _indices(values: np.ndarray, *, name: str, size: int | None = None) -> list[int]:
    array = np.asarray(values)
    if array.ndim != 1 or array.dtype.kind not in "iu":
        raise ValueError(f"{name} must be a one-dimensional integer array.")
    result = [int(value) for value in array]
    if any(value < 0 or (size is not None and value >= size) for value in result):
        raise ValueError(f"{name} contains an out-of-range row index.")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} contains duplicate row indices.")
    return result


def build_evaluation_protocol(
    config: MLConfig,
    *,
    dataset_sha256: str,
    row_index: np.ndarray,
    train_idx: np.ndarray,
    validation_idx: np.ndarray | None,
    test_idx: np.ndarray,
    cv_splits: Sequence[tuple[np.ndarray, np.ndarray]],
    software_versions: dict[str, str],
) -> dict[str, Any]:
    """Validate and hash the caller's allocation without generating a new split.

    Array order is retained: temporal and positional evaluation depend on it.
    An absent validation set is the canonical empty integer-list hash. The
    protocol ID hashes the entire versioned payload before adding its own ID.
    """
    kept = _indices(row_index, name="row_index")
    train = _indices(train_idx, name="train_idx", size=len(kept))
    validation = (
        []
        if validation_idx is None
        else _indices(validation_idx, name="validation_idx", size=len(kept))
    )
    test = _indices(test_idx, name="test_idx", size=len(kept))
    partitions = [set(train), set(validation), set(test)]
    if any(partitions[left] & partitions[right] for left, right in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Train, validation and test partitions must be disjoint.")
    if not isinstance(dataset_sha256, str):
        raise ValueError("dataset_sha256 must be a string.")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in software_versions.items()
    ):
        raise ValueError("Software versions must contain only string keys and values.")

    original_train = [kept[index] for index in train]
    folds: list[dict[str, Any]] = []
    for number, pair in enumerate(cv_splits, start=1):
        if len(pair) != 2:
            raise ValueError("Each CV fold must contain training and validation indices.")
        fold_train = _indices(pair[0], name=f"cv[{number}].train", size=len(train))
        fold_validation = _indices(pair[1], name=f"cv[{number}].validation", size=len(train))
        if set(fold_train) & set(fold_validation):
            raise ValueError("CV training and validation partitions must be disjoint.")
        folds.append(
            {
                "fold": number,
                "n_train": len(fold_train),
                "n_validation": len(fold_validation),
                "train_rows_sha256": _canonical_hash(
                    [original_train[index] for index in fold_train]
                ),
                "validation_rows_sha256": _canonical_hash(
                    [original_train[index] for index in fold_validation]
                ),
            }
        )

    protocol: dict[str, Any] = {
        "version": 1,
        "dataset_sha256": dataset_sha256,
        "target": config.target,
        "features": list(config.merged_features()),
        "categorical_features": list(dict.fromkeys(config.categorical_features)),
        "filter_policy": "drop_null_or_nan_target",
        "split": {
            "strategy": config.split_strategy.value,
            "random_seed": config.random_seed,
            "group_variable": config.group_variable,
            "time_variable": config.time_variable,
            "n_splits": config.n_splits,
            "test_size": config.test_size,
            "val_size": config.val_size,
            "n_train": len(train),
            "n_validation": len(validation),
            "n_test": len(test),
            "n_kept": len(kept),
            "kept_rows_sha256": _canonical_hash(kept),
            "train_rows_sha256": _canonical_hash(original_train),
            "validation_rows_sha256": _canonical_hash([kept[index] for index in validation]),
            "test_rows_sha256": _canonical_hash([kept[index] for index in test]),
            "cv_folds": folds,
        },
        "preprocessing": {
            "standardize": config.standardize,
            "impute_numeric": config.impute_numeric,
            "impute_categorical": config.impute_categorical,
            "handle_outliers": config.handle_outliers,
            "encoding": "one_hot",
            "unknown_categories": "ignore",
            "fit_scope": "training_fold_only",
        },
        "scoring": {
            "task_type": config.task_type.value,
            "primary_metric": primary_metric(config.task_type),
            "direction": "maximize",
        },
        "software_versions": dict(software_versions),
    }
    if config.feature_selection_k is not None:
        protocol["preprocessing"]["feature_selection"] = {
            "method": "select_k_best",
            "k": config.feature_selection_k,
            "scoring": "f_regression" if config.task_type.value == "regression" else "f_classif",
            "fit_scope": "training_fold_only",
        }
    protocol["protocol_id"] = _canonical_hash(protocol)
    return protocol
