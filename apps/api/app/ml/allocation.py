"""One actual allocation shared by training and workflow partition artifacts."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import polars as pl

from app.ml.config import MLConfig
from app.ml.splits import (
    held_out_test_split,
    make_cv_splitter,
    uses_cross_validation,
    validation_split,
)
from app.ml.types import MLTaskType, SplitStrategy


@dataclass(frozen=True)
class EvaluationAllocation:
    train: np.ndarray
    validation: np.ndarray | None
    test: np.ndarray
    groups: np.ndarray | None
    cv_folds: list[tuple[np.ndarray, np.ndarray]]

    def original_rows(self, kept: np.ndarray) -> dict:
        original_train = kept[self.train]
        return {
            "kept": kept.tolist(),
            "train": original_train.tolist(),
            "validation": [] if self.validation is None else kept[self.validation].tolist(),
            "test": kept[self.test].tolist(),
            "cv_folds": [
                {
                    "train": original_train[train].tolist(),
                    "validation": original_train[validation].tolist(),
                }
                for train, validation in self.cv_folds
            ],
        }


def allocate_evaluation(
    df: pl.DataFrame, config: MLConfig, X: pd.DataFrame, y: np.ndarray, kept: np.ndarray
) -> EvaluationAllocation:
    groups = (
        df.get_column(config.group_variable).to_numpy()[kept]
        if config.group_variable and config.group_variable in df.columns
        else None
    )
    split_target = y if config.task_type != MLTaskType.REGRESSION else np.zeros(len(y))
    train, test = held_out_test_split(len(y), config, split_target, groups=groups)
    validation = None
    if config.split_strategy == SplitStrategy.TRAIN_VAL_TEST:
        train, validation = validation_split(train, config, split_target)
    folds = []
    if uses_cross_validation(config):
        train_groups = None if groups is None else groups[train]
        splitter = make_cv_splitter(config, y[train], train_groups)
        folds = list(
            splitter.split(X.iloc[train], y[train], train_groups)
            if config.split_strategy == SplitStrategy.GROUP_KFOLD
            else splitter.split(X.iloc[train], y[train])
        )
    return EvaluationAllocation(train, validation, test, groups, folds)
