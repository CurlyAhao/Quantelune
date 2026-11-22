"""Formal once-only model calls. Real business completion remains the domain kernel's job."""

import asyncio
import hashlib
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.research_security import ResearchApproval, ResearchBudgetAccount, ResearchCharge
from app.preview.vault import Vault
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import (
    DisclosureSelection,
    PreparedWire,
    PriceEvidence,
    ProviderReply,
    connection_profile,
    credentials,
    failure,
    parse_reply,
    prepare_wire,
    require_network_connection,
)
from app.research.model_history import profile_for
from app.research.price_catalog import PriceCatalog
from app.research.security_contracts import CallSpecification, PriceSnapshot, estimate
from app.research.transport import HttpTransport, Transport
from app.schemas.research_security import ProviderSettings
from app.services import research_provider_service as providers
from app.services import research_security_service as security
from app.services import research_service as ledger
from app.services import research_source_service as sources


@dataclass(frozen=True)
class PreparedCall:
    specification: CallSpecification
    wire: PreparedWire
    credential: str
    usage_profile: Literal["generic", "deepseek_cached"]


def same_specification(first: CallSpecification, second: CallSpecification) -> bool:
    return canonical_bytes(first.model_dump(mode="json")) == canonical_bytes(
        second.model_dump(mode="json")
    )


def private_key(
    value: str, kind: str, owner: uuid.UUID, run_id: uuid.UUID, known_secret: str | None = None
) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or credentials(value, (known_secret,) if known_secret else ())
    ):
        raise failure("IDEMPOTENCY_SECRET", 422)
    document = {
        "version": "gateway-key-v1",
        "kind": kind,
        "owner": str(owner),
        "run": str(run_id),
        "value": value,
    }
    return "gw1:" + hashlib.sha256(canonical_bytes(document)).hexdigest()


def approval_summary(row: ResearchApproval) -> dict[str, Any]:
    document = parse_document(row.snapshot_json)
    spec = CallSpecification.model_validate(document["specification"])
    amount = estimate(spec)
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "state": row.state,
        "request_hash": row.request_hash,
        "expires_at_ms": row.expires_at_ms,
        "scope": spec.scope,
        "purpose": spec.purpose,
        "payload_sha256": spec.payload_sha256,
        "source_binding_sha256": spec.source_binding_sha256,
        "configuration_hash": spec.config_sha256,
        "input_bytes": spec.input_bytes,
        "reserved_cost_units": None if spec.price.pricing_mode == "unpriced" else amount.cost,
        "currency": "USD",
        "cost_unit": "0.00000001",
        "provider": spec.price.provider,
        "model": spec.price.model,
        "price_verified": bool(not spec.price.synthetic and spec.price.verified_at_ms is not None),
        "pricing_mode": spec.price.pricing_mode,
    }


def charge_summary(row: ResearchCharge, *, reused: bool = False) -> dict[str, Any]:
    unpriced = parse_document(row.specification_json)["price"].get("pricing_mode") == "unpriced"
    return {
        "charge_id": str(row.id),
        "run_id": str(row.run_id),
        "state": row.state,
        "record_reused": reused,
        "reserved_cost_units": None if unpriced else row.reserved_cost,
        "used_cost_units": None if unpriced else row.used_cost,
        "pricing_mode": "unpriced" if unpriced else "estimated",
        "used_tokens": row.used_tokens,
        "used_ms": row.used_ms,
        "currency": "USD",
        "cost_unit": "0.00000001",
    }


class Gateway:
    def __init__(self, catalog: Mapping[str, PriceEvidence] | None = None) -> None:
        self._catalog_supplied = catalog is not None
        self._price_profiles: PriceCatalog | None = None
        self.catalog: Mapping[str, PriceEvidence] = MappingProxyType(dict(catalog or {}))
        self.vault: Vault = providers.vault
        self.transport: Transport = HttpTransport(self.catalog)
        self._testing = False
        self._tools_json = "[]"

    def configure_runtime_catalog(self, root: Path | None) -> None:
        """Boot-only operator configuration; explicit runtimes retain their binding."""
        from app.research.price_catalog import CatalogUnavailableError, load

        if self._testing or self._catalog_supplied:
            return
        self.catalog = MappingProxyType({})
        self.transport = HttpTransport(self.catalog)
        self._price_profiles = None
        if root is not None:
            try:
                self._price_profiles = load(root, now_ms=security.clock_ms())
            except CatalogUnavailableError:
                pass  # Keep deterministic/offline use available; network guard remains closed.

    def _price_evidence(self, price: PriceSnapshot, fingerprint: str) -> PriceEvidence | None:
        evidence = self.catalog.get(fingerprint)
        if evidence is None and self._price_profiles is not None and not self._testing:
            evidence = self._price_profiles.bind(price, fingerprint, now_ms=security.clock_ms())
            if evidence is not None:
                if not isinstance(self.transport, HttpTransport):
                    raise failure("TRANSPORT_UNQUALIFIED", 503)
                self.catalog = MappingProxyType({**self.catalog, fingerprint: evidence})
                # Keep the transport and gateway on the same immutable snapshot.
                self.transport.catalog = self.catalog
        return evidence

    @classmethod
    def for_tests(cls, vault: Vault, transport: Transport) -> "Gateway":
        if settings.environment != "test" or transport.network:
            raise RuntimeError("A recorder is restricted to the isolated test environment.")
        instance = cls()
        instance.vault, instance.transport, instance._testing = vault, transport, True
        return instance

    def registered_tools(self, tools: Sequence[dict[str, Any]]) -> None:
        # Internal registration only; no public API accepts tools or this runtime.
        self._tools_json = canonical_bytes(list(tools)).decode("utf-8")

    async def prepare(
        self,
        db: AsyncSession,
        owner: uuid.UUID,
        run_id: uuid.UUID,
        selection: DisclosureSelection,
        *,
        conversation: Sequence[dict[str, Any]] | None = None,
        model_tools: Sequence[dict[str, Any]] | None = None,
        _planner_proof=None,
    ) -> PreparedCall:
        from app.research import planner_v2

        await acquire_write_fence(db, owner)
        run, _ = await ledger.run(db, run_id, owner)
        draft_context = None
        if run.kind == "draft_advice":
            from app.research import draft_advice_gateway as advice

            await advice.fresh(db, owner, run, selection)
            draft_context = advice.wire_context(advice.parameters(run))
            expected_tools = advice.schemas(draft_context["draft"])
            if model_tools is not None:
                advice.assert_tools(draft_context["draft"], model_tools)
            model_tools = expected_tools
        protocol_metadata = None
        reviewer_metadata = None
        review_history_proof = None
        if run.kind == "reviewer":
            from app.models.research import ResearchAttempt, ResearchEvent
            from app.research import reviewer_history, reviewer_sources, reviewer_tools

            current_attempt = await db.scalar(
                select(ResearchAttempt).where(
                    ResearchAttempt.run_id == run.id,
                    ResearchAttempt.number == run.attempt_count,
                )
            )
            if current_attempt is None:
                raise failure("INVOCATION_BINDING_CHANGED")
            if (
                await db.scalar(
                    select(ResearchEvent.id).where(
                        ResearchEvent.entity_id == current_attempt.id,
                        ResearchEvent.event_type == "reviewer.review_receipted",
                    )
                )
                is not None
            ):
                raise failure("REVIEWER_RECEIPT_ONLY")
            source = await reviewer_sources.live(db, owner, run)
            reviewer_metadata = reviewer_sources.disclosure(source)
            reviewer_sources.selected_metadata(reviewer_metadata, selection)
            review_history_proof = await reviewer_history.prove(
                db, owner, run, [] if conversation is None else conversation
            )
            reviewer_tools.assert_tools(
                parse_document(self._tools_json) if model_tools is None else model_tools
            )
        if run.kind == "planner":
            from app.models.research_planning import ResearchPlanReceipt

            if (
                await db.scalar(
                    select(ResearchPlanReceipt.id).where(ResearchPlanReceipt.run_id == run.id)
                )
                is not None
            ):
                raise failure("PLANNING_ALREADY_RECEIPTED")
            from app.research import planner_admission
            from app.research.planner_protocol import assert_tools_v2

            protocol = (
                await planner_v2.live(db, owner, run)
                if _planner_proof is None
                else _planner_proof.consume(db, owner, run)
            )
            if protocol is not None:
                planner_v2.selected(protocol, selection)
                assert_tools_v2(
                    parse_document(self._tools_json) if model_tools is None else model_tools
                )
                admitted = await planner_admission.load(db, owner, run)
                if admitted is not None and (
                    admitted["selection"] != selection.model_dump(mode="json")
                    or admitted["tools"] != list(model_tools or parse_document(self._tools_json))
                ):
                    raise failure("INVOCATION_BINDING_CHANGED")
                protocol_metadata = planner_v2.metadata(protocol)
        state, cfg = await providers.current(db, owner, required=True)
        assert state is not None and cfg is not None
        options = ProviderSettings.model_validate(parse_document(cfg.document_json)["settings"])
        fingerprint = providers.binding(state, cfg)
        price = options.unverified_price(cfg.fingerprint)
        profile = connection_profile(price.provider)
        field = profile.output_token_field
        usage_profile = profile.usage_profile
        evidence = self._price_evidence(price, fingerprint)
        if evidence is not None:
            qualified = evidence.price
            if (
                qualified.provider != price.provider
                or qualified.model != price.model
                or qualified.currency != price.currency
                or qualified.input_per_million != price.input_per_million
                or qualified.output_per_million != price.output_per_million
            ):
                raise failure("PRICE_CONFIGURATION_CHANGED", 503)
            price = qualified
            if evidence.output_token_field is not None:
                field = evidence.output_token_field
            if evidence.usage_profile is not None:
                usage_profile = evidence.usage_profile
        if self._testing:
            if settings.environment != "test" or self.transport.network:
                raise failure("TEST_RUNTIME_DENIED", 503)
            price = price.model_copy(update={"synthetic": True, "verified_at_ms": None})
        projection = await sources.project(db, owner, run_id, selection)
        credential = providers._secret(self.vault, cfg, owner)
        wire = prepare_wire(
            selection,
            price,
            projection.sources,
            max_output_tokens=options.max_output_tokens,
            tools=parse_document(self._tools_json) if model_tools is None else model_tools,
            owned_secrets=(credential,),
            output_token_field=field,
            conversation=conversation,
            tool_profile="draft-advice"
            if run.kind == "draft_advice"
            else "planner"
            if run.kind == "planner"
            else profile_for(run.kind, parse_document(run.input_json)["parameters"]),
            research_protocol=protocol_metadata,
            review_context=reviewer_metadata,
            review_history_proof=review_history_proof,
            thinking_mode=evidence.thinking_mode if evidence is not None else profile.thinking_mode,
            planner_wire_version=planner_v2.wire_version(run),
            draft_context=draft_context,
        )
        if len(wire.body) > options.max_input_bytes:
            raise failure("INPUT_LIMIT", 422)
        spec = CallSpecification(
            purpose=selection.purpose,
            scope=selection.scope,
            payload_sha256=wire.sha256,
            config_sha256=fingerprint,
            price=price,
            input_bytes=len(wire.body),
            max_output_tokens=options.max_output_tokens,
            timeout_ms=options.timeout_ms,
            source_binding_sha256=logical_hash(
                dict(
                    version="research-source-binding-reviewer-v1",
                    source_binding_sha256=projection.binding_sha256,
                    source_proof_hash=logical_hash(source),
                    review_context=reviewer_metadata,
                )
            )
            if reviewer_metadata is not None
            else logical_hash(
                dict(
                    version="research-source-binding-protocol-v1",
                    source_binding_sha256=projection.binding_sha256,
                    research_protocol=protocol_metadata,
                )
            )
            if protocol_metadata is not None
            else logical_hash(
                {
                    "version": "research-source-binding-draft-advice-v1",
                    "source_binding_sha256": projection.binding_sha256,
                    "draft_context": draft_context,
                }
            )
            if draft_context is not None
            else projection.binding_sha256,
        )
        return PreparedCall(spec, wire, credential, usage_profile)

    def guard(self, prepared: PreparedCall) -> None:
        if self._testing:
            if (
                settings.environment != "test"
                or self.transport.network
                or not prepared.specification.price.synthetic
            ):
                raise failure("TEST_RUNTIME_DENIED", 503)
            return
        require_network_connection(prepared.specification, self.catalog, now_ms=security.clock_ms())
        if not isinstance(self.transport, HttpTransport):
            raise failure("TRANSPORT_UNQUALIFIED", 503)
        self.transport.guard(prepared.specification, prepared.wire.body, security.clock_ms())

    async def propose(
        self,
        db: AsyncSession,
        owner: uuid.UUID,
        run_id: uuid.UUID,
        proposal_key: str,
        selection: DisclosureSelection,
        *,
        conversation: Sequence[dict[str, Any]] | None = None,
        model_binding=None,
        model_call_hash: str | None = None,
        model_tools: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if (conversation is None) != (model_binding is None) or (
            model_binding is not None and (model_call_hash is None or model_tools is None)
        ):
            raise failure("MODEL_BINDING_CHANGED")
        prepared = await self.prepare(
            db, owner, run_id, selection, conversation=conversation, model_tools=model_tools
        )
        key = private_key(proposal_key, "proposal", owner, run_id, prepared.credential)
        row = await security.propose(db, owner, run_id, key, prepared.specification)
        if model_binding is not None:
            from app.research.model_request import record

            await record(
                db, owner, row, model_binding, model_call_hash, conversation, selection, model_tools
            )
        return {
            **approval_summary(row),
            "disclosure": prepared.wire.disclosure,
            "execution_kind": "test_recorder" if self._testing else "provider",
        }

    async def decide(
        self,
        db: AsyncSession,
        owner: uuid.UUID,
        approval_id: uuid.UUID,
        request_hash: str,
        decision: Literal["approve", "deny", "revoke"],
        selection: DisclosureSelection | None = None,
    ) -> dict[str, Any]:
        await acquire_write_fence(db, owner)
        row = await security.approval(db, owner, approval_id)
        if decision == "approve":
            if selection is None:
                raise failure("DISCLOSURE_REQUIRED", 422)
            bound = await self._model_context(db, owner, row, selection)
            fresh = await self.prepare(db, owner, row.run_id, selection, **bound)
            recorded = CallSpecification.model_validate(
                parse_document(row.snapshot_json)["specification"]
            )
            if not recorded.source_binding_sha256 or not same_specification(
                recorded, fresh.specification
            ):
                raise failure("APPROVAL_STALE")
        row = await security.decide(db, owner, approval_id, request_hash, decision)
        return approval_summary(row)

    async def _model_context(
        self, db, owner, approval, selection, *, dispatch=False, _planner_prepared=None
    ):
        from app.research.model_request import load

        recorded = await load(db, owner, approval)
        if recorded is None:
            current_run, _ = await ledger.run(db, approval.run_id, owner)
            if current_run.kind == "draft_advice":
                from app.research import draft_advice_gateway as advice
                from app.services.draft_advice_service import require_dispatch_task

                if dispatch:
                    require_dispatch_task(owner, current_run.id)
                return {"model_tools": advice.schemas(advice.parameters(current_run)["context"])}
            if dispatch and (await ledger.run(db, approval.run_id, owner))[0].kind == "reviewer":
                raise failure("REVIEWER_MODEL_REQUEST_REQUIRED")
            return {}
        if recorded["selection"] != selection.model_dump(mode="json"):
            raise failure("MODEL_BINDING_CHANGED")
        if dispatch:
            from app.models.research_execution import ResearchExecutionLease
            from app.research.execution_lease import ExecutionToken, validate

            attempt_id = uuid.UUID(recorded["attempt_id"])
            run, _ = await ledger.run(db, approval.run_id, owner)
            if run.kind == "reader":
                from app.models.research import ResearchAttempt
                from app.research import reader_control

                attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
                if attempt is None or await reader_control.cancelled(db, owner, run, attempt):
                    raise failure("MODEL_CANCELLED")
                from app.models.research_planning import ResearchPlanningLease
                from app.research.reader_contracts import ReaderToken
                from app.research.reader_lease import validate as reader_validate

                reader = await db.get(ResearchPlanningLease, attempt_id, populate_existing=True)
                if reader is None or reader.pause_requested:
                    raise failure("MODEL_PAUSED")
                reader_token = ReaderToken(
                    owner, run.id, attempt_id, reader.worker_id, reader.fence
                )
                current, _, _, _ = await reader_validate(db, reader_token, running=False)
            elif run.kind == "reviewer":
                from app.models.research_planning import ResearchPlanningLease
                from app.research import reviewer_lease, reviewer_sdk_tasks
                from app.research.reviewer_contracts import ReviewerToken

                if type(recorded["version"]) is not int or recorded["version"] != 4:
                    raise failure("REVIEWER_MODEL_REQUEST_REQUIRED")
                reviewer = await db.get(ResearchPlanningLease, attempt_id, populate_existing=True)
                if reviewer is None:
                    raise failure("MODEL_BINDING_CHANGED")
                reviewer_token = ReviewerToken(
                    owner, run.id, attempt_id, reviewer.worker_id, reviewer.fence
                )
                current, _, _, _ = await reviewer_lease.validate(db, reviewer_token, running=False)
                invocation = reviewer_sdk_tasks.require_callback(
                    reviewer_token, recorded["binding_hash"]
                )
                if invocation.binding_id != recorded["binding_id"]:
                    raise failure("MODEL_BINDING_CHANGED")
            elif run.kind == "planner":
                from app.models.research import ResearchAttempt
                from app.research import planning_control

                attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
                if attempt is None or await planning_control.cancelled(db, owner, run, attempt):
                    raise failure("MODEL_CANCELLED")
                from app.models.research_planning import ResearchPlanningLease
                from app.research.planning_lease import PlanningToken
                from app.research.planning_lease import validate as planning_validate

                planning = await db.get(ResearchPlanningLease, attempt_id, populate_existing=True)
                if planning is None or planning.pause_requested:
                    raise failure("MODEL_PAUSED")
                planning_token = PlanningToken(
                    owner, run.id, attempt_id, planning.worker_id, planning.fence
                )
                current, _, _, _ = await planning_validate(db, planning_token, running=False)
            else:
                lease = await db.get(ResearchExecutionLease, attempt_id, populate_existing=True)
                if lease is None or lease.pause_requested:
                    raise failure("MODEL_PAUSED")
                execution_token = ExecutionToken(
                    owner, approval.run_id, attempt_id, lease.worker_id, lease.fence
                )
                current, _, _, _ = await validate(db, execution_token, running=False)
            if current.state not in {"running", "waiting_approval"}:
                raise failure("MODEL_CANCELLED")
            if run.kind == "planner" and _planner_prepared is not None:
                await _planner_prepared(current, planning_token)
        return {"conversation": recorded["conversation"], "model_tools": recorded["tools"]}

    async def _prepare_dispatch(self, db, owner, approval, selection):
        from app.research import planner_v2
        from app.research.planner_preparation import _Preparation

        proof = None

        async def prepared(run, token):
            nonlocal proof
            if planner_v2.captured(run) is not None:
                proof = await _Preparation.open(db, owner, run, token)

        try:
            bound = await self._model_context(
                db, owner, approval, selection, dispatch=True, _planner_prepared=prepared
            )
            fresh = await self.prepare(
                db,
                owner,
                approval.run_id,
                selection,
                **bound,
                **({"_planner_proof": proof} if proof is not None else {}),
            )
            if proof is not None:
                await proof.finish(db, owner)
            return bound, fresh
        finally:
            if proof is not None:
                proof.close()

    async def _existing(
        self,
        db: AsyncSession,
        owner: uuid.UUID,
        permitted: ResearchApproval,
        request_hash: str,
        call_key: str,
    ) -> ResearchCharge | None:
        row = await db.scalar(
            select(ResearchCharge)
            .where(ResearchCharge.run_id == permitted.run_id, ResearchCharge.call_key == call_key)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        if (row.owner_id, row.approval_id, row.request_hash) != (owner, permitted.id, request_hash):
            raise failure("CALL_CONFLICT")
        scope = await security._charge_scope(db, owner, row)
        await security._registered(db, owner, scope, row)
        return row

    async def _unknown(self, db: AsyncSession, owner: uuid.UUID, charge_id: uuid.UUID) -> None:
        await db.rollback()
        await security.settle(db, owner, charge_id, outcome="unknown")
        await db.commit()

    async def _cleanup_unknown(
        self, db: AsyncSession, owner: uuid.UUID, charge_id: uuid.UUID
    ) -> None:
        task = asyncio.create_task(self._unknown(db, owner, charge_id))

        async def terminal_drain():
            try:
                await asyncio.wait_for(task, timeout=5)
            except BaseException:
                # Preserve the existing finite deadline and conservative claim.
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
                except BaseException:
                    pass

        terminal = asyncio.create_task(terminal_drain(), name="research-financial-cleanup")
        while not terminal.done():
            try:
                await asyncio.shield(terminal)
            except asyncio.CancelledError:
                # SDK runner and executor can both cancel the same node. Those
                # requests must not cancel its already-owned financial cleanup.
                # dispatch rethrows the original outcome only after this drain.
                if terminal.cancelled():
                    break
        try:
            terminal.result()
        except BaseException:
            pass  # A failed cleanup retains dispatched and cannot authorize stop.

    async def dispatch(
        self,
        db: AsyncSession,
        owner: uuid.UUID,
        approval_id: uuid.UUID,
        request_hash: str,
        call_key: str,
        selection: DisclosureSelection,
    ) -> dict[str, Any]:
        await acquire_write_fence(db, owner)
        permitted = await security.approval(db, owner, approval_id)
        if permitted.request_hash != request_hash:
            raise failure("APPROVAL_HASH")
        key = private_key(call_key, "call", owner, permitted.run_id)
        existing = await self._existing(db, owner, permitted, request_hash, key)
        if existing is not None:
            from app.research.advisory_receipt import read

            saved_reply = await read(db, owner, permitted, existing)
            return {**charge_summary(existing, reused=True), **(saved_reply or {})}
        _, recorded = await security._validate(db, owner, permitted, request_hash)
        if permitted.state != "approved":
            raise failure("APPROVAL_REQUIRED")
        bound, fresh = await self._prepare_dispatch(db, owner, permitted, selection)
        key = private_key(call_key, "call", owner, permitted.run_id, fresh.credential)
        if not recorded.source_binding_sha256 or not same_specification(
            recorded, fresh.specification
        ):
            raise failure("APPROVAL_STALE")
        self.guard(fresh)
        row = await security.reserve(db, owner, approval_id, request_hash, key)
        charge_id = row.id
        await db.commit()
        claimed = False
        try:
            await acquire_write_fence(db, owner)
            bound, before_claim = await self._prepare_dispatch(db, owner, permitted, selection)
            if not same_specification(recorded, before_claim.specification):
                raise failure("APPROVAL_STALE")
            self.guard(before_claim)
            await security.dispatch(db, owner, charge_id)
            await db.commit()  # Durable once-only claim precedes the send boundary.
            claimed = True
            await acquire_write_fence(db, owner)
            permitted = await security.approval(db, owner, approval_id)
            _, current_spec = await security._validate(db, owner, permitted, request_hash)
            bound, latest = await self._prepare_dispatch(db, owner, permitted, selection)
            if not same_specification(recorded, current_spec) or not same_specification(
                recorded, latest.specification
            ):
                raise failure("APPROVAL_STALE")
            self.guard(latest)
            dispatch_run, _ = await ledger.run(db, permitted.run_id, owner)
            if dispatch_run.kind == "reader":
                from app.research import reader_transport

                await reader_transport.register(db, owner, dispatch_run)
                # The durable dispatched claim precedes this final fresh guard.
                # Holding its write lock across network await would prevent the
                # Reader cancel request from being persisted while in flight.
                # Later cancellation still needs transport drain and an ACK;
                # the Gateway alone records any unknown financial outcome.
                await db.commit()
            elif dispatch_run.kind == "reviewer":
                from app.research import reviewer_transport

                await reviewer_transport.register(db, owner, dispatch_run, charge_id)
                await db.commit()  # Never hold the owner transaction across transport await.
            elif dispatch_run.kind == "draft_advice":
                from app.services.draft_advice_service import require_dispatch_task

                require_dispatch_task(owner, dispatch_run.id)
                await (
                    db.commit()
                )  # Let the owning cancellation request persist while awaiting HTTP.
            started = time.monotonic()
            async with asyncio.timeout(latest.specification.timeout_ms / 1000):
                response = await self.transport.send(
                    latest.specification, latest.wire.body, latest.credential
                )
            elapsed = max(0, int((time.monotonic() - started) * 1000))
            tools = bound.get("model_tools", parse_document(self._tools_json))
            reply: ProviderReply = parse_reply(
                response,
                allowed_tools=frozenset(tool["function"]["name"] for tool in tools),
                owned_secrets=(latest.credential,),
                usage_profile=latest.usage_profile,
                provider=latest.specification.price.provider,
                tool_profile="draft-advice"
                if dispatch_run.kind == "draft_advice"
                else "reviewer"
                if dispatch_run.kind == "reviewer"
                else "planner"
                if (await ledger.run(db, permitted.run_id, owner))[0].kind == "planner"
                else "assistant",
            )
            if reply.usage is None:
                raise failure("USAGE_UNCONFIRMED", 502)
            row = await security.settle(
                db,
                owner,
                charge_id,
                outcome="usage",
                input_tokens=reply.usage.input_tokens,
                output_tokens=reply.usage.output_tokens,
                elapsed_ms=elapsed,
            )
            from app.research.advisory_receipt import record

            await record(
                db,
                owner,
                permitted,
                row,
                reply,
                execution_kind="test_recorder" if self._testing else "provider",
            )
            await db.commit()
            return {
                **charge_summary(row),
                "execution_kind": "test_recorder" if self._testing else "provider",
                "advisory": {"text": reply.text, "tool_calls": reply.tool_calls},
                "run_completed": False,
            }
        except BaseException as exc:
            if claimed:
                await self._cleanup_unknown(db, owner, charge_id)
            else:
                try:
                    await db.rollback()
                    await security.settle(db, owner, charge_id, outcome="not_dispatched")
                    await db.commit()
                except BaseException:
                    pass  # Original reserved row remains conservative, never resends.
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise failure("OUTCOME_UNKNOWN", 503) from None


async def budget_summary(db: AsyncSession, owner: uuid.UUID) -> dict[str, Any]:
    await acquire_write_fence(db, owner)
    await security._verify_accounts(db, owner)
    rows = (
        await db.scalars(
            select(ResearchBudgetAccount)
            .where(ResearchBudgetAccount.owner_id == owner)
            .execution_options(populate_existing=True)
        )
    ).all()
    unknown = (
        await db.scalars(select(ResearchCharge).where(ResearchCharge.owner_id == owner))
    ).all()
    unpriced_accounts = {}
    for call in unknown:
        if security.unpriced(call) and call.state != "released":
            for account_id in parse_document(call.account_ids_json):
                unpriced_accounts[account_id] = unpriced_accounts.get(account_id, 0) + 1
    return {
        "currency": "USD",
        "cost_unit": "0.00000001",
        "accounts": [
            {
                "id": str(row.id),
                "scope": row.scope_kind,
                "scope_id": str(row.scope_id),
                "cost_limit_units": row.cost_limit,
                "reserved_units": None if str(row.id) in unpriced_accounts else row.reserved_cost,
                "consumed_units": None if str(row.id) in unpriced_accounts else row.consumed_cost,
                "unpriced_calls": unpriced_accounts.get(str(row.id), 0),
                "attempts": row.attempts,
                "attempt_limit": row.attempt_limit,
            }
            for row in rows
        ],
    }


gateway = Gateway()


def get_gateway() -> Gateway:
    return gateway
