"""Enumerations for the statistical analysis engine."""

from __future__ import annotations

import enum


class MethodId(str, enum.Enum):
    """Canonical identifier for every supported statistical method."""

    DESCRIPTIVE = "descriptive"
    PEARSON = "pearson"
    SPEARMAN = "spearman"
    KENDALL = "kendall"
    ONE_SAMPLE_T = "one_sample_t"
    WELCH_T = "welch_t"
    STUDENT_T = "student_t"
    PAIRED_T = "paired_t"
    MANN_WHITNEY = "mann_whitney"
    WILCOXON = "wilcoxon"
    CHI_SQUARE_INDEPENDENCE = "chi_square_independence"
    CHI_SQUARE_GOF = "chi_square_gof"
    FISHER_EXACT = "fisher_exact"
    ONE_WAY_ANOVA = "one_way_anova"
    WELCH_ANOVA = "welch_anova"
    KRUSKAL_WALLIS = "kruskal_wallis"
    LINEAR_REGRESSION = "linear_regression"
    LOGISTIC_REGRESSION = "logistic_regression"
    RIDGE_REGRESSION = "ridge_regression"
    LASSO_REGRESSION = "lasso_regression"
    ELASTIC_NET_REGRESSION = "elastic_net_regression"
    EVALUATE_PREDICTIONS = "evaluate_predictions"
    EVALUATE_BINARY_PREDICTIONS = "evaluate_binary_predictions"


class MissingPolicy(str, enum.Enum):
    """How missing values are handled before a method runs.

    Nothing is ever silently dropped — the result always reports the original
    size, the number excluded, the final size and the reason.
    """

    LISTWISE_DELETE = "listwise_delete"
    PAIRWISE_DELETE = "pairwise_delete"
    ERROR_ON_MISSING = "error_on_missing"


class Alternative(str, enum.Enum):
    """Hypothesis direction for a test."""

    TWO_SIDED = "two_sided"
    LESS = "less"
    GREATER = "greater"


class MethodStatus(str, enum.Enum):
    """Outcome of a single method execution."""

    COMPLETED = "completed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


# Human-readable method metadata used by the /statistics/methods endpoint and
# the frontend method picker.
METHOD_CATEGORIES: dict[MethodId, str] = {
    MethodId.EVALUATE_PREDICTIONS: "prediction_evaluation",
    MethodId.EVALUATE_BINARY_PREDICTIONS: "prediction_evaluation",
    MethodId.DESCRIPTIVE: "descriptive",
    MethodId.PEARSON: "correlation",
    MethodId.SPEARMAN: "correlation",
    MethodId.KENDALL: "correlation",
    MethodId.ONE_SAMPLE_T: "two_group",
    MethodId.WELCH_T: "two_group",
    MethodId.STUDENT_T: "two_group",
    MethodId.PAIRED_T: "two_group",
    MethodId.MANN_WHITNEY: "two_group",
    MethodId.WILCOXON: "two_group",
    MethodId.CHI_SQUARE_INDEPENDENCE: "categorical",
    MethodId.CHI_SQUARE_GOF: "categorical",
    MethodId.FISHER_EXACT: "categorical",
    MethodId.ONE_WAY_ANOVA: "multi_group",
    MethodId.WELCH_ANOVA: "multi_group",
    MethodId.KRUSKAL_WALLIS: "multi_group",
    MethodId.LINEAR_REGRESSION: "regression",
    MethodId.LOGISTIC_REGRESSION: "regression",
    MethodId.RIDGE_REGRESSION: "regression",
    MethodId.LASSO_REGRESSION: "regression",
    MethodId.ELASTIC_NET_REGRESSION: "regression",
}

# Minimum number of valid (non-missing) observations for a method to run.
MIN_SAMPLE_SIZE: dict[MethodId, int] = {
    MethodId.EVALUATE_PREDICTIONS: 1,
    MethodId.EVALUATE_BINARY_PREDICTIONS: 1,
    MethodId.DESCRIPTIVE: 1,
    MethodId.PEARSON: 3,
    MethodId.SPEARMAN: 3,
    MethodId.KENDALL: 3,
    MethodId.ONE_SAMPLE_T: 2,
    MethodId.WELCH_T: 2,
    MethodId.STUDENT_T: 2,
    MethodId.PAIRED_T: 2,
    MethodId.MANN_WHITNEY: 2,
    MethodId.WILCOXON: 2,
    MethodId.CHI_SQUARE_INDEPENDENCE: 1,
    MethodId.CHI_SQUARE_GOF: 1,
    MethodId.FISHER_EXACT: 1,
    MethodId.ONE_WAY_ANOVA: 2,
    MethodId.WELCH_ANOVA: 2,
    MethodId.KRUSKAL_WALLIS: 2,
    MethodId.LINEAR_REGRESSION: 5,
    MethodId.LOGISTIC_REGRESSION: 10,
    MethodId.RIDGE_REGRESSION: 5,
    MethodId.LASSO_REGRESSION: 5,
    MethodId.ELASTIC_NET_REGRESSION: 5,
}
