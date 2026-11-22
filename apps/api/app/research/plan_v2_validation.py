"""Pure Plan v2 metadata validation; no owner, confirmation or execution authority."""

from app.ml.resources import MLResourceError
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash
from app.research.domain_compute import normalize
from app.research.plan_contracts import PlanDocument, PlanStep
from app.research.plan_v2_contracts import PlanDocumentV2, PlanStepV2
from app.research.plan_validation import failure, validate
from app.research.protocol_contracts import ResearchProtocolDocument
from app.research.protocol_validation import assess_protocol, protocol_hash


def _fresh(plan: PlanDocumentV2) -> PlanDocumentV2:
    # Rebuild nested containers too: a frozen model can still contain changed lists/dicts.
    try:
        for step in plan.steps:
            canonical_bytes(step.configuration)
        return PlanDocumentV2.model_validate(plan.model_dump(mode="python"))
    except (AttributeError, TypeError) as error:
        raise ValueError("Invalid Plan v2 nested document.") from error


def plan_v2_hash(plan: PlanDocumentV2) -> str:
    return logical_hash(_fresh(plan).model_dump(mode="json"))


def _normalize(configuration: dict) -> dict:
    try:
        return normalize("research_statistics", configuration)
    except (MLResourceError, ValueError, TypeError):
        raise failure("PARAMETER_INVALID") from None


def _statistics(
    step: PlanStepV2, protocol: ResearchProtocolDocument, context: ContextSnapshot
) -> dict:
    expected_fields = [item.reference for item in protocol.variables]
    pair_id = protocol.pairing.pair_id if protocol.pairing else None
    if pair_id is not None and pair_id not in expected_fields:
        expected_fields.append(pair_id)
    if step.fields != expected_fields:
        raise failure("FIELD_INVALID")
    if any(reference.dataset_id != step.dataset_id for reference in expected_fields):
        raise failure("FIELD_INVALID")
    config = _normalize(step.configuration)
    method = config["method_id"]
    if assess_protocol(protocol, context, method_id=method).status != "ready":
        raise failure("PROTOCOL_NOT_READY")
    if method == "evaluate_binary_predictions":
        from app.research.binary_prediction_evaluation import validate_configuration

        try:
            validate_configuration(config, protocol)
        except (ValueError, KeyError, TypeError):
            raise failure("PARAMETER_INVALID") from None
        return config
    if method == "evaluate_predictions":
        from app.research.prediction_evaluation import validate_configuration

        try:
            validate_configuration(config, protocol)
        except (ValueError, KeyError, TypeError):
            raise failure("PARAMETER_INVALID") from None
        return config
    roles: dict[str, list[str]] = {}
    for variable in protocol.variables:
        roles.setdefault(variable.role, []).append(variable.reference.name)
    default = _normalize({"method_id": method})
    expected = dict(default)
    if method in {"descriptive", "pearson", "spearman", "kendall"}:
        expected["variables"] = roles.get("analysis", [])
    elif method in {
        "welch_t",
        "student_t",
        "mann_whitney",
        "one_way_anova",
        "welch_anova",
        "kruskal_wallis",
    }:
        expected["variables"] = roles["outcome"]
        expected["group_variable"] = roles["group"][0]
    elif method in {"paired_t", "wilcoxon"}:
        expected["variables"] = roles["outcome"]
        expected["paired"] = True
    elif method == "linear_regression":
        predictors = roles["predictor"]
        categoricals = config["categorical_variables"]
        if len(categoricals) != len(set(categoricals)) or not set(categoricals).issubset(
            predictors
        ):
            raise failure("PARAMETER_INVALID")
        if categoricals != [name for name in predictors if name in categoricals]:
            raise failure("PARAMETER_INVALID")
        source = next(item for item in context.datasets if item.dataset_id == step.dataset_id)
        field_types = {item.reference.name: item.inferred_type for item in source.fields}
        if any(field_types[name] != "numeric" and name not in categoricals for name in predictors):
            raise failure("FIELD_INVALID")
        references = config["reference_categories"]
        if (
            (references is None and categoricals)
            or (references is not None and set(references) != set(categoricals))
            or (
                references is not None
                and any(
                    not value or value.isspace() or len(value) > 255
                    for value in references.values()
                )
            )
        ):
            raise failure("PARAMETER_INVALID")
        expected["variables"] = [name for name in predictors if name not in categoricals]
        expected["target"] = roles["outcome"][0]
    else:
        raise failure("PARAMETER_INVALID")
    semantic = {"confidence_level", "alternative", "missing_value_policy"}
    if method == "linear_regression":
        # V56 follows the existing engine's disjoint numeric/categorical inputs.
        semantic |= {"categorical_variables", "reference_categories"}
    if any(config[key] != expected[key] for key in config if key not in semantic):
        raise failure("PARAMETER_INVALID")
    if protocol.reporting is not None and (
        config["confidence_level"] != protocol.reporting.confidence_level
        or config["alternative"] != protocol.reporting.alternative
    ):
        raise failure("REPORTING_CONFLICT")
    if protocol.missing_value_policy != "unknown" and (
        config["missing_value_policy"] != protocol.missing_value_policy
    ):
        raise failure("MISSING_POLICY_CONFLICT")
    return config


def validate_v2(
    plan: PlanDocumentV2, context: ContextSnapshot, protocol: ResearchProtocolDocument
) -> PlanDocumentV2:
    try:
        plan = _fresh(plan)
        context = ContextSnapshot.model_validate(context.model_dump(mode="python"))
        protocol = ResearchProtocolDocument.model_validate(protocol.model_dump(mode="python"))
    except (ValueError, TypeError, AttributeError):
        raise failure("DOCUMENT_INVALID") from None
    if (
        plan.source_version != context.source_version
        or protocol.source_version != plan.source_version
    ):
        raise failure("SOURCE_CHANGED")
    if plan.protocol_hash != protocol_hash(protocol):
        raise failure("PROTOCOL_CHANGED")
    if plan.research_question != protocol.research_question:
        raise failure("PROTOCOL_CHANGED")
    datasets = {item.dataset_id: item for item in context.datasets}
    if len(datasets) != len(context.datasets):
        raise failure("SOURCE_CHANGED")
    if assess_protocol(protocol, context).status != "ready":
        raise failure("PROTOCOL_NOT_READY")
    identifiers = [step.id for step in plan.steps]
    if len(set(identifiers)) != len(identifiers):
        raise failure("DUPLICATE_STEP")
    graph: dict[str, set[str]] = {}
    normalized = []
    for step in plan.steps:
        source = datasets.get(step.dataset_id)
        if source is None or source.sha256 != step.dataset_sha256:
            raise failure("SOURCE_CHANGED")
        if (
            len(set(step.depends_on)) != len(step.depends_on)
            or step.id in step.depends_on
            or not set(step.depends_on).issubset(identifiers)
        ):
            raise failure("DEPENDENCY_INVALID")
        graph[step.id] = set(step.depends_on)
        fields = {item.reference.name: item.reference for item in source.fields}
        if len(fields) != len(source.fields) or len({ref.name for ref in step.fields}) != len(
            step.fields
        ):
            raise failure("FIELD_INVALID")
        for reference in step.fields:
            if (
                reference.dataset_id != source.dataset_id
                or reference.dataset_sha256 != source.sha256
                or reference.schema_sha256 != source.schema_sha256
                or fields.get(reference.name) != reference
            ):
                raise failure("FIELD_INVALID")
        if step.capability == "statistics":
            config = _statistics(step, protocol, context)
        else:
            # Only a local pure adapter. Never emit/persist v1 as a fallback for v2.
            legacy_step = PlanStep.model_validate(
                dict(step.model_dump(mode="python"), depends_on=[])
            )
            legacy = PlanDocument(
                source_version=plan.source_version,
                title=plan.title,
                research_question=plan.research_question,
                steps=[legacy_step],
            )
            config = validate(legacy, context).steps[0].configuration
        normalized.append(step.model_copy(update={"configuration": config}))
    remaining = dict(graph)
    while remaining:
        ready = {identifier for identifier, edges in remaining.items() if not edges}
        if not ready:
            raise failure("DEPENDENCY_CYCLE")
        remaining = {
            identifier: edges - ready
            for identifier, edges in remaining.items()
            if identifier not in ready
        }
    count = sum(step.capability == "statistics" for step in plan.steps)
    multiplicity = protocol.multiplicity
    if multiplicity is not None and (
        (multiplicity.policy == "single_prespecified" and count > 1)
        or (
            protocol.mode == "inference"
            and multiplicity.planned_comparisons is not None
            and count > multiplicity.planned_comparisons
        )
    ):
        raise failure("COMPARISON_LIMIT")
    # Also enforce the size bound after native normalization materializes defaults.
    try:
        return PlanDocumentV2.model_validate(
            plan.model_copy(update={"steps": normalized}).model_dump(mode="python")
        )
    except (ValueError, TypeError):
        raise failure("DOCUMENT_INVALID") from None
