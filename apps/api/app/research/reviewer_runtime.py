"""Independent review callbacks tied to original replies and durable tool records."""

import uuid

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchAdvisoryReceipt, ResearchHarnessThread
from app.models.research_security import ResearchCharge
from app.preview.harness import planning_todos, protocol_messages
from app.research import (
    advisory_receipt,
    harness_binding,
    historical_reply,
    reviewer_admission,
    reviewer_contracts,
    reviewer_lease,
    reviewer_receipt,
    reviewer_sources,
    reviewer_tools,
)
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import failure
from app.research.model_history import validate
from app.research.reviewer_contracts import ReviewReport
from app.services import research_gateway_service as gateways
from app.services import research_security_service as security
from app.services import research_service as ledger

EVENT = "reviewer.tool_recorded"
NAMES = frozenset({"read_review_evidence", "submit_review"})
TODO_RESULT = '{"state":"reviewer_state_updated"}'


def normalize(name, args):
    if name == "read_review_evidence" and args == {}:
        return {}
    if name == "submit_review" and isinstance(args, dict) and set(args) == {"report"}:
        return {"report": ReviewReport.model_validate(args["report"]).model_dump(mode="json")}
    raise failure("REVIEWER_TOOL_DENIED")


def _history(messages, tool_call_id):
    matches = [
        (i, m)
        for i, m in enumerate(messages)
        if isinstance(m, AIMessage) and any(c.get("id") == tool_call_id for c in m.tool_calls)
    ]
    if not matches:
        raise failure("REVIEWER_TOOL_UNPROVEN")
    index, message = matches[-1]
    calls = [c for c in message.tool_calls if c.get("id") == tool_call_id]
    if len(calls) != 1:
        raise failure("REVIEWER_TOOL_UNPROVEN")
    history = validate(
        [x for x in protocol_messages(messages[:index]) if x["role"] != "system"],
        tool_profile="reviewer",
    )
    return history, message, calls[0]


async def resolve(db, token, binding, name, args, tool_call_id, messages):
    if type(token) is not reviewer_contracts.ReviewerToken:
        raise failure("REVIEWER_TOOL_UNPROVEN")
    history, actual_message, call = _history(messages, tool_call_id)
    if call["name"] != name or normalize(name, call["args"]) != args:
        raise failure("REVIEWER_TOOL_UNPROVEN")
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    if run.kind != reviewer_contracts.KIND:
        raise failure("REVIEWER_TOOL_UNPROVEN")
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
    matches = []
    for event in events:
        request = parse_document(event.payload_json)
        if (
            request.get("binding_hash"),
            request.get("attempt_id"),
            request.get("conversation"),
        ) != (binding.binding_hash, str(token.attempt_id), history):
            continue
        key = gateways.private_key(
            "reviewer-model:" + request["call_hash"], "call", token.owner_id, run.id
        )
        charge = await db.scalar(
            select(ResearchCharge).where(
                ResearchCharge.run_id == run.id, ResearchCharge.call_key == key
            )
        )
        reply = (
            None
            if charge is None
            else await db.get(ResearchAdvisoryReceipt, charge.id, populate_existing=True)
        )
        if reply is None:
            raise failure("REVIEWER_TOOL_UNPROVEN")
        proof = await historical_reply.prove(
            db,
            token.owner_id,
            run.id,
            token.attempt_id,
            charge.id,
            reply_hash=reply.document_hash,
            binding_hash=binding.binding_hash,
            call_hash=request["call_hash"],
        )
        original = parse_document(proof.document_json)["advisory"]
        if (
            actual_message.content != (original["text"] if original["text"] is not None else "")
            or [{k: c[k] for k in ("id", "name", "args")} for c in actual_message.tool_calls]
            != original["tool_calls"]
        ):
            raise failure("REVIEWER_TOOL_UNPROVEN")
        matches.append(
            dict(
                charge_id=str(charge.id),
                reply_hash=reply.document_hash,
                call_hash=request["call_hash"],
                tool_call_id=tool_call_id,
            )
        )
    if len(matches) != 1:
        raise failure("REVIEWER_TOOL_UNPROVEN")
    return matches[0]


def identity(binding, reference):
    return uuid.uuid5(
        binding.id,
        logical_hash(
            dict(
                charge_id=reference["charge_id"],
                tool_call_id=reference["tool_call_id"],
            )
        ),
    )


def evidence_result(source):
    """The actual approved wire already contains every original aggregate fact."""
    context = source["review_context"]
    return dict(
        version="reviewer-evidence-reference-v1",
        input_hash=source["review_input_hash"],
        captured_protocol=dict(
            version_id=context["protocol_version_id"],
            hash=context["protocol_hash"],
            generation=context["generation"],
        ),
        sources=[
            dict(artifact_id=s["artifact_id"], projection_hash=s["result_facts"]["projection_hash"])
            for s in context["sources"]
        ],
        computed=False,
        evidence_location="approved_review_context",
    )


def review_result(receipt):
    return dict(
        kind="reviewer_review_receipt",
        review_id=str(receipt.id),
        review_hash=receipt.document_hash,
        computed=False,
        expert_review=False,
        authorizes_execution=False,
    )


async def prove_event(db, owner, event):
    try:
        return await _prove_event(db, owner, event)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        raise failure("REVIEWER_TOOL_UNPROVEN") from None


async def _prove_event(db, owner, event):
    value = parse_document(event.payload_json)
    if (
        set(value)
        != {
            "version",
            "run_id",
            "attempt_id",
            "binding_id",
            "binding_hash",
            "name",
            "args",
            "result",
            "source",
            "charge_id",
            "reply_hash",
            "call_hash",
            "tool_call_id",
        }
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["name"] not in NAMES
        or event.event_type != EVENT
    ):
        raise failure("REVIEWER_TOOL_UNPROVEN")
    run, scope = await ledger.run(db, uuid.UUID(value["run_id"]), owner)
    attempt = await db.get(ResearchAttempt, uuid.UUID(value["attempt_id"]), populate_existing=True)
    thread = await db.get(
        ResearchHarnessThread, uuid.UUID(value["binding_id"]), populate_existing=True
    )
    if (
        run.kind != reviewer_contracts.KIND
        or attempt is None
        or attempt.run_id != run.id
        or thread is None
        or thread.owner_id != owner
        or thread.run_id != run.id
        or thread.attempt_id != attempt.id
        or thread.purpose != "assistant"
        or thread.input_hash != run.input_hash
        or thread.profile_hash != logical_hash(reviewer_contracts.PROFILE)
        or thread.binding_hash != value["binding_hash"]
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or uuid.UUID(thread.thread_id).hex != thread.thread_id
        or event.actor_id != owner
        or event.workspace_id != scope.id
        or event.entity_kind != "reviewer_tool"
        or event.entity_id != identity(thread, value)
        or event.logical_hash != logical_hash(value)
        or normalize(value["name"], value["args"]) != value["args"]
    ):
        raise failure("REVIEWER_TOOL_UNPROVEN")
    admission = await reviewer_admission.load(db, owner, run, attempt)
    proof = await historical_reply.prove(
        db,
        owner,
        run.id,
        attempt.id,
        uuid.UUID(value["charge_id"]),
        reply_hash=value["reply_hash"],
        binding_hash=thread.binding_hash,
        call_hash=value["call_hash"],
    )
    request = parse_document(proof.request_json)
    calls = [
        c
        for c in parse_document(proof.document_json)["advisory"]["tool_calls"]
        if c.get("id") == value["tool_call_id"]
    ]
    saved = reviewer_sources.captured(run)
    if (
        admission is None
        or request["version"] != 4
        or request["selection"] != admission["selection"]
        or request["tools"] != admission["tools"]
        or request["profile_hash"] != admission["profile_hash"]
        or request["source_proof_hash"] != admission["source_proof_hash"]
        or request["review_context"] != reviewer_sources.disclosure(saved)
        or len(calls) != 1
        or calls[0]["name"] != value["name"]
        or normalize(value["name"], calls[0]["args"]) != value["args"]
        or proof.resumed_sequence >= event.sequence
    ):
        raise failure("REVIEWER_TOOL_UNPROVEN")
    if value["name"] == "submit_review":
        receipt = await reviewer_receipt.prove(db, owner, run, attempt)
        report = parse_document(receipt.document_json)
        if (
            any(
                report[k] != value[k]
                for k in ("charge_id", "reply_hash", "call_hash", "tool_call_id", "binding_hash")
            )
            or receipt.sequence >= event.sequence
            or value["source"] is not None
            or value["result"] != review_result(receipt)
            or report["report"] != value["args"]["report"]
        ):
            raise failure("REVIEWER_TOOL_UNPROVEN")
    elif (
        value["source"] != saved
        or logical_hash(saved) != admission["source_proof_hash"]
        or value["result"] != evidence_result(saved)
    ):
        raise failure("REVIEWER_TOOL_UNPROVEN")
    if len(canonical_bytes(value["result"])) > 8192:
        raise failure("REVIEWER_TOOL_UNPROVEN")
    return value


class ReviewerRuntime:
    def __init__(self, factory, token, binding):
        if type(token) is not reviewer_contracts.ReviewerToken or binding.purpose != "assistant":
            raise failure("MODEL_BINDING_CHANGED")
        self.factory, self.token, self.binding = factory, token, binding

    def tools(self):
        return reviewer_tools.build_tools(self.call, stateful=True)

    async def call(self, name, args, tool_call_id, messages):
        from app.research import reviewer_sdk_tasks

        async with reviewer_sdk_tasks.callback(self.token, self.binding.binding_hash):
            return await self._call(name, args, tool_call_id, messages)

    async def _call(self, name, args, tool_call_id, messages):
        args = normalize(name, args)
        await harness_binding.authorize(self.factory, self.binding, self.token)
        async with self.factory() as db:
            run, scope, lease, _ = await reviewer_lease.validate(
                db, self.token, current_source=False
            )
            from app.research import reviewer_control

            attempt = await db.get(ResearchAttempt, self.token.attempt_id, populate_existing=True)
            if await reviewer_control.cancelled(db, self.token.owner_id, run, attempt):
                raise failure("MODEL_CANCELLED")
            if lease.pause_requested:
                raise failure("MODEL_PAUSED")
            reference = await resolve(
                db, self.token, self.binding, name, args, tool_call_id, messages
            )
            key = identity(self.binding, reference)
            old = (
                await db.scalars(
                    select(ResearchEvent)
                    .where(
                        ResearchEvent.workspace_id == scope.id,
                        ResearchEvent.entity_id == key,
                        ResearchEvent.event_type == EVENT,
                    )
                    .limit(2)
                )
            ).all()
            if len(old) > 1:
                raise failure("REVIEWER_TOOL_UNPROVEN")
            if old:
                value = await prove_event(db, self.token.owner_id, old[0])
                if value["name"] != name or value["args"] != args:
                    raise failure("REVIEWER_TOOL_UNPROVEN")
                return value["result"]
            run, scope, _, _ = await reviewer_lease.validate(db, self.token)
            charge = await security.charge(
                db, self.token.owner_id, uuid.UUID(reference["charge_id"])
            )
            approval = await security.approval(db, self.token.owner_id, charge.approval_id)
            if await advisory_receipt.read(db, self.token.owner_id, approval, charge) is None:
                raise failure("REVIEWER_TOOL_UNPROVEN")
            source = None
            if name == "submit_review":
                receipt = await reviewer_receipt.record(
                    db,
                    self.token,
                    self.binding,
                    charge.id,
                    reference["reply_hash"],
                    reference["call_hash"],
                    tool_call_id,
                    ReviewReport.model_validate(args["report"]),
                )
                result = review_result(receipt)
            else:
                source = await reviewer_lease.source(db, self.token.owner_id, run)
                result = evidence_result(source)
            if len(canonical_bytes(result)) > 8192:
                raise failure("REVIEWER_TOOL_LIMIT")
            value = dict(
                version=1,
                run_id=str(run.id),
                attempt_id=str(self.token.attempt_id),
                binding_id=str(self.binding.id),
                binding_hash=self.binding.binding_hash,
                name=name,
                args=args,
                result=result,
                source=source,
                **reference,
            )
            await ledger._append(db, scope, self.token.owner_id, "reviewer_tool", key, EVENT, value)
            await db.commit()
            return result


async def verify_messages(db, token, binding, messages):
    """Cached tool results and todos require their actual same-thread reply origins."""
    from app.research import reviewer_history

    run, _ = await ledger.run(db, token.run_id, token.owner_id)
    await reviewer_history.prove(
        db,
        token.owner_id,
        run,
        [m for m in protocol_messages(messages) if m["role"] != "system"],
        thread_id=binding.id,
    )
    for index, item in enumerate(messages):
        if not isinstance(item, ToolMessage):
            continue
        _, _, call = _history(messages[:index], item.tool_call_id)
        if item.status == "error" or not isinstance(item.content, str):
            raise failure("REVIEWER_TOOL_UNPROVEN")
        if call["name"] == "write_todos":
            planning_todos(call["args"])
            if item.content != TODO_RESULT:
                raise failure("REVIEWER_TOOL_UNPROVEN")
            continue
        reference = await resolve(
            db,
            token,
            binding,
            call["name"],
            normalize(call["name"], call["args"]),
            item.tool_call_id,
            messages[:index],
        )
        rows = (
            await db.scalars(
                select(ResearchEvent)
                .where(
                    ResearchEvent.entity_id == identity(binding, reference),
                    ResearchEvent.event_type == EVENT,
                )
                .limit(2)
            )
        ).all()
        if len(rows) != 1:
            raise failure("REVIEWER_TOOL_UNPROVEN")
        value = await prove_event(db, token.owner_id, rows[0])
        if parse_document(item.content) != value["result"]:
            raise failure("REVIEWER_TOOL_UNPROVEN")
