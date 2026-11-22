"""Static, versioned applicability metadata, independent of execution engines."""

from dataclasses import dataclass
from typing import Any

MATRIX_VERSION = "protocol-method-matrix-v1"
DIAGNOSTIC_IDS = frozenset(
    {
        "linearity",
        "influence",
        "monotonicity",
        "group_normality",
        "equal_variance",
        "group_distribution",
        "difference_normality",
        "difference_symmetry",
        "homoscedasticity",
        "residual_normality",
    }
)


@dataclass(frozen=True)
class MethodRequirement:
    method_id: str
    design: str
    estimand: str
    numeric_roles: tuple[str, ...]
    required_diagnostics: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


_METHODS = (
    MethodRequirement("descriptive", "descriptive", "summary", ("analysis",)),
    MethodRequirement(
        "pearson",
        "association",
        "association",
        ("analysis",),
        ("linearity", "influence"),
        ("spearman", "kendall"),
        ("association_not_causation",),
    ),
    MethodRequirement(
        "spearman",
        "association",
        "association",
        ("analysis",),
        ("monotonicity",),
        ("kendall", "pearson"),
        ("association_not_causation",),
    ),
    MethodRequirement(
        "kendall",
        "association",
        "association",
        ("analysis",),
        ("monotonicity",),
        ("spearman", "pearson"),
        ("association_not_causation",),
    ),
    MethodRequirement(
        "welch_t",
        "independent_groups",
        "mean_difference",
        ("outcome",),
        ("group_normality",),
        ("student_t", "mann_whitney"),
        ("recorded_group_order",),
    ),
    MethodRequirement(
        "student_t",
        "independent_groups",
        "mean_difference",
        ("outcome",),
        ("group_normality", "equal_variance"),
        ("welch_t", "mann_whitney"),
        ("recorded_group_order",),
    ),
    MethodRequirement(
        "mann_whitney",
        "independent_groups",
        "distribution_difference",
        ("outcome",),
        ("group_distribution",),
        ("welch_t",),
        ("not_generic_mean_or_median_difference", "recorded_group_order"),
    ),
    MethodRequirement(
        "paired_t",
        "paired",
        "paired_mean_difference",
        ("outcome",),
        ("difference_normality",),
        ("wilcoxon",),
        ("ordered_paired_variables",),
    ),
    MethodRequirement(
        "wilcoxon",
        "paired",
        "paired_distribution_shift",
        ("outcome",),
        ("difference_symmetry",),
        ("paired_t",),
        ("not_paired_mean_difference", "ordered_paired_variables"),
    ),
    MethodRequirement(
        "one_way_anova",
        "one_way_groups",
        "group_difference",
        ("outcome",),
        ("group_normality", "equal_variance"),
        ("welch_anova", "kruskal_wallis"),
        ("omnibus_not_pairwise",),
    ),
    MethodRequirement(
        "welch_anova",
        "one_way_groups",
        "group_difference",
        ("outcome",),
        ("group_normality",),
        ("one_way_anova", "kruskal_wallis"),
        ("omnibus_not_pairwise",),
    ),
    MethodRequirement(
        "kruskal_wallis",
        "one_way_groups",
        "distribution_difference",
        ("outcome",),
        ("group_distribution",),
        ("welch_anova",),
        ("omnibus_not_pairwise", "no_invented_mean_ci"),
    ),
    MethodRequirement(
        "linear_regression",
        "cross_sectional",
        "conditional_association",
        ("outcome",),
        ("linearity", "homoscedasticity", "residual_normality", "influence"),
        (),
        ("conditional_association_not_causation",),
    ),
)


def requirement_for(method_id: str) -> MethodRequirement | None:
    return next((item for item in _METHODS if item.method_id == method_id), None)


def method_requirements() -> list[dict[str, Any]]:
    """Return independent JSON-compatible records; no global mutable matrix escapes."""
    return [
        {
            "version": MATRIX_VERSION,
            "method_id": item.method_id,
            "study_design": item.design,
            "estimand": item.estimand,
            "numeric_roles": list(item.numeric_roles),
            "required_diagnostics": list(item.required_diagnostics),
            "alternatives": list(item.alternatives),
            "limitations": list(item.limitations),
        }
        for item in _METHODS
    ]
