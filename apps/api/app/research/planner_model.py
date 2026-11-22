"""Planner uses the original real Approval/Charge/AdvisoryReceipt authority."""

import re
import uuid
from contextlib import contextmanager

from app.preview.harness import protocol_messages
from app.research import harness_binding, plan_receipt, planning_lease
from app.research.assistant_model import AssistantModel
from app.research.contracts import State, logical_hash
from app.research.egress import failure
from app.research.model_history import validate
from app.research.planner_protocol import assert_tools
from app.services import research_service as ledger


class PlannerModel(AssistantModel):
    def __init__(self, factory, token, binding, gateway, selection, tools):
        if binding.purpose != "planner" or not isinstance(token, planning_lease.PlanningToken):
            raise failure("MODEL_BINDING_CHANGED")
        self.factory, self.token, self.binding = factory, token, binding
        self.gateway, self.selection = gateway, selection
        self._replay_scope = None
        from app.research import planner_v2
        from app.research.planner_protocol import assert_tools_v2

        self.version = 2 if binding.profile_hash == logical_hash(planner_v2.PROFILE) else 1
        self.tools = assert_tools_v2(tools) if self.version == 2 else assert_tools(tools)
        if selection.purpose != "planning" or selection.scope != "metadata_only" or selection.rows:
            raise failure("PLANNER_DISCLOSURE_REFUSED")

    async def _prepare_new_approval(self, db, run, history, tools):
        from app.research import planner_v2
        from app.research.planner_preparation import _Preparation

        if (
            not isinstance(self.token, planning_lease.PlanningToken)
            or run.state != State.RUNNING.value
            or planner_v2.captured(run) is None
        ):
            return await super()._prepare_new_approval(db, run, history, tools)
        # AssistantModel.call just completed the full current validation. Only
        # this immediate, read-only preflight shares it; propose after the
        # waiting transition still performs its own complete preparation.
        proof = await _Preparation.open(db, self.token.owner_id, run, self.token)
        try:
            prepared = await self.gateway.prepare(
                db,
                self.token.owner_id,
                run.id,
                self.selection,
                conversation=history,
                model_tools=tools,
                _planner_proof=proof,
            )
            await proof.finish(db, self.token.owner_id)
            return prepared
        finally:
            proof.close()

    def bridge(self):
        from app.research.planner_harness import PlannerBridge

        return PlannerBridge(
            model_name="planner-v2" if self.version == 2 else "planner-v1",
            binding_hash=self.binding.binding_hash,
            gateway=self.call,
            authorize=self.authorize,
            verify_reply=self.verify_reply,
            verify_state=self.verify_state,
            receipted=self.receipted,
            replay_order=self._replay_order,
        )

    @contextmanager
    def _replaying(self, reference):
        """Single-use request selector data from the verified server interrupt."""
        from app.research.harness_serde import SafeSerializer

        if self._replay_scope is not None:
            raise failure("MODEL_RESUME_REFUSED")
        if reference is None:
            yield
            return
        if (
            type(reference) is not dict
            or set(reference)
            != {"kind", "approval_id", "request_hash", "call_hash", "binding_hash"}
            or reference["kind"] != "research_approval"
            or reference["binding_hash"] != self.binding.binding_hash
            or any(
                type(reference[field]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", reference[field]) is None
                for field in ("request_hash", "call_hash", "binding_hash")
            )
        ):
            raise failure("MODEL_RESUME_REFUSED")
        try:
            if str(uuid.UUID(reference["approval_id"])) != reference["approval_id"]:
                raise ValueError("Invalid approval identity")
        except (ValueError, TypeError, AttributeError):
            raise failure("MODEL_RESUME_REFUSED") from None
        serde = SafeSerializer()
        self._replay_scope = dict(
            reference=serde.loads_typed(serde.dumps_typed(reference)), consumed=False
        )
        try:
            yield
        finally:
            self._replay_scope = None

    async def _replay_order(self, projection, tools):
        """Read only the specified original request; all authority runs afterwards."""
        scope = self._replay_scope
        if scope is None or scope["consumed"]:
            return None
        scope["consumed"] = True  # Consume before any await, including failed lookups.
        reference = scope["reference"]
        digest = logical_hash(
            dict(binding_hash=self.binding.binding_hash, history=projection, tools=tools)
        )
        if digest == reference["call_hash"]:
            return None
        from app.preview.write_fence import acquire_write_fence
        from app.research import model_request
        from app.research.planner_messages import restore_tool_order

        async with self.factory() as db:
            await acquire_write_fence(db, self.token.owner_id)
            await harness_binding._bound_in_session(db, self.binding, self.token)
            approval, _charge = await self._rows(db, reference["call_hash"])
            if (
                approval is None
                or str(approval.id) != reference["approval_id"]
                or approval.request_hash != reference["request_hash"]
            ):
                raise failure("MODEL_BINDING_CHANGED")
            recorded = await model_request.load(db, self.token.owner_id, approval)
            if (
                recorded is None
                or recorded["binding_hash"] != reference["binding_hash"]
                or recorded["call_hash"] != reference["call_hash"]
                or recorded["selection"] != self.selection.model_dump(mode="json")
                or recorded["tools"] != tools
            ):
                raise failure("MODEL_BINDING_CHANGED")
            # Only detached, validated request data crosses this Session boundary.
            history = recorded["conversation"]
        try:
            order = restore_tool_order(projection, history)
        except (ValueError, TypeError, KeyError):
            raise failure("MODEL_BINDING_CHANGED") from None
        restored = [projection[index] for index in order]
        if (
            logical_hash(
                dict(binding_hash=self.binding.binding_hash, history=restored, tools=tools)
            )
            != reference["call_hash"]
        ):
            raise failure("MODEL_BINDING_CHANGED")
        return order

    async def authorize(self):
        if await self._authorize_live():
            return
        await harness_binding.authorize(self.factory, self.binding, self.token)
        async with self.factory() as db:
            run, _ = await ledger.run(db, self.token.run_id, self.token.owner_id)
            from app.models.research import ResearchAttempt
            from app.research import planning_control

            attempt = await db.get(ResearchAttempt, self.token.attempt_id, populate_existing=True)
            if attempt is None or await planning_control.cancelled(
                db, self.token.owner_id, run, attempt
            ):
                raise failure("MODEL_CANCELLED")
            from app.research import planner_invocation

            invocation = await planner_invocation.load(
                db, self.token.owner_id, run, self.token.attempt_id
            )
            if (
                invocation is None
                or invocation["selection"] != self.selection.model_dump(mode="json")
                or invocation["tools"] != self.tools
            ):
                raise failure("INVOCATION_BINDING_CHANGED")
            if run.state == State.COMPLETED.value:
                return
            from sqlalchemy import select

            from app.models.research_planning import ResearchPlanReceipt

            if (
                await db.scalar(
                    select(ResearchPlanReceipt.id).where(
                        ResearchPlanReceipt.attempt_id == self.token.attempt_id
                    )
                )
                is not None
            ):
                await plan_receipt.prove(db, self.token.owner_id, run, attempt)
                return  # Receipt-only bookkeeping; Gateway/tools independently forbid new work.
            run, _, lease, _ = await planning_lease.validate(db, self.token, running=False)
            if run.state in {"cancelled", "cancel_requested", "interrupted"}:
                raise failure("MODEL_CANCELLED")
            if lease.pause_requested or run.state == "paused":
                raise failure("MODEL_PAUSED")

    async def _authorize_live(self):
        """One new, owned transaction; historical and legacy authority stay original."""
        from sqlalchemy import select

        from app.models.research import ResearchAttempt
        from app.models.research_planning import ResearchPlanReceipt
        from app.preview.write_fence import acquire_write_fence
        from app.research import planner_invocation, planner_v2, planning_control
        from app.research.planner_preparation import _Preparation

        async with self.factory() as db:
            await acquire_write_fence(db, self.token.owner_id)
            run, _ = await harness_binding._bound_in_session(db, self.binding, self.token)
            if run.state not in {
                State.RUNNING.value,
                State.WAITING_APPROVAL.value,
            }:
                return False
            receipt = select(ResearchPlanReceipt.id).where(
                ResearchPlanReceipt.attempt_id == self.token.attempt_id
            )
            if await db.scalar(receipt) is not None:
                return False  # Closing this Session precedes the original historical path.
            if planner_v2.captured(run) is None:
                return False
            current, _, lease, _ = await planning_lease.validate(db, self.token, running=False)
            proof = await _Preparation.open(db, self.token.owner_id, current, self.token)
            try:
                proof.consume(db, self.token.owner_id, current)
                attempt = await db.get(
                    ResearchAttempt, self.token.attempt_id, populate_existing=True
                )
                if attempt is None or await planning_control.cancelled(
                    db, self.token.owner_id, current, attempt
                ):
                    raise failure("MODEL_CANCELLED")
                invocation = await planner_invocation.load(
                    db, self.token.owner_id, current, self.token.attempt_id
                )
                if (
                    invocation is None
                    or invocation["selection"] != self.selection.model_dump(mode="json")
                    or invocation["tools"] != self.tools
                ):
                    raise failure("INVOCATION_BINDING_CHANGED")
                if await db.scalar(receipt) is not None:
                    raise failure("MODEL_BINDING_CHANGED")
                if current.state in {"cancelled", "cancel_requested", "interrupted"}:
                    raise failure("MODEL_CANCELLED")
                if lease.pause_requested or current.state == "paused":
                    raise failure("MODEL_PAUSED")
                await proof.finish(db, self.token.owner_id)
                return True
            finally:
                proof.close()

    async def receipted(self):
        await harness_binding.authorize(self.factory, self.binding, self.token)
        async with self.factory() as db:
            from sqlalchemy import select

            from app.models.research import ResearchAttempt
            from app.models.research_planning import ResearchPlanReceipt

            row = await db.scalar(
                select(ResearchPlanReceipt.id).where(
                    ResearchPlanReceipt.attempt_id == self.token.attempt_id
                )
            )
            if row is None:
                return False
            run, _ = await ledger.run(db, self.token.run_id, self.token.owner_id)
            await plan_receipt.prove(
                db, self.token.owner_id, run, await db.get(ResearchAttempt, self.token.attempt_id)
            )
            return True

    def _key(self, digest):
        return "planner-model:" + digest

    def _reference(self, messages, tools):
        from app.research.planner_protocol import assert_tools_v2

        if (assert_tools_v2(tools) if self.version == 2 else assert_tools(tools)) != self.tools:
            raise failure("MODEL_BINDING_CHANGED")
        projection = protocol_messages(messages)
        call_hash = logical_hash(
            dict(binding_hash=self.binding.binding_hash, history=projection, tools=tools)
        )
        history = validate(
            [item for item in projection if item["role"] != "system"], tool_profile="planner"
        )
        if any(
            item["role"] == "user" and item["content"] != self.selection.question
            for item in history
        ):
            raise failure("MODEL_BINDING_CHANGED")
        return call_hash, history
