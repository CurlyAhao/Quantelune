"""Pure deterministic Plan validation against one freshly authorized context."""

from app.exceptions import StatForgeError
from app.ml.resources import MLResourceError
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash
from app.research.domain_compute import normalize
from app.research.plan_contracts import CapabilityContract, PlanDocument, RuleSelection
from app.statistics.registry import METHOD_REGISTRY, METHODSET_VERSION
from app.statistics.types import MethodId


def failure(code: str) -> StatForgeError:
    return StatForgeError(
        422, "RESEARCH_PLAN_" + code, "Plan requires valid explicit sources and parameters."
    )


def capabilities() -> list[CapabilityContract]:
    from app.quality.registry import RULESET_VERSION
    from app.services.profiling_service import ALGORITHM_VERSION, PROFILING_VERSION

    methods = [
        item
        for item in METHOD_REGISTRY.describe()
        if item["method_id"] in {"descriptive", "pearson", "spearman", "kendall"}
    ]
    if len(methods) != 4:
        raise failure("CAPABILITY_UNAVAILABLE")
    return [
        CapabilityContract(id="inspect", version="research-context-v1", effect="metadata_read"),
        CapabilityContract(
            id="profile",
            version=f"{PROFILING_VERSION}:{ALGORITHM_VERSION}",
            effect="saved_profile_read",
        ),
        CapabilityContract(id="quality", version=RULESET_VERSION, effect="native_run"),
        CapabilityContract(
            id="descriptive",
            version=METHODSET_VERSION,
            effect="native_run",
            methods=["descriptive"],
        ),
        CapabilityContract(
            id="correlation",
            version=METHODSET_VERSION,
            effect="native_run",
            methods=["pearson", "spearman", "kendall"],
        ),
    ]


def _configuration(operation: str, config: dict) -> dict:
    try:
        return normalize(operation, config)
    except (MLResourceError, ValueError, TypeError):
        raise failure("PARAMETER_INVALID") from None


def validate(plan: PlanDocument, context: ContextSnapshot) -> PlanDocument:
    # Revalidate mutable nested DTO containers at the boundary.
    try:
        for step in plan.steps:
            canonical_bytes(step.configuration)
    except (ValueError, TypeError):
        raise failure("DOCUMENT_INVALID") from None
    plan = PlanDocument.model_validate(plan.model_dump(mode="json"))
    try:
        canonical_bytes(plan.model_dump(mode="json"))
    except (ValueError, TypeError):
        raise failure("DOCUMENT_INVALID") from None
    if plan.source_version != context.source_version:
        raise failure("SOURCE_CHANGED")
    identifiers = [item.id for item in plan.steps]
    if len(set(identifiers)) != len(identifiers):
        raise failure("DUPLICATE_STEP")
    datasets = {item.dataset_id: item for item in context.datasets}
    normalized = []
    graph = {}
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
        fields = {item.reference.name: item for item in source.fields}
        names = [item.name for item in step.fields]
        if len(set(names)) != len(names):
            raise failure("FIELD_INVALID")
        for reference in step.fields:
            current = fields.get(reference.name)
            if current is None or current.reference != reference:
                raise failure("FIELD_INVALID")
        config = step.configuration
        if step.capability in {"inspect", "profile"}:
            if config:
                raise failure("PARAMETER_INVALID")
        elif step.capability == "quality":
            config = _configuration("research_quality", config)
            if config["ohlcv_mapping"] is not None and not set(
                config["ohlcv_mapping"].values()
            ).issubset(names):
                raise failure("FIELD_INVALID")
        else:
            config = _configuration("research_statistics", config)
            method = MethodId(config["method_id"])
            supported = (
                {MethodId.DESCRIPTIVE}
                if step.capability == "descriptive"
                else {MethodId.PEARSON, MethodId.SPEARMAN, MethodId.KENDALL}
            )
            if (
                method not in supported
                or not METHOD_REGISTRY.has(method)
                or config["variables"] != names
            ):
                raise failure("PARAMETER_INVALID")
            registered = METHOD_REGISTRY.get(method)
            if not registered.min_variables <= len(names) <= registered.max_variables or any(
                fields[name].inferred_type != "numeric" for name in names
            ):
                raise failure("FIELD_INVALID")
            # These v1 capabilities have no grouping/target/paired/regression
            # semantics. Preserve shared DTO defaults without silently ignoring
            # an explicitly changed unrelated statistical parameter.
            defaults = _configuration(
                "research_statistics", {"method_id": method.value, "variables": names}
            )
            semantic = {
                "method_id",
                "variables",
                "confidence_level",
                "missing_value_policy",
                "alternative",
            }
            if any(config[key] != defaults[key] for key in config if key not in semantic):
                raise failure("PARAMETER_INVALID")
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
    return plan.model_copy(update={"steps": normalized})


def plan_hash(plan: PlanDocument) -> str:
    return logical_hash(plan.model_dump(mode="json"))


def rule_proposal(context: ContextSnapshot, selection: RuleSelection) -> dict:
    from app.research.plan_contracts import PlanStep

    dataset = next(
        (item for item in context.datasets if item.dataset_id == selection.dataset_id), None
    )
    if dataset is None:
        return {"state": "clarification", "code": "choose_attached_dataset", "plan": None}
    fields = {item.reference.name: item.reference for item in dataset.fields}
    if not set(selection.field_names).issubset(fields) or (
        selection.capability in {"descriptive", "correlation"} and not selection.field_names
    ):
        return {"state": "clarification", "code": "choose_explicit_fields", "plan": None}
    if selection.capability == "correlation" and "method_id" not in selection.configuration:
        return {"state": "clarification", "code": "choose_correlation_method", "plan": None}
    config = dict(selection.configuration)
    if selection.capability == "descriptive":
        config.setdefault("method_id", "descriptive")
    if selection.capability in {"descriptive", "correlation"}:
        config.setdefault("variables", selection.field_names)
    plan = PlanDocument(
        source_version=context.source_version,
        title="Explicit structured analysis",
        steps=[
            PlanStep(
                id="analysis",
                capability=selection.capability,
                dataset_id=dataset.dataset_id,
                dataset_sha256=dataset.sha256,
                fields=[fields[name] for name in selection.field_names],
                configuration=config,
            )
        ],
    )
    try:
        result = validate(plan, context)
    except (StatForgeError, ValueError, TypeError):
        return {"state": "clarification", "code": "review_parameters", "plan": None}
    return {
        "state": "proposed",
        "plan": result.model_dump(mode="json"),
        "plan_hash": plan_hash(result),
        "executed": False,
    }
