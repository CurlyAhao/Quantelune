"""Fixed typed domain tools: fresh owner/source, same-token native effects and real facts."""

import asyncio
import uuid

from sqlalchemy import select

from app.models.project import Project
from app.models.research import ResearchAttempt, ResearchEvent
from app.models.user import User
from app.preview.context import digest, schema_snapshot
from app.research import (
    assistant_invocation,
    execution_lease,
    execution_spec,
    harness_binding,
    publication,
)
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import DisclosureSelection, credentials, failure
from app.research.harness_serde import SafeSerializer
from app.schemas.ml import MLExperimentCreateRequest
from app.schemas.preview import PreviewScope
from app.schemas.preview_extensions import ExplanationRequest, PlanProposal
from app.schemas.statistics import StatisticalRunRequest
from app.services import research_provider_service as providers
from app.services import research_service as ledger

CACHED = frozenset({"propose_statistics", "propose_plan", "execute_statistics", "execute_ml"})
EVENT = "harness.tool_result"


class FormalTools:
    def __init__(self, factory, token, gateway, execute):
        self.factory, self.token, self.gateway, self.execute = factory, token, gateway, execute
        self.lock = asyncio.Lock()

    async def context(self, db):
        binding = await harness_binding.existing(self.factory, self.token, "assistant")
        if binding is None:
            raise failure("INVOCATION_REQUIRED")
        run, scope = await ledger.run(db, self.token.run_id, self.token.owner_id)
        actor = await db.get(User, self.token.owner_id, populate_existing=True)
        if actor is None or not actor.is_active:
            raise failure("TOOL_DENIED")
        actual = await publication._confirmed(self.factory, self.token)
        attempt = await db.get(ResearchAttempt, self.token.attempt_id, populate_existing=True)
        invocation = await assistant_invocation.load(db, actor.id, run, attempt)
        if actual is None:
            _, _, lease, _ = await execution_lease.validate(db, self.token)
            if lease.pause_requested:
                raise failure("MODEL_PAUSED")
            await assistant_invocation.verify_current(db, actor.id, run, attempt, invocation)
        selection = DisclosureSelection.model_validate(invocation["selection"])
        source, config, origin, operation, _, _ = await execution_spec.describe(db, actor.id, run)
        columns = next(
            (item.columns for item in selection.datasets if item.dataset_id == str(source.id)), []
        )
        scope_data = PreviewScope(
            project_id=source.project_id,
            dataset_id=source.id,
            dataset_sha256=source.sha256,
            schema_sha256=digest(schema_snapshot(source.schema_json)),
            columns=columns,
        )
        return binding, run, scope, actor, scope_data, source, config, actual

    async def safe_call(self, name, args):
        from app.preview.harness import DOMAIN_TOOLS

        if name not in DOMAIN_TOOLS or not isinstance(args, dict):
            raise failure("TOOL_DENIED")
        # The unchanged typed factory uses model_dump(mode="python"), which
        # contains registered enums. Normalize ONLY these concrete DTOs here;
        # the checkpoint serializer must not revive arbitrary Enum classes.
        if name in {"propose_statistics", "execute_statistics", "ml_preflight", "execute_ml"}:
            request_type = (
                StatisticalRunRequest
                if name in {"propose_statistics", "execute_statistics"}
                else MLExperimentCreateRequest
            )
            if set(args) != {"config"} or set(args["config"]) - set(request_type.model_fields):
                raise failure("TOOL_CONFIGURATION_INVALID")
            args = {"config": request_type.model_validate(args["config"]).model_dump(mode="json")}
        elif name == "propose_plan":
            if set(args) != {"spec"}:
                raise failure("TOOL_CONFIGURATION_INVALID")
            args = {"spec": PlanProposal.model_validate(args["spec"]).model_dump(mode="json")}
        elif name == "explain_results":
            if set(args) != {"spec"}:
                raise failure("TOOL_CONFIGURATION_INVALID")
            args = {"spec": ExplanationRequest.model_validate(args["spec"]).model_dump(mode="json")}
        elif args:
            raise failure("TOOL_CONFIGURATION_INVALID")
        SafeSerializer().check(args)
        async with self.lock:
            async with self.factory() as db:
                binding, run, scope, actor, selected, source, config, actual = await self.context(
                    db
                )
                await ledger.verify_events(db, actor.id, scope.id)
                key = logical_hash(
                    {"binding_hash": binding.binding_hash, "name": name, "args": args}
                )
                identity = uuid.uuid5(binding.id, key)
                old = (
                    await db.scalars(
                        select(ResearchEvent)
                        .where(
                            ResearchEvent.workspace_id == scope.id,
                            ResearchEvent.entity_id == identity,
                            ResearchEvent.event_type == EVENT,
                        )
                        .limit(2)
                    )
                ).all()
                if name in CACHED and old:
                    if len(old) != 1:
                        raise failure("TOOL_RECEIPT_CONFLICT")
                    document = parse_document(old[0].payload_json)
                    if (
                        old[0].entity_kind != "harness_tool"
                        or old[0].logical_hash != logical_hash(document)
                        or document["key"] != key
                        or document["binding_hash"] != binding.binding_hash
                        or document["run_id"] != str(run.id)
                        or document["attempt_id"] != str(self.token.attempt_id)
                        or document["name"] != name
                        or document["args_hash"] != logical_hash(args)
                    ):
                        raise failure("TOOL_RECEIPT_CHANGED")
                    if name in {"execute_statistics", "execute_ml"}:
                        if actual is None or document["result"]["publication"] != actual:
                            raise failure("TOOL_RECEIPT_CHANGED")
                    return document["result"]
                if not selected.columns and name != "project_context":
                    raise failure("TOOL_SCOPE_REQUIRED")
                if name == "project_context":
                    project = await db.get(Project, selected.project_id, populate_existing=True)
                    result = {
                        "project_id": str(project.id),
                        "title": project.title,
                        "description": project.description,
                    }
                elif name == "field_explanation":
                    result = {
                        "dataset_id": str(source.id),
                        "columns": [
                            column
                            for column in schema_snapshot(source.schema_json)
                            if column["name"] in selected.columns
                        ],
                    }
                elif name == "result_facts":
                    result = await self.facts(db, run, selected, actor, actual)
                elif name == "propose_statistics":
                    body = StatisticalRunRequest.model_validate(args["config"])
                    self.columns(body, selected.columns)
                    from app.routers.statistics import validate_method

                    validation = await validate_method(body=body, current_user=actor)
                    if not validation.applicable:
                        raise failure("TOOL_CONFIGURATION_INVALID")
                    result = {
                        "state": "proposed",
                        "dataset_id": str(source.id),
                        "config": body.model_dump(mode="json"),
                        "executed": False,
                    }
                elif name in {"execute_statistics", "execute_ml"}:
                    if (name, run.kind) not in {
                        ("execute_statistics", "statistics"),
                        ("execute_ml", "ml"),
                    }:
                        raise failure("TOOL_EXECUTION_MISMATCH")
                    if name == "execute_statistics":
                        body = StatisticalRunRequest.model_validate(args["config"])
                        self.columns(body, selected.columns)
                        proposed = body.model_dump(mode="json")
                    else:
                        body_ml = MLExperimentCreateRequest.model_validate(args["config"])
                        self.columns(body_ml, selected.columns)
                        from app.services.ml_service import config_from_dict

                        proposed = config_from_dict(body_ml.model_dump(mode="json")).to_dict()
                    if canonical_bytes(proposed) != canonical_bytes(config):
                        raise failure("TOOL_EXECUTION_MISMATCH")
                    await db.rollback()  # No Session spans the owned native operation.
                    actual = await publication._confirmed(self.factory, self.token)
                    if actual is None:
                        actual = await self.execute(self.token)
                    (
                        binding,
                        run,
                        scope,
                        actor,
                        selected,
                        source,
                        config,
                        proved,
                    ) = await self.context(db)
                    if proved is None or proved != actual:
                        raise failure("TOOL_EXECUTION_UNPROVEN")
                    result = {"state": actual["state"], "publication": actual}
                elif name == "quality_summary":
                    # The registered quality tool remains a local summary read,
                    # not a silent Workflow/ML/statistics execution capability.
                    from app.preview.context import verify_storage_version
                    from app.services.ml_service import load_dataset_frame

                    await verify_storage_version(source)
                    frame = await load_dataset_frame(source)
                    result = {
                        "row_count": frame.height,
                        "null_counts": {
                            column: frame[column].null_count() for column in selected.columns
                        },
                    }
                elif name == "ml_preflight":
                    body_ml = MLExperimentCreateRequest.model_validate(args["config"])
                    self.columns(body_ml, selected.columns)
                    from app.ml.worker_control import MLTaskScope
                    from app.preview.context import ML_PREFLIGHT_FIELDS
                    from app.routers.ml import ml_preflight_stats, ml_validate

                    validation_ml = await ml_validate(body=body_ml, current_user=actor)
                    if not validation_ml["valid"]:
                        raise failure("TOOL_CONFIGURATION_INVALID")
                    source_id = source.id
                    await db.rollback()
                    actor = await db.get(User, self.token.owner_id, populate_existing=True)
                    async with MLTaskScope(cancel_check=self.cancel_check) as task:
                        values = await ml_preflight_stats(
                            dataset_id=source_id,
                            target=body_ml.target,
                            task_type=body_ml.task_type,
                            split_strategy=body_ml.split_strategy,
                            group_variable=body_ml.group_variable,
                            time_variable=body_ml.time_variable,
                            n_splits=body_ml.n_splits,
                            test_size=body_ml.test_size,
                            random_seed=body_ml.random_seed,
                            db=db,
                            current_user=actor,
                            task=task,
                        )
                    result = {key: values[key] for key in ML_PREFLIGHT_FIELDS}
                    (
                        binding,
                        run,
                        scope,
                        actor,
                        selected,
                        source,
                        config,
                        actual,
                    ) = await self.context(db)
                elif name == "propose_plan":
                    from app.preview.workspace import save_record
                    from app.schemas.workspace import PlanBody, SaveRecord

                    proposal = PlanProposal.model_validate(args["spec"])
                    body_plan = PlanBody(scope=selected, **proposal.model_dump())
                    saved = await save_record(
                        db,
                        actor,
                        SaveRecord(project_id=selected.project_id, payload=body_plan),
                        commit=False,
                    )
                    result = {
                        "state": "proposed",
                        "plan_id": saved["id"],
                        "revision": saved["revision"],
                        "executed": False,
                    }
                elif name == "explain_results":
                    from app.preview.extensions import explain_fields

                    request = ExplanationRequest.model_validate(args["spec"])
                    attached = await self.attached_results(db, run, selected, actor, actual)
                    if attached:
                        from app.research.result_contexts import explain

                        result = await explain(db, actor, request, attached)
                    else:
                        allowed = await self.allowed_results(db, actor.id, run, selected)
                        result = await explain_fields(db, selected, actor, request, allowed, False)
                else:
                    raise failure("TOOL_DENIED")
                _, cfg = await providers.current(db, actor.id, required=True)
                assert cfg is not None
                credential = providers._secret(self.gateway.vault, cfg, actor.id)
                if credentials(result, (credential,)):
                    raise failure("TOOL_SECRET")
                SafeSerializer().check(result)
                document = {
                    "version": 1,
                    "binding_hash": binding.binding_hash,
                    "run_id": str(run.id),
                    "attempt_id": str(self.token.attempt_id),
                    "key": key,
                    "name": name,
                    "args_hash": logical_hash(args),
                    "result": result,
                }
                await ledger._append(
                    db,
                    scope,
                    actor.id,
                    "harness_tool",
                    identity if name in CACHED else uuid.uuid4(),
                    EVENT,
                    document,
                )
                await db.commit()  # Proposal/draft and receipt commit together.
                return result

    @staticmethod
    def columns(body, selected):
        names: list[str] = []
        for field in ("variables", "categorical_variables", "features", "categorical_features"):
            names.extend(getattr(body, field, []))
        names.extend(getattr(body, "reference_categories", {}) or {})
        names.extend(
            value
            for field in ("group_variable", "time_variable", "target")
            if (value := getattr(body, field, None))
        )
        if not set(names) <= set(selected):
            raise failure("TOOL_SCOPE_CHANGED")

    async def allowed_results(self, db, owner, run, selected):
        # Only the exact saved result of this business Run is implicitly attached.
        # Explicit attached results use independently proven scopes above.
        if run.domain_kind not in {"statistics", "ml"} or run.domain_run_id is None:
            raise failure("TOOL_RESULT_REQUIRED")
        await publication._confirmed(self.factory, self.token)
        selected.statistical_run_id = run.domain_run_id if run.domain_kind == "statistics" else None
        selected.ml_run_id = run.domain_run_id if run.domain_kind == "ml" else None
        return {run.domain_run_id}

    async def facts(self, db, run, selected, actor, actual):
        attached = await self.attached_results(db, run, selected, actor, actual)
        if attached:
            return dict(
                kind="saved_result_facts",
                state=run.state,
                run_completed=actual is not None and actual["state"] == "completed",
                computed=False,
                results=[
                    dict(facts=item["facts"], citations=item["citations"])
                    for item in attached.values()
                ],
            )
        if actual is None:
            return {"state": run.state, "run_completed": False}
        if run.domain_kind == "statistics":
            from app.preview.context import statistical_facts

            return await statistical_facts(db, selected, actor, run.domain_run_id)
        if run.domain_kind == "ml":
            from app.preview.context import ml_facts

            return await ml_facts(db, selected, actor, run.domain_run_id)
        return {"state": actual["state"], "publication": actual}

    async def attached_results(self, db, run, selected, actor, actual):
        attempt = await db.get(ResearchAttempt, self.token.attempt_id, populate_existing=True)
        invocation = await assistant_invocation.load(db, actor.id, run, attempt)
        selection = DisclosureSelection.model_validate(invocation["selection"])
        if not selection.artifact_ids:
            return {}
        if invocation["version"] != 3:
            raise failure("INVOCATION_SPEC_REQUIRED")
        await assistant_invocation.verify_current(db, actor.id, run, attempt, invocation)
        from app.research import result_contexts

        values = await result_contexts.read(
            db, actor.id, run.session_id, selection, invocation["context_source_version"]
        )
        # Preserve the existing implicit current result, with the same byte proof.
        if actual is not None and run.domain_kind in {"statistics", "ml"}:
            key = (run.domain_kind, run.domain_run_id)
            if key not in values:
                from app.preview.context import ml_facts, statistical_facts

                snapshot, citation = await result_contexts.prove_result(
                    db, actor.id, uuid.UUID(actual["root_id"])
                )
                scope = selected.model_copy(
                    update={
                        "statistical_run_id": snapshot.source_id
                        if snapshot.kind == "statistics"
                        else None,
                        "ml_run_id": snapshot.source_id if snapshot.kind == "ml" else None,
                    }
                )
                facts = await (statistical_facts if snapshot.kind == "statistics" else ml_facts)(
                    db, scope, actor, snapshot.source_id
                )
                citation.update(
                    dataset_id=str(scope.dataset_id),
                    dataset_sha256=scope.dataset_sha256,
                    schema_sha256=scope.schema_sha256,
                    source_version=invocation["context_source_version"],
                )
                values[key] = dict(scope=scope, facts=facts, citations=[citation])
        return values

    async def cancel_check(self):
        try:
            async with self.factory() as db:
                _, _, lease, _ = await execution_lease.validate(db, self.token)
                return lease.pause_requested
        except Exception:
            return True


def runtime_factory(factory, execute):
    async def resolve(token):
        from app.research.registered_tools import build_tools
        from app.services.research_gateway_service import get_gateway

        gateway = get_gateway()
        runtime = FormalTools(factory, token, gateway, execute)
        return gateway, build_tools(runtime.safe_call)

    return resolve
