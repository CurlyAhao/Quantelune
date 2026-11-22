"""Preprocessing pipeline construction (fit only on training data).

Every transformer here is fit inside a scikit-learn Pipeline / cross-validation
loop, so it only ever sees training-fold data — no full-data standardization,
imputation or encoding can leak into evaluation.
"""

from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin, clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.feature_selection import SelectKBest, f_classif, f_regression
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from app.ml.config import MLConfig


class StrictSelectKBest(SelectKBest):
    """Use sklearn's learned scores within each fold; reject impossible k."""

    def __init__(self, k: int, task_type: str = "regression") -> None:
        self.task_type = task_type
        super().__init__(score_func=f_regression if task_type == "regression" else f_classif, k=k)

    def fit(self, X, y=None):
        if self.k > X.shape[1]:
            raise ValueError("Feature selection k exceeds the actual training-fold feature count.")
        return super().fit(X, y)


class QuantileClip(BaseEstimator, TransformerMixin):
    """Clip features to their [low, high] quantiles learned on the training fold.

    A simple, leak-safe outlier treatment (winsorization).
    """

    def __init__(self, low: float = 0.01, high: float = 0.99) -> None:
        self.low = low
        self.high = high

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=float)
        self.n_features_in_ = X.shape[1]
        self.lower_ = np.nanpercentile(X, self.low * 100, axis=0)
        self.upper_ = np.nanpercentile(X, self.high * 100, axis=0)
        return self

    def get_feature_names_out(self, input_features=None):
        if input_features is None:
            input_features = [f"x{index}" for index in range(self.n_features_in_)]
        return np.asarray(input_features, dtype=object)

    def transform(self, X):
        X = np.asarray(X, dtype=float)
        return np.clip(X, self.lower_, self.upper_)


def build_preprocessor(config: MLConfig) -> ColumnTransformer:
    """Build a ColumnTransformer for the numeric + categorical feature sets."""
    numeric = config.numeric_features()
    categorical = list(config.categorical_features)

    numeric_steps: list[tuple[str, object]] = [
        ("impute", SimpleImputer(strategy=config.impute_numeric))
    ]
    if config.handle_outliers:
        numeric_steps.append(("clip", QuantileClip()))
    if config.standardize:
        numeric_steps.append(("scale", StandardScaler()))

    categorical_steps: list[tuple[str, object]] = [
        ("impute", SimpleImputer(strategy=config.impute_categorical)),
        ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ]

    transformers: list[tuple[str, object, list[str]]] = []
    if numeric:
        transformers.append(("num", Pipeline(numeric_steps), numeric))
    if categorical:
        transformers.append(("cat", Pipeline(categorical_steps), categorical))

    return ColumnTransformer(transformers=transformers, remainder="drop")


def build_pipeline(config: MLConfig, estimator: object) -> Pipeline:
    """Full preprocessing + estimator pipeline."""
    steps: list[tuple[str, object]] = [("preprocess", build_preprocessor(config))]
    if config.feature_selection_k is not None:
        steps.append(
            ("select", StrictSelectKBest(config.feature_selection_k, config.task_type.value))
        )
    if isinstance(estimator, CalibratedClassifierCV):
        inner = Pipeline(steps=[*steps, ("model", estimator.estimator)])
        calibrated = clone(estimator).set_params(estimator=inner)
        return Pipeline(steps=[("model", calibrated)])
    return Pipeline(steps=[*steps, ("model", estimator)])


def ordinal_encode_column(values: np.ndarray) -> np.ndarray:
    """Utility used for ordered-categorical targets if needed."""
    enc = OrdinalEncoder()
    return enc.fit_transform(np.asarray(values).reshape(-1, 1)).ravel()
