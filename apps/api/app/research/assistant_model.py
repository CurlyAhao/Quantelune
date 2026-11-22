"""Actual approval/financial reply authority behind the formal SDK bridge."""

import uuid

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from sqlalchemy import select

from app.models.research import ResearchEvent
from app.models.research_security import ResearchApproval, ResearchCharge
from app.preview.harness import assert_tools, protocol_messages
from app.research import advisory_receipt, execution_lease, harness_binding, model_request
from app.research.contracts import State, logical_hash, parse_document
from app.research.egress import failure
from app.research.formal_harness import FormalBridge
from app.research.model_history import validate
from app.services import research_gateway_service as gateways
from app.services import research_security_service as security
from app.services import research_service as ledger


def message(actual):
    advisory = actual.get("advisory")
    if not isinstance(advisory, dict) or actual.get("run_completed") is not False:
        raise failure("REPLY_UNPROVEN")
    # A filtered tool-only response truthfully has no text. The SDK represents
    # that absence as an empty content string; no narrative is synthesized.
    return AIMessage(
        content=advisory["text"] if advisory["text"] is not None else "",
        tool_calls=advisory["tool_calls"],
    )


class AssistantModel:
    """Runtime-only; no Session, Gateway or credentials are checkpoint values."""

    def __init__(self, factory, token, binding, gateway, selection):
        if binding.purpose != "assistant":
            raise failure("MODEL_BINDING_CHANGED")
        self.factory, self.token, self.binding = factory, token, binding
        self.gateway, self.selection = gateway, selection

    async def authorize(self):
        await harness_binding.authorize(self.factory, self.binding, self.token)
        async with self.factory() as db:
            run, _ = await ledger.run(db, self.token.run_id, self.token.owner_id)
            if run.state in {State.COMPLETED.value, State.FAILED.value}:
                return  # authorize above already proved the real publication.
            run, _, lease, _ = await execution_lease.validate(db, self.token, running=False)
            from app.models.research import ResearchAttempt
            from app.research import assistant_invocation

            attempt = await db.get(ResearchAttempt, self.token.attempt_id, populate_existing=True)
            invocation = await assistant_invocation.load(db, self.token.owner_id, run, attempt)
            if invocation is not None:
                await assistant_invocation.verify_current(
                    db, self.token.owner_id, run, attempt, invocation
                )
            if run.state in {"cancelled", "cancel_requested", "interrupted"}:
                raise failure("MODEL_CANCELLED")
            if lease.pause_requested:
                raise failure("MODEL_PAUSED")

    def bridge(self):
        return FormalBridge(
            binding_hash=self.binding.binding_hash,
            gateway=self.call,
            authorize=self.authorize,
            verify_reply=self.verify_reply,
            verify_state=self.verify_state,
            published=self.published,
        )

    async def published(self):
        from app.research import publication

        await harness_binding.authorize(self.factory, self.binding, self.token)
        return await publication._confirmed(self.factory, self.token) is not None

    def _reference(self, messages, tools):
        assert_tools(tools, sdk=True)
        projection = protocol_messages(messages)
        call_hash = logical_hash(
            dict(binding_hash=self.binding.binding_hash, history=projection, tools=tools)
        )
        # The provider uses the server's fixed SYSTEM_TEXT. SDK-only scaffolding
        # is not a second system authority and is never sent as source material.
        history = validate([item for item in projection if item["role"] != "system"])
        if any(
            item["role"] == "user" and item["content"] != self.selection.question
            for item in history
        ):
            raise failure("MODEL_BINDING_CHANGED")
        return call_hash, history

    def _key(self, digest):
        return "h02-model:" + digest

    async def _rows(self, db, call_hash):
        owner, run_id = self.token.owner_id, self.token.run_id
        key = gateways.private_key(self._key(call_hash), "proposal", owner, run_id)
        approval = await db.scalar(
            select(ResearchApproval)
            .where(ResearchApproval.run_id == run_id, ResearchApproval.proposal_key == key)
            .execution_options(populate_existing=True)
        )
        charge = None
        if approval is not None:
            approval = await security.approval(db, owner, approval.id)
            recorded = await model_request.load(db, owner, approval)
            if (
                recorded is None
                or recorded["binding_hash"] != self.binding.binding_hash
                or recorded["call_hash"] != call_hash
            ):
                raise failure("MODEL_BINDING_CHANGED")
            call_key = gateways.private_key(self._key(call_hash), "call", owner, run_id)
            charge = await db.scalar(
                select(ResearchCharge)
                .where(ResearchCharge.run_id == run_id, ResearchCharge.call_key == call_key)
                .execution_options(populate_existing=True)
            )
        return approval, charge

    async def _prepare_new_approval(self, db, run, history, tools):
        """Original preflight after full validation, before any waiting write."""
        await harness_binding._source(db, self.token, run)
        if run.state != State.RUNNING.value:
            raise failure("MODEL_BINDING_CHANGED")
        return await self.gateway.prepare(
            db,
            self.token.owner_id,
            run.id,
            self.selection,
            conversation=history,
            model_tools=tools,
        )

    async def call(self, messages, tools):
        await self.authorize()
        call_hash, history = self._reference(messages, tools)
        owner = self.token.owner_id
        async with self.factory() as db:
            await execution_lease._boundary(db, owner)
            approval, charge = await self._rows(db, call_hash)
            if charge is not None:
                # Dispatch/unknown or settled-without-reply is never replayable.
                actual = await advisory_receipt.resume(db, owner, approval, charge)
                await db.commit()
                return message(actual)
            run, _, _, _ = await harness_binding._validate(db, self.token, running=False)
            if approval is None:
                prepared = await self._prepare_new_approval(db, run, history, tools)
                self.gateway.guard(prepared)  # Unqualified production price never starts a wait.
                await ledger.transition(db, owner, run.id, run.revision, State.WAITING_APPROVAL)
                offered = await self.gateway.propose(
                    db,
                    owner,
                    run.id,
                    self._key(call_hash),
                    self.selection,
                    conversation=history,
                    model_binding=self.binding,
                    model_call_hash=call_hash,
                    model_tools=tools,
                )
                approval = await security.approval(db, owner, uuid.UUID(offered["id"]))
            else:
                from app.research import planner_v2
                from app.research.planning_lease import PlanningToken

                # This Session just completed full source and end-clock checks.
                # Only the immediate, read-only v2 branch can omit the repeat.
                if not (
                    type(self.token) is PlanningToken
                    and self.binding.purpose == "planner"
                    and self.binding.profile_hash == logical_hash(planner_v2.PROFILE)
                    and planner_v2.captured(run) is not None
                ):
                    await harness_binding._source(db, self.token, run)
            safe_reference = dict(
                kind="research_approval",
                approval_id=str(approval.id),
                request_hash=approval.request_hash,
                call_hash=call_hash,
                binding_hash=self.binding.binding_hash,
            )
            await db.commit()  # Actual waiting r+1/proposal exists before SDK interrupt.
        resumed = interrupt(safe_reference)
        if resumed != safe_reference:
            raise failure("MODEL_RESUME_REFUSED")
        await self.authorize()
        async with self.factory() as db:
            approval, charge = await self._rows(db, call_hash)
            if charge is not None:
                actual = await advisory_receipt.resume(db, owner, approval, charge)
                await db.commit()
                return message(actual)
            if approval is None or approval.state != "approved":
                raise failure("APPROVAL_REQUIRED")
            result = await self.gateway.dispatch(
                db, owner, approval.id, approval.request_hash, self._key(call_hash), self.selection
            )
            charge = await security.charge(db, owner, uuid.UUID(result["charge_id"]))
            actual = await advisory_receipt.resume(db, owner, approval, charge)
            await db.commit()  # Receipt proof precedes trusted running r+2.
            return message(actual)

    async def verify_reply(self, call_hash, reply):
        await self.authorize()
        async with self.factory() as db:
            approval, charge = await self._rows(db, call_hash)
            if approval is None or charge is None:
                raise failure("REPLY_UNPROVEN")
            actual = await advisory_receipt.read(db, self.token.owner_id, approval, charge)
            if actual is None:
                raise failure("REPLY_UNRECOVERABLE")
            expected = message(actual)
            if reply.content != expected.content or reply.tool_calls != expected.tool_calls:
                raise failure("REPLY_UNPROVEN")

    async def verify_state(self, result):
        """A fully cached graph still requires actual reply authority on every read."""
        await self.authorize()
        if not isinstance(result, dict) or not isinstance(result.get("messages"), list):
            raise failure("REPLY_UNPROVEN")
        replies = [item for item in result["messages"] if isinstance(item, AIMessage)]
        actual_replies = []
        async with self.factory() as db:
            _, scope = await ledger.run(db, self.token.run_id, self.token.owner_id)
            events = (
                await db.scalars(
                    select(ResearchEvent)
                    .where(
                        ResearchEvent.workspace_id == scope.id,
                        ResearchEvent.event_type == "harness.model_request",
                    )
                    .order_by(ResearchEvent.sequence)
                    .limit(10_001)
                )
            ).all()
            if len(events) > 10_000:
                raise failure("HISTORY_LIMIT")
            for event in events:
                document = parse_document(event.payload_json)
                if document.get("binding_hash") != self.binding.binding_hash:
                    continue
                approval, charge = await self._rows(db, document["call_hash"])
                if charge is None:
                    continue
                actual = await advisory_receipt.read(db, self.token.owner_id, approval, charge)
                if actual is None:
                    raise failure("REPLY_UNRECOVERABLE")
                actual_replies.append(message(actual))
        if len(replies) > len(actual_replies) or (
            not result.get("__interrupt__") and len(replies) != len(actual_replies)
        ):
            raise failure("REPLY_UNPROVEN")
        for cached, actual in zip(replies, actual_replies, strict=False):
            if cached.content != actual.content or cached.tool_calls != actual.tool_calls:
                raise failure("REPLY_UNPROVEN")
