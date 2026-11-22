"""Pure metadata assessment; readiness never proves assumptions or computation."""

from typing import Literal

from app.research.context_contracts import ContextField, ContextSnapshot, FieldReference
from app.research.contracts import canonical_bytes, logical_hash
from app.research.protocol_contracts import (
    PROTOCOL_MAX_BYTES,
    ProtocolAssessment,
    ProtocolIssue,
    ResearchProtocolDocument,
)
from app.research.protocol_matrix import DIAGNOSTIC_IDS, requirement_for


def _fresh(document: ResearchProtocolDocument) -> ResearchProtocolDocument:
    # frozen=True does not make contained lists immutable. Never trust a model instance directly.
    return ResearchProtocolDocument.model_validate(document.model_dump(mode="python"))


def protocol_hash(document: ResearchProtocolDocument) -> str:
    value = _fresh(document).model_dump(mode="json")
    if len(canonical_bytes(value)) > PROTOCOL_MAX_BYTES:
        raise ValueError("Protocol exceeds the canonical content limit.")
    return logical_hash(value)


def assess_protocol(
    document: ResearchProtocolDocument,
    context: ContextSnapshot,
    *,
    method_id: str | None = None,
) -> ProtocolAssessment:
    doc = _fresh(document)
    ctx = ContextSnapshot.model_validate(context.model_dump(mode="python"))
    if doc.candidate_methods == ["evaluate_binary_predictions"]:
        from app.research.binary_prediction_evaluation import assessment

        return assessment(doc, ctx, method_id)
    if doc.candidate_methods == ["evaluate_predictions"]:
        from app.research.prediction_evaluation import assessment

        return assessment(doc, ctx, method_id)
    issues: set[tuple[str, str, Literal["clarification", "blocked"]]] = set()
    limitations = {"metadata_only_not_confirmation_or_execution", "diagnostics_not_verified"}
    required: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()

    def issue(code: str, field: str, severity: Literal["clarification", "blocked"] = "blocked"):
        issues.add((code, field, severity))

    def missing(field: str):
        issue("required_metadata_missing", field, "clarification")

    def informative(value: str | None) -> bool:
        return bool(value and not value.isspace())

    if doc.source_version != ctx.source_version:
        issue("source_version_mismatch", "source_version")
    if not informative(doc.research_question):
        missing("research_question")
    if not informative(doc.unit_of_analysis):
        missing("unit_of_analysis")
    for field in ("mode", "study_design", "estimand"):
        if getattr(doc, field) == "unknown":
            missing(field)
    if not doc.variables:
        missing("variables")
    if doc.estimand == "causal_effect":
        issue("causal_estimand_not_supported", "estimand")
    if doc.unresolved_questions:
        issue("unresolved_questions_remain", "unresolved_questions", "clarification")

    datasets = {d.dataset_id: d for d in ctx.datasets}
    if len(datasets) != len(ctx.datasets):
        issue("context_dataset_ambiguous", "context.datasets")
    fields: dict[tuple[object, str], list[ContextField]] = {}
    for dataset in ctx.datasets:
        for context_field in dataset.fields:
            fields.setdefault((dataset.dataset_id, context_field.reference.name), []).append(
                context_field
            )

    def bind(reference: FieldReference, path: str) -> ContextField | None:
        dataset = datasets.get(reference.dataset_id)
        candidates = fields.get((reference.dataset_id, reference.name), [])
        if dataset is None or (
            reference.dataset_sha256 != dataset.sha256
            or reference.schema_sha256 != dataset.schema_sha256
        ):
            issue("source_reference_mismatch", path)
            return None
        if len(candidates) != 1 or candidates[0].reference != reference:
            issue("field_reference_mismatch", path)
            return None
        return candidates[0]

    bound = []
    for index, variable in enumerate(doc.variables):
        bound.append(bind(variable.reference, f"variables.{index}.reference"))
        if variable.role == "unknown":
            missing(f"variables.{index}.role")
    source_ids = {variable.reference.dataset_id for variable in doc.variables}
    if doc.pairing and doc.pairing.pair_id:
        bind(doc.pairing.pair_id, "pairing.pair_id")
        source_ids.add(doc.pairing.pair_id.dataset_id)
    if len(source_ids) > 1:
        issue("cross_source_analysis_not_supported", "variables")

    roles: dict[str, list[int]] = {
        role: [index for index, variable in enumerate(doc.variables) if variable.role == role]
        for role in {variable.role for variable in doc.variables}
    }

    def cardinality(role: str, minimum: int, maximum: int | None = None):
        count = len(roles.get(role, []))
        if count < minimum:
            missing(f"variables.{role}")
        elif maximum is not None and count > maximum:
            issue("role_cardinality_conflict", f"variables.{role}")

    def numeric(role: str):
        for index in roles.get(role, []):
            field = bound[index]
            if field is not None and field.inferred_type != "numeric":
                issue("numeric_field_required", f"variables.{index}.reference")

    def expect(field: str, values: set[str]):
        value = getattr(doc, field)
        if value == "unknown":
            missing(field)
        elif value not in values:
            issue("design_metadata_conflict", field)

    design_roles = {
        "descriptive": {"analysis"},
        "association": {"analysis"},
        "independent_groups": {"outcome", "group"},
        "paired": {"outcome", "pairing_id"},
        "one_way_groups": {"outcome", "group"},
        "cross_sectional": {"outcome", "predictor"},
        "prediction": {"target", "predictor"},
    }
    allowed_roles = design_roles.get(doc.study_design)
    if allowed_roles:
        for index, variable in enumerate(doc.variables):
            if variable.role not in allowed_roles | {"unknown", "time", "cluster"}:
                issue("role_design_conflict", f"variables.{index}.role")

    if doc.study_design == "descriptive":
        cardinality("analysis", 1)
        expect("estimand", {"summary"})
        expect("mode", {"description"})
    elif doc.study_design == "association":
        cardinality("analysis", 2, 2)
        numeric("analysis")
        expect("estimand", {"association"})
        expect("mode", {"description", "inference"})
        expect("independence", {"independent"})
        if not any(informative(item) for item in doc.assumptions):
            missing("assumptions")
        limitations.add("association_not_causation")
    elif doc.study_design in {"independent_groups", "one_way_groups"}:
        cardinality("outcome", 1, 1)
        cardinality("group", 1, 1)
        numeric("outcome")
        expect("mode", {"inference"})
        expect("independence", {"independent"})
        expect(
            "estimand",
            {"mean_difference", "distribution_difference"}
            if (doc.study_design == "independent_groups")
            else {"group_difference", "distribution_difference"},
        )
        minimum = 2 if doc.study_design == "independent_groups" else 3
        if len(doc.group_definitions) < minimum:
            missing("group_definitions")
        elif doc.study_design == "independent_groups" and len(doc.group_definitions) != 2:
            issue("group_count_conflict", "group_definitions")
        if len(set(doc.group_definitions)) != len(doc.group_definitions):
            issue("group_definitions_not_distinct", "group_definitions")
        if any(not informative(group) for group in doc.group_definitions):
            missing("group_definitions")
    elif doc.study_design == "paired":
        cardinality("outcome", 2, 2)
        numeric("outcome")
        expect("mode", {"inference"})
        expect("independence", {"paired"})
        expect("estimand", {"paired_mean_difference", "paired_distribution_shift"})
        if doc.pairing is None:
            missing("pairing")
        else:
            if doc.pairing.layout == "unknown":
                missing("pairing.layout")
            elif doc.pairing.layout != "wide_same_row":
                issue("long_pairing_not_supported", "pairing.layout")
            if not informative(doc.pairing.description):
                missing("pairing.description")
            if len(doc.pairing.conditions) != 2 or any(
                not informative(value) for value in doc.pairing.conditions
            ):
                missing("pairing.conditions")
            elif len(set(doc.pairing.conditions)) != 2:
                issue("pairing_conditions_not_distinct", "pairing.conditions")
        limitations.add("ordered_paired_variables")
    elif doc.study_design == "cross_sectional":
        cardinality("outcome", 1, 1)
        cardinality("predictor", 1)
        numeric("outcome")
        expect("mode", {"inference"})
        expect("estimand", {"conditional_association"})
        expect("independence", {"independent"})
        limitations.add("conditional_association_not_causation")
    elif doc.study_design == "prediction":
        cardinality("target", 1, 1)
        cardinality("predictor", 1)
        expect("mode", {"prediction"})
        expect("estimand", {"prediction"})
        prediction = doc.prediction
        if prediction is None:
            missing("prediction")
        else:
            if prediction.task == "unknown":
                missing("prediction.task")
            if not informative(prediction.split_strategy):
                missing("prediction.split_strategy")
            if not informative(prediction.primary_metric):
                missing("prediction.primary_metric")
        limitations.add("formal_execution_not_supported")
    if doc.mode == "prediction" and doc.study_design not in {"prediction", "unknown"}:
        issue("prediction_inference_conflict", "mode")
    if doc.prediction is not None and doc.mode not in {"prediction", "unknown"}:
        issue("prediction_inference_conflict", "prediction")
    if doc.pairing is not None and doc.study_design not in {"paired", "unknown"}:
        issue("pairing_design_conflict", "pairing")

    inference = doc.mode == "inference"
    if inference:
        for field in ("population_and_sample", "missingness_context"):
            if not informative(getattr(doc, field)):
                missing(field)
        if not any(informative(item) for item in doc.assumptions):
            missing("assumptions")
        for field in (
            "analysis_intent",
            "missing_value_policy",
            "cluster_structure",
            "time_structure",
        ):
            if getattr(doc, field) == "unknown":
                missing(field)
        if doc.reporting is None:
            missing("reporting")
        if doc.cluster_structure == "present" or "cluster" in roles:
            issue("clustered_analysis_not_supported", "cluster_structure")
        if doc.time_structure == "present" and doc.study_design != "paired":
            issue("time_structure_not_supported", "time_structure")
        if doc.study_design == "cross_sectional" and "time" in roles:
            issue("time_structure_not_supported", "variables.time")
    else:
        if not informative(doc.population_and_sample):
            limitations.add("population_not_generalizable")
        if doc.reporting is None:
            limitations.add("reporting_not_prespecified")
        if not informative(doc.missingness_context) or doc.missing_value_policy == "unknown":
            limitations.add("missingness_not_prespecified")
    limitations.add("missingness_declaration_not_verified")

    multiplicity = doc.multiplicity
    if multiplicity is None:
        if inference or doc.study_design == "one_way_groups":
            missing("multiplicity")
    else:
        if multiplicity.policy == "unknown":
            missing("multiplicity.policy")
        if multiplicity.planned_comparisons is None:
            missing("multiplicity.planned_comparisons")
        if multiplicity.adjustment == "unknown":
            missing("multiplicity.adjustment")
        if multiplicity.policy == "single_prespecified" and (
            multiplicity.planned_comparisons not in {None, 1}
            or multiplicity.adjustment not in {"unknown", "none"}
        ):
            issue("single_comparison_policy_conflict", "multiplicity")
        elif multiplicity.policy == "family_adjusted":
            issue("family_adjusted_execution_not_supported", "multiplicity.policy")
        elif multiplicity.policy == "exploratory_unadjusted":
            limitations.add("unadjusted_exploratory_only")
            if doc.analysis_intent == "confirmatory":
                issue("unadjusted_confirmatory_conflict", "analysis_intent")
            elif doc.analysis_intent == "unknown":
                missing("analysis_intent")
            if multiplicity.adjustment not in {"none", "unknown"}:
                issue("unadjusted_policy_conflict", "multiplicity.adjustment")

    for index, candidate in enumerate(doc.candidate_methods):
        if requirement_for(candidate) is None:
            issue("method_not_supported", f"candidate_methods.{index}")
    for index, diagnostic in enumerate(doc.diagnostics):
        if diagnostic not in DIAGNOSTIC_IDS:
            issue("diagnostic_not_supported", f"diagnostics.{index}")
    if method_id is not None:
        requirement = requirement_for(method_id)
        if requirement is None:
            issue("method_not_supported", "method_id")
        if method_id not in doc.candidate_methods:
            issue("method_not_candidate", "method_id")
        if doc.mode == "prediction" or doc.study_design == "prediction":
            issue("formal_execution_not_supported", "method_id")
        elif requirement is not None:
            if requirement.design != doc.study_design:
                issue("method_design_conflict", "method_id")
            if doc.estimand != "unknown" and requirement.estimand != doc.estimand:
                issue("method_estimand_conflict", "estimand")
            for role in requirement.numeric_roles:
                numeric(role)
            required = requirement.required_diagnostics
            alternatives = requirement.alternatives
            limitations.update(requirement.limitations)
            if alternatives:
                limitations.add("alternatives_require_reassessment")
            for diagnostic in required:
                if diagnostic not in doc.diagnostics:
                    issue("diagnostic_plan_missing", f"diagnostics.{diagnostic}", "clarification")

    ordered = [
        ProtocolIssue(code=code, field=field, severity=severity)
        for code, field, severity in sorted(issues, key=lambda item: (item[1], item[0], item[2]))
    ]
    status: Literal["ready", "clarification", "blocked"] = (
        "blocked"
        if any(item.severity == "blocked" for item in ordered)
        else "clarification"
        if ordered
        else "ready"
    )
    return ProtocolAssessment(
        status=status,
        issues=ordered,
        required_diagnostics=list(required),
        alternatives=list(alternatives),
        limitations=sorted(limitations),
    )
