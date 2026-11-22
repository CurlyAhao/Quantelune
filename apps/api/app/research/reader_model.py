"""Reader model bridge: live calls stay fresh; committed answers permit history only."""

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchAdvisoryReceipt
from app.preview.harness import protocol_messages
from app.research import (
    harness_binding,
    historical_reply,
    reader_admission,
    reader_answer,
    reader_contracts,
    reader_lease,
    reader_tools,
)
from app.research.assistant_model import AssistantModel, message
from app.research.contracts import State, logical_hash, parse_document
from app.research.egress import failure
from app.research.model_history import validate
from app.research.reader_contracts import ReaderToken
from app.research.reader_contracts import selection as validate_selection
from app.services import research_service as ledger


class ReaderModel(AssistantModel):
    def __init__(self, factory, token, binding, gateway, selection, tools):
        if type(token) is not ReaderToken or binding.purpose != "assistant":
            raise failure("MODEL_BINDING_CHANGED")
        super().__init__(factory, token, binding, gateway, validate_selection(selection))
        self.projection_version = reader_contracts.view_from_profile(binding.profile_hash)
        self.tools = reader_tools.assert_tools(tools, projection_version=self.projection_version)

    def bridge(self):
        from app.research.reader_harness import ReaderBridge

        return ReaderBridge(
            model_name="artifact-reader-v1"
            if self.projection_version == 1
            else "artifact-reader-v2",
            binding_hash=self.binding.binding_hash,
            gateway=self.call,
            authorize=self.authorize,
            verify_reply=self.verify_reply,
            verify_state=self.verify_state,
            receipted=self.receipted,
        )

    async def authorize(self):
        await harness_binding.authorize(self.factory, self.binding, self.token)
        async with self.factory() as db:
            run, _ = await ledger.run(db, self.token.run_id, self.token.owner_id)
            attempt = await db.get(ResearchAttempt, self.token.attempt_id, populate_existing=True)
            admission = await reader_admission.load(db, self.token.owner_id, run, attempt)
            if (
                admission is None
                or admission["selection"] != self.selection.model_dump(mode="json")
                or admission["tools"] != self.tools
            ):
                raise failure("INVOCATION_BINDING_CHANGED")
            if run.state == State.COMPLETED.value:
                return
            if run.state not in {"running", "waiting_approval", "paused"}:
                raise failure("MODEL_CANCELLED")
            _, _, lease, _ = await reader_lease.validate(
                db, self.token, running=False, current_source=False
            )
            from app.research import reader_control

            if await reader_control.cancelled(db, self.token.owner_id, run, attempt):
                raise failure("MODEL_CANCELLED")
            if lease.pause_requested or run.state == "paused":
                raise failure("MODEL_PAUSED")

    async def receipted(self):
        await self.authorize()
        async with self.factory() as db:
            if await reader_lease.answer_event(db, self.token.attempt_id) is None:
                return False
            run, _ = await ledger.run(db, self.token.run_id, self.token.owner_id)
            await reader_answer.prove(
                db, self.token.owner_id, run, await db.get(ResearchAttempt, self.token.attempt_id)
            )
            return True

    def _key(self, digest):
        return "reader-model:" + digest

    def _reference(self, messages, tools):
        if (
            reader_tools.assert_tools(tools, projection_version=self.projection_version)
            != self.tools
        ):
            raise failure("MODEL_BINDING_CHANGED")
        projection = protocol_messages(messages)
        call_hash = logical_hash(
            dict(binding_hash=self.binding.binding_hash, history=projection, tools=tools)
        )
        history = validate(
            [item for item in projection if item["role"] != "system"],
            tool_profile="reader-v2" if self.projection_version == 2 else "assistant",
        )
        if any(
            item["role"] == "user" and item["content"] != self.selection.question
            for item in history
        ):
            raise failure("MODEL_BINDING_CHANGED")
        return call_hash, history

    async def call(self, messages, tools):
        if await self.receipted():
            raise failure("READER_RECEIPT_ONLY")
        return await super().call(messages, tools)

    async def _actual(self, db, call_hash):
        approval, charge = await self._rows(db, call_hash)
        if approval is None or charge is None:
            raise failure("REPLY_UNPROVEN")
        if await reader_lease.answer_event(db, self.token.attempt_id) is None:
            from app.research.advisory_receipt import read

            actual = await read(db, self.token.owner_id, approval, charge)
            if actual is None:
                raise failure("REPLY_UNRECOVERABLE")
            return actual
        run, _ = await ledger.run(db, self.token.run_id, self.token.owner_id)
        await reader_answer.prove(
            db, self.token.owner_id, run, await db.get(ResearchAttempt, self.token.attempt_id)
        )
        row = await db.get(ResearchAdvisoryReceipt, charge.id, populate_existing=True)
        if row is None:
            raise failure("REPLY_UNRECOVERABLE")
        proof = await historical_reply.prove(
            db,
            self.token.owner_id,
            run.id,
            self.token.attempt_id,
            charge.id,
            reply_hash=row.document_hash,
            binding_hash=self.binding.binding_hash,
            call_hash=call_hash,
        )
        document = parse_document(proof.document_json)
        return dict(
            advisory=document["advisory"],
            execution_kind=document["execution_kind"],
            reply_receipt_hash=row.document_hash,
            run_completed=False,
        )

    async def verify_reply(self, call_hash, reply):
        await self.authorize()
        async with self.factory() as db:
            expected = message(await self._actual(db, call_hash))
            if reply.content != expected.content or reply.tool_calls != expected.tool_calls:
                raise failure("REPLY_UNPROVEN")

    async def verify_state(self, result):
        from langchain_core.messages import AIMessage

        from app.research import reader_runtime

        await self.authorize()
        if not isinstance(result, dict) or not isinstance(result.get("messages"), list):
            raise failure("REPLY_UNPROVEN")
        replies = [m for m in result["messages"] if isinstance(m, AIMessage)]
        async with self.factory() as db:
            run, scope = await ledger.run(db, self.token.run_id, self.token.owner_id)
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
            expected = []
            for event in events:
                value = parse_document(event.payload_json)
                if value.get("binding_hash") != self.binding.binding_hash:
                    continue
                _, charge = await self._rows(db, value["call_hash"])
                if charge is not None:
                    expected.append(message(await self._actual(db, value["call_hash"])))
            if len(replies) > len(expected) or (
                not result.get("__interrupt__") and len(replies) != len(expected)
            ):
                raise failure("REPLY_UNPROVEN")
            for cached, actual in zip(replies, expected, strict=False):
                if cached.content != actual.content or cached.tool_calls != actual.tool_calls:
                    raise failure("REPLY_UNPROVEN")
            await reader_runtime.verify_messages(db, self.token, self.binding, result["messages"])
