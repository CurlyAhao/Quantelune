"""Model factory: map a model id to a scikit-learn estimator."""

from __future__ import annotations

from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import (
    ElasticNet,
    Lasso,
    LinearRegression,
    LogisticRegression,
    Ridge,
)
from sklearn.neighbors import KNeighborsClassifier
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

from app.ml.types import MLModelId, MLTaskType


def build_estimator(
    model_id: MLModelId, task_type: MLTaskType, seed: int, params: dict | None = None
):
    """Instantiate a scikit-learn estimator for the given model id and task.

    Hyperparameters come from ``params`` (validated upstream); a fixed random
    seed keeps runs reproducible.
    """
    params = dict(params or {})
    params.setdefault("random_state", seed)
    if model_id == MLModelId.LOGISTIC_REGRESSION:
        params.setdefault("max_iter", 1000)
        params["n_jobs"] = 1
        return LogisticRegression(**params)
    if model_id == MLModelId.DECISION_TREE:
        return DecisionTreeClassifier(**params)
    if model_id == MLModelId.RANDOM_FOREST:
        params["n_jobs"] = 1
        return RandomForestClassifier(**params)
    if model_id == MLModelId.GRADIENT_BOOSTING:
        return GradientBoostingClassifier(**params)
    if model_id == MLModelId.KNN:
        params.pop("random_state", None)
        params["n_jobs"] = 1
        return KNeighborsClassifier(**params)
    if model_id == MLModelId.SVM:
        from sklearn.calibration import CalibratedClassifierCV

        # CalibratedClassifierCV provides predict_proba without the deprecated
        # SVC(probability=True).
        return CalibratedClassifierCV(SVC(**params), cv=3, ensemble=False, n_jobs=1)
    if model_id == MLModelId.LINEAR_REGRESSION:
        params.pop("random_state", None)
        params["n_jobs"] = 1
        return LinearRegression(**params)
    if model_id == MLModelId.RIDGE:
        return Ridge(**params)
    if model_id == MLModelId.LASSO:
        params.setdefault("max_iter", 5000)
        return Lasso(**params)
    if model_id == MLModelId.ELASTIC_NET:
        params.setdefault("max_iter", 5000)
        return ElasticNet(**params)
    if model_id == MLModelId.DECISION_TREE_REGRESSOR:
        return DecisionTreeRegressor(**params)
    if model_id == MLModelId.RANDOM_FOREST_REGRESSOR:
        params["n_jobs"] = 1
        return RandomForestRegressor(**params)
    if model_id == MLModelId.GRADIENT_BOOSTING_REGRESSOR:
        return GradientBoostingRegressor(**params)
    raise ValueError(f"Unknown model id: {model_id}")


def is_linear_model(model_id: MLModelId) -> bool:
    return model_id in {
        MLModelId.LOGISTIC_REGRESSION,
        MLModelId.LINEAR_REGRESSION,
        MLModelId.RIDGE,
        MLModelId.LASSO,
        MLModelId.ELASTIC_NET,
    }


def is_tree_model(model_id: MLModelId) -> bool:
    return model_id in {
        MLModelId.DECISION_TREE,
        MLModelId.RANDOM_FOREST,
        MLModelId.GRADIENT_BOOSTING,
        MLModelId.DECISION_TREE_REGRESSOR,
        MLModelId.RANDOM_FOREST_REGRESSOR,
        MLModelId.GRADIENT_BOOSTING_REGRESSOR,
    }


def models_for_task(task_type: MLTaskType) -> list[MLModelId]:
    from app.ml.types import CLASSIFICATION_MODELS, REGRESSION_MODELS

    if task_type == MLTaskType.REGRESSION:
        return list(REGRESSION_MODELS)
    return list(CLASSIFICATION_MODELS)
