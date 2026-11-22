"""Bounded aggregate projection of a proved result; never computes statistics."""

import math
from typing import Any

from app.research.contracts import canonical_bytes, logical_hash
from app.research.p4_method_capabilities import DIRECTIONAL, METHODS, issues
from app.research.plan_v2_contracts import PlanDocumentV2
from app.research.plan_validation import failure
from app.research.protocol_contracts import ResearchProtocolDocument
from app.statistics.missing_preflight import (
    NEW_REJECT_MISSING_METHODS,
    RESULT_METADATA_VERSION,
    WELCH_ANOVA_VERSION,
    recorded_reject_missing_supported,
)

MAX_FACTS = 4096
MAX_BYTES = 256 * 1024
OMNIBUS = {"one_way_anova", "welch_anova", "kruskal_wallis"}
CORRELATIONS = {"pearson", "spearman", "kendall"}
EFFECTS = {
    "descriptive": (),
    "pearson": ("name", "value"),
    "spearman": ("name", "value"),
    "kendall": ("name", "value"),
    "welch_t": ("cohens_d", "hedges_g"),
    "student_t": ("cohens_d", "hedges_g"),
    "mann_whitney": ("rank_biserial",),
    "paired_t": ("cohens_d_paired",),
    "wilcoxon": ("rank_biserial",),
    "one_way_anova": ("eta_squared", "omega_squared"),
    "welch_anova": (),
    "kruskal_wallis": ("epsilon_squared",),
    "linear_regression": ("r_squared", "adj_r_squared"),
}


def project(
    plan: PlanDocumentV2,
    step_id: str,
    protocol: ResearchProtocolDocument,
    result: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    step = next((item for item in plan.steps if item.id == step_id), None)
    if step is None or step.capability != "statistics":
        raise failure("RESULT_FACTS_UNAVAILABLE")
    config = step.configuration
    method = config["method_id"]
    if (
        method not in METHODS
        or result.get("method") != method
        or result.get("status") != "completed"
        or not result.get("method_version")
    ):
        raise failure("RESULT_FACTS_UNPROVEN")
    if method == "evaluate_binary_predictions":
        from app.research.p4_binary_evaluation_facts import project as project_binary_evaluation

        return project_binary_evaluation(plan, step, protocol, result, binding)
    if method == "evaluate_predictions":
        from app.research.p4_evaluation_facts import project as evaluation_facts

        return evaluation_facts(plan, step, protocol, result, binding)
    if method == "welch_anova" and result["method_version"] not in {
        WELCH_ANOVA_VERSION,
        RESULT_METADATA_VERSION,
    }:
        # Historical artifacts remain readable, but known incorrect/unknown
        # Welch denominator df must not become new P4 inferential facts.
        raise failure("RESULT_METHOD_VERSION_UNVERIFIED")
    if method == "descriptive" and result["method_version"] == "1.0.0":
        summaries = result.get("descriptive_statistics", {}).get("variables", {})
        if isinstance(summaries, dict) and any(
            isinstance(value, dict)
            and value.get("count") == 1
            and (value.get("std") == 0 or value.get("variance") == 0)
            for value in summaries.values()
        ):
            # The original record remains readable. Known incorrect single-row
            # sample dispersion cannot become new model explanation facts.
            raise failure("RESULT_METHOD_VERSION_UNVERIFIED")
    if (
        method == "kendall"
        and config["missing_value_policy"] == "pairwise_delete"
        and result["method_version"] != "1.0.1"
    ):
        raise failure("RESULT_METHOD_VERSION_UNVERIFIED")
    facts: list[dict[str, Any]] = []
    prefix: list[str | int] = ["results", 0, "result"]

    def add(path: list[str | int], value: Any):
        if not (value is None or type(value) in {str, int, float, bool}):
            raise failure("RESULT_FACTS_UNPROVEN")
        if isinstance(value, float) and not math.isfinite(value):
            raise failure("RESULT_FACTS_UNPROVEN")
        if isinstance(value, str) and len(value) > 1024:
            raise failure("RESULT_FACTS_LIMIT")
        facts.append({"path": [*prefix, *path], "value": value})
        if len(facts) > MAX_FACTS:
            raise failure("RESULT_FACTS_LIMIT")

    def fields(value: Any, path: list[str | int], keys):
        if type(value) is not dict:
            raise failure("RESULT_FACTS_UNPROVEN")
        for key in keys:
            if key in value:
                add([*path, key], value[key])

    def mapping(value: Any):
        if type(value) is not dict or len(value) > 100:
            raise failure("RESULT_FACTS_LIMIT")
        if any(type(key) is not str or not 1 <= len(key) <= 255 for key in value):
            raise failure("RESULT_FACTS_UNPROVEN")
        return value.items()

    for key in ("method", "method_version", "status", "alternative", "confidence_level", "p_value"):
        if key in result:
            add([key], result[key])
    sample = result.get("sample")
    if sample is not None:
        fields(sample, ["sample"], ("original_n", "excluded_n", "final_n", "reason"))
        for name, value in mapping(sample.get("per_variable", {})):
            add(["sample", "per_variable", name], value)
    fields(
        result.get("statistic", {}),
        ["statistic"],
        (
            "name",
            "value",
            "n",
            "df",
            "df_between",
            "df_within",
            "df_model",
            "df_resid",
            "group_1",
            "group_2",
            "U_for_group",
            "p_value_selection",
            "p_value_method",
            "continuity_correction",
            "ties_present",
            "tie_correction",
        ),
    )
    fields(result.get("effect_size", {}), ["effect_size"], EFFECTS[method])
    estimates = result.get("estimates", {})
    if method in CORRELATIONS:
        fields(estimates, ["estimates"], ("correlation",))
    elif method in {"welch_t", "student_t", "paired_t", "wilcoxon"}:
        fields(estimates, ["estimates"], ("mean_difference", "median_difference"))
    if method in OMNIBUS:
        for name, value in mapping(estimates):
            add(["estimates", name], value)
    if method == "mann_whitney":
        for name, value in mapping(estimates):
            if name.startswith("median_"):
                add(["estimates", name], value)

    ci = result.get("confidence_interval", {})
    if method in {"welch_t", "student_t"}:
        fields(
            ci,
            ["confidence_interval"],
            (
                "schema_version",
                "estimand",
                "direction",
                "confidence_level",
                "alternative",
                "df",
                "standard_error",
                "lower",
                "upper",
                "lower_unbounded",
                "upper_unbounded",
                "method",
            ),
        )
        order = ci.get("group_order", [])
        if type(order) is not list or len(order) not in {0, 2}:
            raise failure("RESULT_FACTS_UNPROVEN")
        for index, label in enumerate(order):
            add(["confidence_interval", "group_order", index], label)
    elif method == "pearson":
        if "correlation" in ci:
            fields(
                ci["correlation"],
                ["confidence_interval", "correlation"],
                ("lower", "upper", "confidence_level"),
            )
    elif method == "descriptive":
        variables = result.get("descriptive_statistics", {}).get("variables", {})
        for name, summary in mapping(variables):
            if name not in config["variables"]:
                raise failure("RESULT_FACTS_UNPROVEN")
            path = ["descriptive_statistics", "variables", name]
            fields(
                summary,
                path,
                (
                    "name",
                    "count",
                    "mean",
                    "median",
                    "variance",
                    "std",
                    "min",
                    "max",
                    "q1",
                    "q3",
                    "iqr",
                    "skewness",
                    "kurtosis",
                ),
            )
            if summary.get("mean_ci") is not None:
                fields(
                    summary["mean_ci"], [*path, "mean_ci"], ("lower", "upper", "confidence_level")
                )
    elif method == "linear_regression":
        coefficients = estimates.get("coefficients", [])
        if type(coefficients) is not list or len(coefficients) > 100:
            raise failure("RESULT_FACTS_LIMIT")
        for index, coefficient in enumerate(coefficients):
            fields(
                coefficient,
                ["estimates", "coefficients", index],
                ("term", "coefficient", "std_error", "t", "p_value", "ci_lower", "ci_upper"),
            )
        for name, reference in mapping(estimates.get("reference_categories", {})):
            add(["estimates", "reference_categories", name], reference)
        for name, encoding in mapping(estimates.get("encoding", {})):
            fields(encoding, ["estimates", "encoding", name], ("reference",))
            for key in ("encoded_levels", "all_levels"):
                levels = encoding.get(key, [])
                if type(levels) is not list or len(levels) > 100:
                    raise failure("RESULT_FACTS_LIMIT")
                for index, level in enumerate(levels):
                    add(["estimates", "encoding", name, key, index], level)
    if method in {
        "welch_t",
        "student_t",
        "mann_whitney",
        "one_way_anova",
        "welch_anova",
        "kruskal_wallis",
    }:
        for name, summary in mapping(result.get("descriptive_statistics", {})):
            fields(summary, ["descriptive_statistics", name], ("n", "mean", "std"))

    checks = result.get("assumption_checks", [])
    if type(checks) is not list or len(checks) > 200:
        raise failure("RESULT_FACTS_LIMIT")
    for index, check in enumerate(checks):
        # A recorded boolean is not an independently verified assumption pass.
        fields(check, ["assumption_checks", index], ("name", "passed"))
        for key in ("value", "threshold"):
            value = check.get(key)
            if type(value) is dict:
                fields(
                    value,
                    ["assumption_checks", index, key],
                    (
                        "statistic",
                        "p_value",
                        "count",
                        "max_cooks_distance",
                        "n",
                        "skewness",
                        "kurtosis",
                    ),
                )
            elif value is None or type(value) in {str, int, float, bool}:
                add(["assumption_checks", index, key], value)
    for name, version in mapping(result.get("software_versions", {})):
        if name in {"python", "numpy", "scipy", "statsmodels", "polars"}:
            add(["software_versions", name], version)

    policy = config["missing_value_policy"]
    effective_missing = (
        "per_variable_nonmissing"
        if method == "descriptive"
        else "complete_original_pairs"
        if method in CORRELATIONS | {"paired_t", "wilcoxon"}
        else "complete_model_rows"
        if method == "linear_regression"
        else "complete_outcome_group_rows"
    )
    if policy == "error_on_missing":
        recorded_support = not issues(config) and (
            method not in NEW_REJECT_MISSING_METHODS
            or recorded_reject_missing_supported(method, result["method_version"])
        )
        effective_missing = "reject_missing" if recorded_support else "unsupported_request"
    wording = [
        "association_not_causation",
        "non_significance_not_no_effect",
        "raw_p_not_effect_size",
        "diagnostics_not_verified",
        "study_family_not_verified",
    ]
    if method in OMNIBUS:
        wording.append("no_pairwise_contrast_from_omnibus")
    if method in {"mann_whitney", "wilcoxon", "kruskal_wallis"}:
        wording.append("distribution_not_median")
    if method == "descriptive":
        wording.append("descriptive_sample_only")
    multiplicity = protocol.multiplicity
    has_ci = any(
        item["value"] is not None
        and (
            "confidence_interval" in item["path"]
            or "mean_ci" in item["path"]
            or "ci_lower" in item["path"]
            or "ci_upper" in item["path"]
        )
        and item["path"][-1] in {"lower", "upper", "ci_lower", "ci_upper"}
        for item in facts
    )
    has_effect = any(
        item["path"][3] == "effect_size" and type(item["value"]) in {int, float} for item in facts
    )
    value = {
        "version": "p4-result-facts-v1",
        "computed": False,
        "binding": binding,
        "method": {"id": method, "version": result["method_version"]},
        "requested": {
            "alternative": config["alternative"],
            "confidence_level": config["confidence_level"],
            "missing_value_policy": policy,
            "alpha": protocol.reporting.alpha if protocol.reporting else None,
        },
        "execution": {
            "alternative": "none"
            if method == "descriptive"
            else "omnibus"
            if method in OMNIBUS
            else config["alternative"]
            if method in DIRECTIONAL
            else "two_sided",
            "missing_semantics": effective_missing,
            "new_work_supported": not issues(config),
            "capability_issues": [item.code for item in issues(config)],
            "per_variable_exclusions_are_engine_records": True,
            "comparison_order": ci.get("group_order")
            if method in {"welch_t", "student_t"}
            else config["variables"]
            if method in {"paired_t", "wilcoxon"}
            else None,
        },
        "multiplicity": {
            "declared": multiplicity.model_dump(mode="json") if multiplicity else None,
            "plan_statistical_steps": sum(item.capability == "statistics" for item in plan.steps),
            "adjustment_executed": False,
            "study_family_verified": False,
        },
        "diagnostics": {
            "planned": protocol.diagnostics,
            "status": "NOT_VERIFIED",
            "recorded_count": len(checks),
            "recorded_pass_is_not_assumption_proof": True,
        },
        "facts": facts,
        "availability": {
            "ci": "available" if has_ci else "unavailable",
            "effect": "available" if has_effect else "unavailable",
            "p_value": "available" if result.get("p_value") is not None else "unavailable",
        },
        "wording": wording,
    }
    value["projection_hash"] = logical_hash(value)
    if len(canonical_bytes(value)) > MAX_BYTES:
        raise failure("RESULT_FACTS_LIMIT")
    return value
