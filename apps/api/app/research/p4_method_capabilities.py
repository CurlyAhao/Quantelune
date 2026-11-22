"""Current engine capabilities for NEW P4 work, never historical replay validation."""

from typing import Any

from app.research.plan_v2_contracts import PlanDocumentV2
from app.research.plan_validation import failure
from app.research.protocol_contracts import (
    ProtocolAssessment,
    ProtocolIssue,
    ResearchProtocolDocument,
)
from app.statistics.missing_preflight import NEW_REJECT_MISSING_METHODS

METHODS = frozenset(
    {
        "descriptive",
        "pearson",
        "spearman",
        "kendall",
        "welch_t",
        "student_t",
        "mann_whitney",
        "paired_t",
        "wilcoxon",
        "one_way_anova",
        "welch_anova",
        "kruskal_wallis",
        "linear_regression",
        "evaluate_predictions",
        "evaluate_binary_predictions",
    }
)
DIRECTIONAL = frozenset({"welch_t", "student_t", "mann_whitney", "paired_t", "wilcoxon"})
ERROR_ON_MISSING = (
    frozenset(
        {
            "pearson",
            "spearman",
            "kendall",
            "welch_t",
            "paired_t",
            "wilcoxon",
            "evaluate_predictions",
            "evaluate_binary_predictions",
        }
    )
    | NEW_REJECT_MISSING_METHODS
)


def issues(configuration: dict[str, Any]) -> list[ProtocolIssue]:
    method = configuration["method_id"]
    found: list[ProtocolIssue] = []

    def add(code: str, field: str):
        found.append(ProtocolIssue(code=code, field=field, severity="blocked"))

    if method not in METHODS:
        add("method_not_supported", "method_id")
        return found
    if method in {"evaluate_predictions", "evaluate_binary_predictions"}:
        from app.ml.resources import MLResourceError
        from app.research.domain_compute import normalize

        try:
            normalize("research_statistics", configuration)
        except (MLResourceError, ValueError, TypeError):
            add("evaluation_configuration_invalid", "evaluation")
        return found
    if configuration.get("alternative", "two_sided") != "two_sided" and method not in DIRECTIONAL:
        add("method_alternative_not_supported", "reporting.alternative")
    policy = configuration.get("missing_value_policy", "listwise_delete")
    if (
        policy == "error_on_missing"
        and method not in ERROR_ON_MISSING
        or policy == "pairwise_delete"
        and method == "linear_regression"
        or method == "descriptive"
        and policy == "listwise_delete"
        and len(configuration.get("variables") or []) > 1
    ):
        add("method_missing_policy_not_supported", "missing_value_policy")
    return found


def require_new_plan(plan: PlanDocumentV2) -> None:
    """Only normalized plans at a live, confirmed-Protocol boundary call this."""
    for step in plan.steps:
        if step.capability == "statistics":
            found = issues(step.configuration)
            if found:
                raise failure(found[0].code.upper())


def live_assessment(
    base: ProtocolAssessment, document: ResearchProtocolDocument, method_id: str
) -> ProtocolAssessment:
    if method_id in {"evaluate_predictions", "evaluate_binary_predictions"}:
        # This endpoint assesses Protocol metadata only. Exact versioned
        # evaluation configuration is mandatory at the live Plan boundary.
        return base
    configuration = {
        "method_id": method_id,
        "alternative": document.reporting.alternative if document.reporting else "two_sided",
        "missing_value_policy": (
            document.missing_value_policy
            if document.missing_value_policy != "unknown"
            else "listwise_delete"
        ),
        "variables": [v.reference.name for v in document.variables if v.role == "analysis"],
    }
    extra = issues(configuration)
    return base.model_copy(
        update={
            "status": "blocked" if extra else base.status,
            "issues": sorted([*base.issues, *extra], key=lambda item: (item.field, item.code)),
        }
    )
