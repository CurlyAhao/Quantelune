"""Enumerations and metadata for the machine-learning experiment engine."""

from __future__ import annotations

import enum


class MLTaskType(str, enum.Enum):
    BINARY_CLASSIFICATION = "binary_classification"
    MULTICLASS_CLASSIFICATION = "multiclass_classification"
    REGRESSION = "regression"


class SplitStrategy(str, enum.Enum):
    TRAIN_TEST = "train_test"
    TRAIN_VAL_TEST = "train_val_test"
    KFOLD = "kfold"
    STRATIFIED_KFOLD = "stratified_kfold"
    GROUP_KFOLD = "group_kfold"
    TIME_SERIES_SPLIT = "time_series_split"


class MLModelId(str, enum.Enum):
    # Classification
    LOGISTIC_REGRESSION = "logistic_regression"
    DECISION_TREE = "decision_tree"
    RANDOM_FOREST = "random_forest"
    GRADIENT_BOOSTING = "gradient_boosting"
    KNN = "knn"
    SVM = "svm"
    # Regression
    LINEAR_REGRESSION = "linear_regression"
    RIDGE = "ridge"
    LASSO = "lasso"
    ELASTIC_NET = "elastic_net"
    DECISION_TREE_REGRESSOR = "decision_tree_regressor"
    RANDOM_FOREST_REGRESSOR = "random_forest_regressor"
    GRADIENT_BOOSTING_REGRESSOR = "gradient_boosting_regressor"


CLASSIFICATION_MODELS = {
    MLModelId.LOGISTIC_REGRESSION,
    MLModelId.DECISION_TREE,
    MLModelId.RANDOM_FOREST,
    MLModelId.GRADIENT_BOOSTING,
    MLModelId.KNN,
    MLModelId.SVM,
}

REGRESSION_MODELS = {
    MLModelId.LINEAR_REGRESSION,
    MLModelId.RIDGE,
    MLModelId.LASSO,
    MLModelId.ELASTIC_NET,
    MLModelId.DECISION_TREE_REGRESSOR,
    MLModelId.RANDOM_FOREST_REGRESSOR,
    MLModelId.GRADIENT_BOOSTING_REGRESSOR,
}

MODEL_METADATA: dict[MLModelId, dict] = {
    MLModelId.LOGISTIC_REGRESSION: {
        "name": "Logistic Regression",
        "tasks": ["binary_classification", "multiclass_classification"],
        "interpretable": True,
    },
    MLModelId.DECISION_TREE: {
        "name": "Decision Tree",
        "tasks": ["binary_classification", "multiclass_classification"],
        "interpretable": True,
    },
    MLModelId.RANDOM_FOREST: {
        "name": "Random Forest",
        "tasks": ["binary_classification", "multiclass_classification"],
        "interpretable": False,
    },
    MLModelId.GRADIENT_BOOSTING: {
        "name": "Gradient Boosting",
        "tasks": ["binary_classification", "multiclass_classification"],
        "interpretable": False,
    },
    MLModelId.KNN: {
        "name": "K-Nearest Neighbors",
        "tasks": ["binary_classification", "multiclass_classification"],
        "interpretable": False,
    },
    MLModelId.SVM: {
        "name": "Support Vector Machine",
        "tasks": ["binary_classification"],
        "interpretable": False,
    },
    MLModelId.LINEAR_REGRESSION: {
        "name": "Linear Regression",
        "tasks": ["regression"],
        "interpretable": True,
    },
    MLModelId.RIDGE: {"name": "Ridge", "tasks": ["regression"], "interpretable": True},
    MLModelId.LASSO: {"name": "Lasso", "tasks": ["regression"], "interpretable": True},
    MLModelId.ELASTIC_NET: {"name": "Elastic Net", "tasks": ["regression"], "interpretable": True},
    MLModelId.DECISION_TREE_REGRESSOR: {
        "name": "Decision Tree Regressor",
        "tasks": ["regression"],
        "interpretable": True,
    },
    MLModelId.RANDOM_FOREST_REGRESSOR: {
        "name": "Random Forest Regressor",
        "tasks": ["regression"],
        "interpretable": False,
    },
    MLModelId.GRADIENT_BOOSTING_REGRESSOR: {
        "name": "Gradient Boosting Regressor",
        "tasks": ["regression"],
        "interpretable": False,
    },
}

# Cross-validation fold bounds.
MIN_FOLDS = 2
MAX_FOLDS = 10
DEFAULT_N_SPLITS = 5
# Maximum models allowed in a single comparison run.
MAX_MODELS_PER_RUN = 6
