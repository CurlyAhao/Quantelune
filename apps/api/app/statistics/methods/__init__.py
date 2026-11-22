"""Statistical method implementations."""

from app.statistics.methods.anova import KRUSKAL_WALLIS, ONE_WAY_ANOVA, WELCH_ANOVA
from app.statistics.methods.categorical import (
    CHI_SQUARE_GOF,
    CHI_SQUARE_INDEPENDENCE,
    FISHER_EXACT,
)
from app.statistics.methods.correlation import KENDALL, PEARSON, SPEARMAN
from app.statistics.methods.descriptive import DESCRIPTIVE
from app.statistics.methods.evaluate_binary_predictions import EVALUATE_BINARY_PREDICTIONS
from app.statistics.methods.evaluate_predictions import EVALUATE_PREDICTIONS
from app.statistics.methods.regression import (
    ELASTIC_NET_REGRESSION,
    LASSO_REGRESSION,
    LINEAR_REGRESSION,
    LOGISTIC_REGRESSION,
    RIDGE_REGRESSION,
)
from app.statistics.methods.two_sample import (
    MANN_WHITNEY,
    ONE_SAMPLE_T,
    PAIRED_T,
    STUDENT_T,
    WELCH_T,
    WILCOXON,
)

ALL_METHODS = [
    EVALUATE_PREDICTIONS,
    EVALUATE_BINARY_PREDICTIONS,
    DESCRIPTIVE,
    PEARSON,
    SPEARMAN,
    KENDALL,
    ONE_SAMPLE_T,
    WELCH_T,
    STUDENT_T,
    PAIRED_T,
    MANN_WHITNEY,
    WILCOXON,
    CHI_SQUARE_INDEPENDENCE,
    CHI_SQUARE_GOF,
    FISHER_EXACT,
    ONE_WAY_ANOVA,
    WELCH_ANOVA,
    KRUSKAL_WALLIS,
    LINEAR_REGRESSION,
    LOGISTIC_REGRESSION,
    RIDGE_REGRESSION,
    LASSO_REGRESSION,
    ELASTIC_NET_REGRESSION,
]

__all__ = ["ALL_METHODS"]
