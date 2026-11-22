"""Per-turn authorization, restricted egress and real deterministic domain tools."""

import asyncio
import json
import uuid
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError, TokenInvalidError
from app.models.preview import PreviewEffect, PreviewTurn
from app.models.user import User
from app.preview import vault as credentials
from app.preview.configuration import ENDPOINTS, get_config, not_ready, reserve
from app.preview.context import (
    ML_PREFLIGHT_FIELDS,
    canonical,
    digest,
    invalid,
    load_dataset,
    ml_facts,
    resolve_context,
    statistical_facts,
    verify_storage_version,
)
from app.preview.harness import HarnessAdapter, StatForgeBridge, assert_tools, protocol_messages
from app.preview.read_evidence import (
    EVIDENCE_LIMIT,
    READ_CATEGORIES,
    READ_LIMIT,
    project_records,
    validate_record,
)
from app.preview.security import contains_secret
from app.preview.write_fence import acquire_write_fence
from app.research.registered_tools import build_tools
from app.routers import ml as ml_routes
from app.routers import statistics as stat_routes
from app.schemas.ml import MLExperimentCreateRequest
from app.schemas.preview import ProviderConfig, TurnRequest
from app.schemas.preview_extensions import ExplanationRequest, PlanProposal
from app.schemas.statistics import StatisticalRunRequest
from app.services import ml_service
from app.services.identity_service import find_user


def response(turn: PreviewTurn) -> dict[str, Any]:
    return {
        "turn_id": str(turn.id),
        "request_id": turn.request_id,
        "status": turn.status,
        "error_code": turn.error_code,
        "evidence": json.loads(turn.response_json),
        "authorization": json.loads(turn.authorization_json),
        "model_narrative_published": False,
    }


class TurnRuntime:
    def __init__(self, db: AsyncSession, user: User, request: TurnRequest, turn: PreviewTurn):
        self.db, self.user, self.request, self.turn = db, user, request, turn
        self.user_id, self.turn_id = user.id, turn.id
        self.lock = asyncio.Lock()
        self.records: list[dict[str, Any]] = []
        self.created_runs: set[uuid.UUID] = set()
        self.explanation_failures = 0

    async def authorize(self) -> dict[str, Any]:
        context = await resolve_context(self.db, self.request.scope, self.user)
        if context["disclosure_hash"] != self.request.disclosure_hash:
            raise invalid()
        return context

    def approved(self, category: str) -> bool:
        return category in self.request.approved_categories

    def approved_form(
        self, kind: str, body: StatisticalRunRequest | MLExperimentCreateRequest
    ) -> bool:
        form = (
            self.request.scope.statistical_config
            if kind == "statistics"
            else self.request.scope.ml_config
        )
        return bool(
            self.approved("form")
            and self.request.approve_tool_summaries
            and form is not None
            and canonical(body.model_dump(mode="json")) == canonical(form.model_dump(mode="json"))
        )

    async def gateway(self, messages: list[BaseMessage], tools: list[dict[str, Any]]) -> AIMessage:
        async with self.lock:
            await self.authorize()
            assert_tools(tools, sdk=True)
            if self.explanation_failures >= 2 or any(
                record.get("kind") in {"plan_proposal", "validated_explanation"}
                for record in self.records
            ):
                # A saved proposal or trusted explanation is the visible outcome
                # of this turn. Finish through the SDK without another paid call;
                # free-text completion cannot add business evidence. Two rejected
                # explanations also exhaust the single permitted repair.
                return AIMessage(content="")
            row = await get_config(self.db, self.user_id)
            if (
                row is None
                or not row.key_configured
                or row.config_json == "{}"
                or row.revision != self.request.config_revision
            ):
                raise not_ready()
            config = ProviderConfig.model_validate_json(row.config_json)
            # Capture immutable credential/provider binding before the awaited
            # reservation commits. No post-commit ORM expiration can refresh it
            # to a concurrently saved provider's namespace.
            namespace = row.vault_namespace
            endpoint = ENDPOINTS[config.provider]
            # All messages originate from the exact approved initial projection,
            # registered tool schemas and filtered tool replies below.
            payload = {
                "model": config.model,
                "messages": protocol_messages(messages),
                "tools": tools,
                "thinking": {"type": "disabled"},
                "stream": False,
            }
            if config.max_output_tokens is not None:
                payload["max_tokens"] = config.max_output_tokens
            secret = credentials.vault.get(namespace, self.user_id)
            if not secret:
                raise not_ready()
            if contains_secret(payload, secret):
                raise StatForgeError(422, "PREVIEW_SECRET_INPUT", "Use only the secure key entry.")
            wire = canonical(payload).encode()
            await reserve(self.db, row, self.turn_id, config, len(wire))
            try:
                # No environment proxies, redirected endpoints or ambient SDK credentials.
                async with httpx.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    timeout=45,
                    transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=True),
                ) as client:
                    result = await client.post(
                        endpoint,
                        content=wire,
                        headers={
                            "Authorization": f"Bearer {secret}",
                            "Content-Type": "application/json",
                        },
                    )
                    if result.status_code != 200 or len(result.content) > 256000:
                        raise ValueError("provider response rejected")
                    if secret in result.text:
                        raise ValueError("credential echo rejected")
                    data = result.json()["choices"][0]["message"]
                    if contains_secret(data, secret):
                        raise ValueError("decoded credential echo rejected")
                calls = []
                for call in data.get("tool_calls", []):
                    function = call["function"]
                    args = json.loads(function["arguments"])
                    if contains_secret(args, secret):
                        raise ValueError("argument credential echo rejected")
                    if not isinstance(args, dict):
                        raise ValueError("invalid arguments")
                    calls.append({"id": call["id"], "name": function["name"], "args": args})
                # Discard provider free text entirely. Only trusted tool records are
                # published; provider prose cannot invent metrics/completed status.
                return AIMessage(content="", tool_calls=calls)
            except Exception:
                raise StatForgeError(
                    502, "PREVIEW_PROVIDER_FAILED", "Provider call failed; reservation retained."
                ) from None

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        async with self.lock:
            context = await self.authorize()
            if len(canonical(args).encode()) > 16000:
                raise StatForgeError(413, "PREVIEW_TOOL_INPUT_LIMIT", "Tool input exceeds limit.")
            scope = self.request.scope
            if name == "propose_plan":
                if not self.approved("schema"):
                    return {"state": "approval_required"}
                existing = next((r for r in self.records if r.get("kind") == "plan_proposal"), None)
                if existing:
                    # A provider response can contain multiple tool calls. They
                    # share this lock and cannot create a second plan in one turn.
                    return {**existing, "state": "proposal_already_recorded"}
                from app.preview.workspace import save_record
                from app.schemas.workspace import PlanBody, SaveRecord

                proposal = PlanProposal.model_validate(args["spec"])
                plan = PlanBody(
                    scope=scope.model_copy(
                        update={
                            "session_id": None,
                            "session_revision": None,
                            "statistical_config": None,
                            "ml_config": None,
                        }
                    ),
                    **proposal.model_dump(),
                )
                saved = await save_record(
                    self.db, self.user, SaveRecord(project_id=scope.project_id, payload=plan)
                )
                receipt = {
                    "kind": "plan_proposal",
                    "state": "proposed",
                    "plan_id": saved["id"],
                    "revision": saved["revision"],
                    "project_id": str(scope.project_id),
                }
                self.records.append(receipt)
                return receipt
            if name == "explain_results":
                if not self.approved("results"):
                    return {"state": "approval_required"}
                if self.explanation_failures >= 2:
                    return {
                        "state": "facts_only",
                        "facts": context["categories"].get("results", {}).get("current", []),
                    }
                from app.preview.extensions import explain_fields

                allowed = self.created_runs | {
                    run for run in (scope.statistical_run_id, scope.ml_run_id) if run
                }
                spec = ExplanationRequest.model_validate(args["spec"])
                explanation = await explain_fields(
                    self.db, scope, self.user, spec, allowed, self.approved("materials")
                )
                self.records.append(explanation)
                return explanation
            if name == "project_context":
                return (
                    context["categories"]["project"]
                    if self.approved("project")
                    else {"state": "approval_required"}
                )
            if name == "field_explanation":
                return (
                    context["categories"].get("schema", {})
                    if self.approved("schema")
                    else {"state": "approval_required"}
                )
            if name == "quality_summary":
                if not self.approved("quality"):
                    return {"state": "approval_required"}
                dataset = await load_dataset(self.db, scope, self.user)
                await verify_storage_version(dataset)
                frame = await ml_service.load_dataset_frame(dataset)
                return {
                    "row_count": frame.height,
                    "null_counts": {c: frame[c].null_count() for c in scope.columns},
                }
            if name == "result_facts":
                if not self.approved("results"):
                    return {"state": "approval_required"}
                return context["categories"].get("results", {}).get("current", [])
            if name in {"propose_statistics", "execute_statistics"}:
                body = StatisticalRunRequest.model_validate(args["config"])
                dataset = await load_dataset(self.db, scope, self.user)
                cols = [
                    *body.variables,
                    *body.categorical_variables,
                    *([body.group_variable] if body.group_variable else []),
                    *([body.target] if body.target else []),
                ]
                if not set(cols) <= set(scope.columns):
                    raise invalid()
                validation = await stat_routes.validate_method(body=body, current_user=self.user)
                if not validation.applicable:
                    raise StatForgeError(
                        422, "INVALID_METHOD_INPUT", "Statistical configuration is not applicable."
                    )
                proposed = {
                    "kind": "statistics_proposal",
                    "project_id": str(scope.project_id),
                    "dataset_id": str(scope.dataset_id),
                    "schema_sha256": scope.schema_sha256,
                    "config": body.model_dump(mode="json"),
                    "dataset_sha256": dataset.sha256,
                    "state": "proposed",
                }
                material_review = bool(scope.material_references) and not self.approved_form(
                    "statistics", body
                )
                if (
                    name == "propose_statistics"
                    or not self.request.local_auto_execute
                    or material_review
                ):
                    if material_review:
                        proposed["state"] = "material_parameter_review_required"
                    self.records.append(proposed)
                    return proposed
                if not self.approved("results") or not self.request.approve_tool_summaries:
                    return {"state": "approval_required"}
                await verify_storage_version(dataset)
                return await self.effect(name, body.model_dump(mode="json"), body)
            if name in {"ml_preflight", "execute_ml"}:
                body_ml = MLExperimentCreateRequest.model_validate(args["config"])
                dataset = await load_dataset(self.db, scope, self.user)
                cols = [
                    *body_ml.features,
                    body_ml.target,
                    *([body_ml.group_variable] if body_ml.group_variable else []),
                    *([body_ml.time_variable] if body_ml.time_variable else []),
                ]
                if not set(cols) <= set(scope.columns):
                    raise invalid()
                validation_ml = await ml_routes.ml_validate(body=body_ml, current_user=self.user)
                if not validation_ml["valid"]:
                    raise StatForgeError(
                        422, "INVALID_ML_INPUT", "ML configuration is not applicable."
                    )
                if name == "ml_preflight":
                    if not self.approved("quality") or not self.approved_form("ml", body_ml):
                        return {"state": "approval_required"}
                material_review_ml = bool(scope.material_references) and not self.approved_form(
                    "ml", body_ml
                )
                if name == "execute_ml" and (
                    not self.request.local_auto_execute or material_review_ml
                ):
                    proposed_ml = {
                        "kind": "ml_proposal",
                        "project_id": str(scope.project_id),
                        "dataset_id": str(scope.dataset_id),
                        "schema_sha256": scope.schema_sha256,
                        "config": body_ml.model_dump(mode="json"),
                        "dataset_sha256": dataset.sha256,
                        "state": "material_parameter_review_required"
                        if material_review_ml
                        else "proposed",
                    }
                    self.records.append(proposed_ml)
                    return proposed_ml
                if name == "execute_ml" and (
                    not self.approved("results") or not self.request.approve_tool_summaries
                ):
                    return {"state": "approval_required"}
                await verify_storage_version(dataset)
                # Full-frame preflight happens only after the matching approved
                # read scope or actual execution approval; pure proposals never
                # load a frame. Saved experiment execution rechecks its inputs.
                stats = await ml_routes.ml_preflight_stats(
                    dataset_id=dataset.id,
                    target=body_ml.target,
                    task_type=body_ml.task_type,
                    split_strategy=body_ml.split_strategy,
                    group_variable=body_ml.group_variable,
                    time_variable=body_ml.time_variable,
                    n_splits=body_ml.n_splits,
                    test_size=body_ml.test_size,
                    random_seed=body_ml.random_seed,
                    db=self.db,
                    current_user=self.user,
                )
                if name == "ml_preflight":
                    return {k: stats[k] for k in ML_PREFLIGHT_FIELDS}
                return await self.effect(name, body_ml.model_dump(mode="json"), body_ml)
            raise StatForgeError(403, "PREVIEW_TOOL_DENIED", "Tool unavailable.")

    async def effect(
        self,
        name: str,
        config: dict[str, Any],
        body: StatisticalRunRequest | MLExperimentCreateRequest,
    ) -> dict[str, Any]:
        operation = digest({"name": name, "config": config})
        existing = await self.db.scalar(
            select(PreviewEffect).where(
                PreviewEffect.turn_id == self.turn_id, PreviewEffect.operation_hash == operation
            )
        )
        if existing:
            if existing.status == "succeeded":
                return json.loads(existing.result_json)
            raise StatForgeError(
                409,
                "PREVIEW_EFFECT_INCOMPLETE",
                "Prior local operation needs inspection; not repeated.",
            )
        record = PreviewEffect(turn_id=self.turn_id, operation_hash=operation)
        self.db.add(record)
        await self.db.commit()  # interruption preserves pending; retry cannot duplicate
        record_id = record.id
        scope = self.request.scope
        try:
            await self.authorize()
            if isinstance(body, StatisticalRunRequest):
                assert scope.dataset_id is not None
                run = await stat_routes.run_statistics(
                    dataset_id=scope.dataset_id, body=body, db=self.db, current_user=self.user
                )
                facts = await statistical_facts(self.db, scope, self.user, run.id)
            else:
                assert scope.dataset_id is not None
                experiment = await ml_routes.create_experiment(
                    dataset_id=scope.dataset_id, body=body, db=self.db, current_user=self.user
                )
                run_ml = await ml_routes.run_experiment(
                    experiment_id=experiment.id, db=self.db, current_user=self.user
                )
                facts = await ml_facts(self.db, scope, self.user, run_ml.id)
            record.status = "succeeded"
            self.created_runs.add(uuid.UUID(facts["run_id"]))
            record.result_json = canonical(facts)
            await self.db.commit()  # real Run + effect success committed together
            self.records.append(facts)
            return facts
        except Exception:
            await self.db.rollback()
            failed_record = await self.db.get(PreviewEffect, record_id)
            if failed_record:
                failed_record.status = "failed"
                await self.db.commit()
            from app.services.identity_service import find_user

            identity = await find_user(self.db, user_id=self.user_id)
            if identity is not None:
                self.user = identity
            raise

    async def safe_call(self, name: str, args: dict[str, Any]) -> Any:
        try:
            value = await self.call(name, args)
            if name in READ_CATEGORIES and not (
                isinstance(value, dict) and value.get("state") == "approval_required"
            ):
                await self.publish_read(name, value)
            return value
        except Exception as exc:
            code = exc.error_code if isinstance(exc, StatForgeError) else "PREVIEW_TOOL_REJECTED"
            if name == "explain_results":
                self.explanation_failures += 1
                code = (
                    "PREVIEW_EXPLANATION_REPAIR_ONCE"
                    if self.explanation_failures == 1
                    else "PREVIEW_EXPLANATION_FACTS_ONLY"
                )
            rejected = {"kind": name, "state": "rejected", "error_code": code}
            self.records.append(rejected)
            if (
                name == "explain_results"
                and self.explanation_failures == 2
                and self.approved("results")
            ):
                await self.safe_call("result_facts", {})
            return rejected

    async def locked_guard(self, values: Any) -> None:
        identity = await find_user(self.db, user_id=self.user_id)
        if identity is None:
            raise TokenInvalidError()
        self.user = identity  # Rollback may have expired the original ORM identity.
        await acquire_write_fence(self.db, self.user_id)
        await self.authorize()
        if self.request.scope.dataset_id:
            await verify_storage_version(await load_dataset(self.db, self.request.scope, self.user))
        row = await get_config(self.db, self.user_id)
        if not row or not row.key_configured or row.revision != self.request.config_revision:
            raise not_ready()
        secret = credentials.vault.get(row.vault_namespace, self.user_id)
        if not secret:
            raise not_ready()
        if contains_secret(values, secret):
            raise StatForgeError(422, "PREVIEW_SECRET_INPUT", "Use only the secure key entry.")
        if len(canonical(values).encode()) > min(
            EVIDENCE_LIMIT, ProviderConfig.model_validate_json(row.config_json).max_input_bytes
        ):
            raise StatForgeError(413, "PREVIEW_TOOL_OUTPUT_LIMIT", "Tool evidence exceeds limit.")

    async def persist_locked(
        self, records: list[dict[str, Any]], status: str | None = None, error: str | None = None
    ) -> None:
        for item in records:
            if "binding" in item:
                validate_record(
                    item,
                    self.request.scope,
                    self.request.disclosure_hash,
                    self.request.approved_categories,
                )
        row = await self.db.get(PreviewTurn, self.turn_id)
        if row is None or row.user_id != self.user_id:
            raise invalid()
        row.response_json = canonical(records)
        if status is not None:
            row.status, row.error_code = status, error
        await self.db.commit()

    async def publish_read(self, name: str, value: Any) -> None:
        async with self.lock:
            await self.locked_guard(value)  # Scan the complete return, including unused fields.
            if len(canonical(value).encode()) > READ_LIMIT:
                raise StatForgeError(
                    413, "PREVIEW_TOOL_OUTPUT_LIMIT", "Tool evidence exceeds limit."
                )
            projected = project_records(
                name, value, self.request.scope, self.request.disclosure_hash
            )
            records = list(self.records)
            seen = {digest(item) for item in records}
            for item in projected:
                if digest(item) not in seen:
                    records.append(item)
                    seen.add(digest(item))
            await self.locked_guard(records)
            await self.persist_locked(records)
            self.records = records

    async def finish(self, status: str, error: str | None) -> None:
        async with self.lock:
            try:
                if self.records:
                    await self.locked_guard(self.records)
                else:
                    # An empty session saves no context or factual payload. A
                    # credential rotation does not turn session end into failure.
                    await acquire_write_fence(self.db, self.user_id)
                await self.persist_locked(self.records, status, error)
            except StatForgeError as exc:
                await self.db.rollback()
                # Do not flush the rejected records after a credential/scope change.
                # Real committed effects are retained separately for honest recovery.
                await acquire_write_fence(self.db, self.user_id)
                row = await self.db.get(PreviewTurn, self.turn_id)
                if row is None or row.user_id != self.user_id:
                    raise invalid() from None
                row.response_json = "[]"
                row.status, row.error_code = "failed", exc.error_code
                await self.db.commit()

    def tools(self) -> list[BaseTool]:
        return build_tools(self.safe_call)


async def read_turn_for_actor(
    db: AsyncSession, turn_id: uuid.UUID, actor_id: uuid.UUID
) -> dict[str, Any]:
    """Reauthorize identity after commit/rollback before exposing saved business facts."""
    actor = await find_user(db, user_id=actor_id)
    if actor is None:
        raise TokenInvalidError()
    # Even a nonexpired identity-map object may predate an external deactivation.
    # Select the scalar from the current DB transaction rather than trusting it.
    with db.no_autoflush:
        active = await db.scalar(select(User.is_active).where(User.id == actor_id))
    if not active:
        raise TokenInvalidError()
    from app.preview.recovery import read_saved

    return await read_saved(db, turn_id, actor)


async def run_turn(db: AsyncSession, user: User, request: TurnRequest) -> dict[str, Any]:
    actor_id = user.id  # authenticated scalar; never dereference an expired actor after rollback
    context = await resolve_context(db, request.scope, user)
    if (
        context["disclosure_hash"] != request.disclosure_hash
        or not request.approve_question
        or not request.approve_tool_summaries
        or not set(request.approved_categories) <= set(context["categories"])
    ):
        raise StatForgeError(
            403,
            "PREVIEW_DISCLOSURE_REQUIRED",
            "Approve this exact question and context disclosure.",
        )
    input_hash = digest(request.model_dump(mode="json"))
    existing = await db.scalar(
        select(PreviewTurn).where(
            PreviewTurn.user_id == actor_id, PreviewTurn.request_id == request.request_id
        )
    )
    if existing:
        if existing.input_hash != input_hash:
            raise StatForgeError(
                409, "PREVIEW_REQUEST_CONFLICT", "Request ID belongs to a different input."
            )
        return await read_turn_for_actor(db, existing.id, actor_id)
    config = await get_config(db, actor_id)
    if not config or not config.key_configured or config.revision != request.config_revision:
        raise not_ready()
    secret = credentials.vault.get(config.vault_namespace, actor_id)
    if not secret:
        raise not_ready()
    if (
        contains_secret(request.model_dump(mode="json"), secret)
        or contains_secret(json.loads(config.config_json), secret)
        or contains_secret(context, secret)
    ):
        raise StatForgeError(422, "PREVIEW_SECRET_INPUT", "Use only the secure key entry.")
    turn = PreviewTurn(
        user_id=actor_id,
        project_id=request.scope.project_id,
        request_id=request.request_id,
        input_hash=input_hash,
        scope_json=canonical(request.scope.model_dump(mode="json")),
        authorization_json=canonical(
            {
                "configuration": ProviderConfig.model_validate_json(config.config_json).model_dump(
                    mode="json"
                ),
                "config_revision": config.revision,
                "question": request.question,
                "approved_categories": request.approved_categories,
                "local_auto_execute": request.local_auto_execute,
                "disclosure_hash": request.disclosure_hash,
                "provider_verified": False,
            }
        ),
    )
    db.add(turn)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise StatForgeError(
            409, "PREVIEW_REQUEST_IN_PROGRESS", "Request already recorded; read its saved state."
        ) from None
    runtime = TurnRuntime(db, user, request, turn)
    if "materials" in request.approved_categories:
        runtime.records.append(
            {"kind": "source_references", "citations": context["categories"]["materials"]}
        )
    turn_id = turn.id
    prompt = canonical(
        {
            "question": request.question,
            "approved_context": {
                k: v for k, v in context["categories"].items() if k in request.approved_categories
            },
            "local_auto_execute": request.local_auto_execute,
        }
    )
    try:
        await HarnessAdapter().invoke(
            StatForgeBridge(gateway=runtime.gateway), runtime.tools(), prompt
        )
        status, error_code = "finished", None  # distinct from any business Run status
    except (StatForgeError, ValidationError) as exc:
        await db.rollback()
        saved = await db.get(PreviewTurn, turn_id)
        assert saved is not None
        turn = saved
        status = "failed"
        error_code = (
            exc.error_code if isinstance(exc, StatForgeError) else "PREVIEW_INVALID_PROPOSAL"
        )
    except Exception:
        await db.rollback()
        saved = await db.get(PreviewTurn, turn_id)
        assert saved is not None
        turn = saved
        status, error_code = "interrupted", "PREVIEW_INTERRUPTED"
    await runtime.finish(status, error_code)
    return await read_turn_for_actor(db, turn_id, actor_id)
