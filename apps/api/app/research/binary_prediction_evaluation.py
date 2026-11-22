"""Metadata and exact Plan binding for existing predictions, never training."""

from app.research.protocol_contracts import ProtocolAssessment, ProtocolIssue
from app.research.protocol_matrix import DIAGNOSTIC_IDS
from app.statistics.binary_prediction_evaluation import BinaryPredictionEvaluation


def assessment(document, context, method_id=None):
    issues = []

    def add(code, field, severity="blocked"):
        issues.append(ProtocolIssue(code=code, field=field, severity=severity))

    if document.source_version != context.source_version:
        add("source_version_mismatch", "source_version")
    if document.candidate_methods != ["evaluate_binary_predictions"] or method_id not in {
        None,
        "evaluate_binary_predictions",
    }:
        add("evaluation_method_conflict", "candidate_methods")
    for name in ("mode", "study_design", "estimand"):
        if getattr(document, name) != "prediction":
            add("evaluation_design_conflict", name)
    for name in ("research_question", "unit_of_analysis"):
        if not getattr(document, name) or not getattr(document, name).strip():
            add("required_metadata_missing", name, "clarification")
    if document.unresolved_questions:
        add("unresolved_questions_remain", "unresolved_questions", "clarification")
    if any(item not in DIAGNOSTIC_IDS for item in document.diagnostics):
        add("diagnostic_not_supported", "diagnostics")
    if document.multiplicity is not None and document.multiplicity.policy == "family_adjusted":
        add("family_adjusted_execution_not_supported", "multiplicity.policy")
    prediction = document.prediction
    if prediction is None or prediction.task != "classification":
        add("evaluation_binary_classification_required", "prediction.task")
    if prediction is None or prediction.primary_metric not in {
        "recall",
        "accuracy",
        "precision",
        "specificity",
    }:
        add("evaluation_metric_required", "prediction.primary_metric", "clarification")
    roles = [v.role for v in document.variables]
    if sorted(roles) != ["analysis", "target"]:
        add("evaluation_roles_required", "variables")
    if (
        document.pairing is not None
        or document.cluster_structure == "present"
        or document.time_structure == "present"
        or document.independence in {"clustered", "repeated"}
    ):
        add("evaluation_adjustment_not_supported", "study_design")
    if document.missing_value_policy not in {"unknown", "error_on_missing"}:
        add("evaluation_missing_policy_conflict", "missing_value_policy")
    sources = {item.dataset_id: item for item in context.datasets}
    if len(sources) != len(context.datasets):
        add("context_dataset_ambiguous", "context.datasets")
    if len({v.reference.dataset_id for v in document.variables}) != 1:
        add("cross_source_analysis_not_supported", "variables")
    for index, variable in enumerate(document.variables):
        reference = variable.reference
        source = sources.get(reference.dataset_id)
        matches = [f for f in source.fields if f.reference == reference] if source else []
        if (
            source is None
            or source.sha256 != reference.dataset_sha256
            or source.schema_sha256 != reference.schema_sha256
            or len(matches) != 1
        ):
            add("field_reference_mismatch", f"variables.{index}.reference")
        elif matches[0].dtype not in {
            "Int8",
            "Int16",
            "Int32",
            "Int64",
            "Int128",
            "UInt8",
            "UInt16",
            "UInt32",
            "UInt64",
        }:
            add("integer_label_field_required", f"variables.{index}.reference")
    ordered = sorted(issues, key=lambda issue: (issue.field, issue.code))
    return ProtocolAssessment(
        status="blocked"
        if any(i.severity == "blocked" for i in ordered)
        else "clarification"
        if ordered
        else "ready",
        issues=ordered,
        required_diagnostics=[],
        alternatives=[],
        limitations=[
            "metadata_only_not_confirmation_or_execution",
            "existing_predictions_only",
            "no_training_or_repartition",
            "split_not_independently_verified",
            "no_inferential_test_or_ci",
            "stored_rows_only",
        ],
    )


def validate_configuration(configuration, document):
    """Called after normal source/field/Protocol validation; no default role guessing."""
    evaluation = BinaryPredictionEvaluation.model_validate(configuration["evaluation"])
    actual = [v.reference for v in document.variables if v.role == "target"]
    predicted = [v.reference for v in document.variables if v.role == "analysis"]
    if (
        actual != [evaluation.actual]
        or predicted != [evaluation.predicted]
        or document.prediction is None
        or document.prediction.primary_metric != evaluation.primary_metric
        or configuration["missing_value_policy"] != "error_on_missing"
    ):
        raise ValueError("Evaluation roles, primary metric or policy differ from the Protocol.")
    return evaluation
