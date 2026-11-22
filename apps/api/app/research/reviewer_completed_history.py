"""Completed saved-message closure, inside the caller's existing owner transaction."""

from dataclasses import dataclass

from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchAdvisoryReceipt, ResearchHarnessThread
from app.research import harness_binding, reviewer_history, reviewer_saved_messages
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.harness_serde import SafeSerializer
from app.research.reviewer_contracts import PROFILE
from app.research.reviewer_snapshot import Observation
from app.services import research_service as ledger


@dataclass(frozen=True)
class CompletedHistory:
    history: reviewer_history.ReviewerHistoryProof
    closure_json: str
    closure_hash: str


def _refuse():
    return ledger.error("REVIEWER_COMPLETED_HISTORY_UNPROVEN")


def _pairs(origins, kind):
    selected = [
        (item["source_id"], item["origin_hash"]) for item in origins if item["kind"] == kind
    ]
    if len(set(selected)) != len(selected) or len({item[0] for item in selected}) != len(selected):
        raise _refuse()
    return set(selected)


def _envelope(event, owner, scope):
    if (
        event.actor_id != owner
        or event.workspace_id != scope.id
        or event.logical_hash != logical_hash(parse_document(event.payload_json))
    ):
        raise _refuse()


async def prove_completed_messages(
    db, owner, run, attempt, binding, observation, *, before_sequence, completion_sequence
):
    """No commit, new transaction, live token, model, saver or recovery operation.

    Caller independently proves completion/release/epoch and supplies its actual
    completed transition sequence. This helper proves message facts and closure.
    """
    transaction = db.sync_session.get_transaction()
    if (
        transaction is None
        or transaction.parent is not None
        or db.sync_session.get_nested_transaction() is not None
        or not transaction.is_active
        or type(observation) is not Observation
    ):
        raise _refuse()
    observation = Observation.model_validate(observation.model_dump(mode="json"))
    actual, scope = await ledger.run(db, run.id, owner)
    current = await db.get(ResearchAttempt, attempt.id, populate_existing=True)
    thread = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
    entry = observation.writer.entry
    if (
        type(before_sequence) is not int
        or type(completion_sequence) is not int
        or not 0 < completion_sequence < before_sequence <= scope.event_sequence + 1
        or actual.kind != "reviewer"
        or actual.state != "completed"
        or actual.input_hash != run.input_hash
        or current is None
        or current.run_id != actual.id
        or current.number != actual.attempt_count
        or current.state != "completed"
        or thread is None
        or thread.owner_id != owner
        or thread.run_id != actual.id
        or thread.attempt_id != current.id
        or thread.purpose != "assistant"
        or thread.input_hash != actual.input_hash
        or thread.profile_hash != logical_hash(PROFILE)
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or any(
            getattr(thread, name) != getattr(binding, name) for name in binding.__dataclass_fields__
        )
        or (
            entry.owner_id,
            entry.run_id,
            entry.attempt_id,
            entry.input_hash,
            entry.binding_id,
            entry.binding_hash,
            entry.thread_id,
        )
        != (
            str(owner),
            str(actual.id),
            str(current.id),
            actual.input_hash,
            str(thread.id),
            thread.binding_hash,
            thread.thread_id,
        )
    ):
        raise _refuse()
    transition = await db.scalar(
        select(ResearchEvent).where(
            ResearchEvent.workspace_id == scope.id, ResearchEvent.sequence == completion_sequence
        )
    )
    if transition is None:
        raise _refuse()
    _envelope(transition, owner, scope)
    changed = parse_document(transition.payload_json)
    if (
        transition.event_type != "run.state_changed"
        or transition.entity_kind != "run"
        or transition.entity_id != actual.id
        or changed.get("state") != "completed"
        or changed.get("attempt") != current.number
        or changed.get("revision") != actual.revision
        or changed.get("completion_hash") != actual.completion_hash
    ):
        raise _refuse()
    raw, wire = reviewer_saved_messages.checked(
        observation.raw_messages_json, observation.raw_messages_hash
    )
    proof = await reviewer_history.prove(db, owner, actual, wire, thread_id=thread.id)
    if proof is None:
        raise _refuse()
    reviewer_history.before(proof, completion_sequence)
    document = reviewer_history.document(proof)
    if (
        document["owner_id"] != str(owner)
        or document["run_id"] != str(actual.id)
        or document["attempt_id"] != str(current.id)
        or document["binding_id"] != str(thread.id)
        or document["binding_hash"] != thread.binding_hash
        or document["history_hash"] != observation.history_hash
    ):
        raise _refuse()
    origins = document["origins"]
    replies = _pairs(origins, "reply")
    tools = _pairs(origins, "tool")
    rows = (
        await db.scalars(
            select(ResearchAdvisoryReceipt)
            .where(
                ResearchAdvisoryReceipt.owner_id == owner,
                ResearchAdvisoryReceipt.run_id == actual.id,
                ResearchAdvisoryReceipt.attempt_id == current.id,
            )
            .limit(301)
            .execution_options(populate_existing=True)
        )
    ).all()
    actual_replies = {(str(row.charge_id), row.document_hash) for row in rows}
    if (
        len(rows) > 300
        or not replies
        or len(actual_replies) != len(rows)
        or actual_replies != replies
    ):
        raise _refuse()
    by_charge = {str(row.charge_id): row for row in rows}
    for saved, origin in zip(raw, origins, strict=True):
        if origin["kind"] != "reply":
            continue
        advisory = parse_document(by_charge[origin["source_id"]].document_json)["advisory"]
        expected = AIMessage(
            content=advisory["text"] if advisory["text"] is not None else "",
            tool_calls=advisory["tool_calls"],
        )
        if (
            saved["type"] != "ai"
            or saved["content"] != expected.content
            or saved["tool_calls"] != expected.tool_calls
        ):
            raise _refuse()
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.event_type.in_(["security.reply_recorded", "reviewer.tool_recorded"]),
            )
            .order_by(ResearchEvent.sequence)
            .limit(10001)
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(events) > 10000:
        raise _refuse()
    recorded, actual_tools, recorded_events = [], [], []
    charges = {pair[0] for pair in replies}
    tool_ids = {pair[0] for pair in tools}
    for event in events:
        value = parse_document(event.payload_json)
        if event.event_type == "security.reply_recorded":
            relevant = (
                value.get("attempt_id") == str(current.id)
                or str(event.entity_id) in charges
                or value.get("charge_id") in charges
            )
            if not relevant:
                continue
            _envelope(event, owner, scope)
            if (
                event.sequence >= completion_sequence
                or event.entity_kind != "security_reply"
                or str(event.entity_id) != value.get("charge_id")
                or value.get("attempt_id") != str(current.id)
                or set(value) != {"charge_id", "attempt_id", "document_hash"}
            ):
                raise _refuse()
            recorded.append((value["charge_id"], value["document_hash"]))
            recorded_events.append((str(event.id), event.logical_hash))
        else:
            relevant = (
                value.get("attempt_id") == str(current.id)
                or value.get("binding_id") == str(thread.id)
                or value.get("binding_hash") == thread.binding_hash
                or str(event.id) in tool_ids
            )
            if not relevant:
                continue
            _envelope(event, owner, scope)
            if (
                event.sequence >= completion_sequence
                or event.entity_kind != "reviewer_tool"
                or value.get("run_id") != str(actual.id)
                or value.get("attempt_id") != str(current.id)
                or value.get("binding_id") != str(thread.id)
                or value.get("binding_hash") != thread.binding_hash
            ):
                raise _refuse()
            actual_tools.append((str(event.id), event.logical_hash))
    if (
        len(recorded) != len(set(recorded))
        or set(recorded) != replies
        or len(actual_tools) != len(set(actual_tools))
        or set(actual_tools) != tools
    ):
        raise _refuse()
    closure = dict(
        version="reviewer-completed-history-v1",
        owner_id=str(owner),
        run_id=str(actual.id),
        attempt_id=str(current.id),
        binding_id=str(thread.id),
        binding_hash=thread.binding_hash,
        input_hash=actual.input_hash,
        before_sequence=before_sequence,
        completion_sequence=completion_sequence,
        raw_messages_hash=observation.raw_messages_hash,
        history_hash=observation.history_hash,
        history_proof_hash=proof.document_hash,
        reply_refs=[dict(charge_id=key, document_hash=value) for key, value in sorted(replies)],
        recorded_refs=[
            dict(event_id=key, event_hash=value) for key, value in sorted(recorded_events)
        ],
        tool_refs=[dict(event_id=key, event_hash=value) for key, value in sorted(tools)],
    )
    SafeSerializer().check(closure)
    if (
        db.sync_session.get_transaction() is not transaction
        or not transaction.is_active
        or db.sync_session.get_nested_transaction() is not None
    ):
        raise _refuse()
    return CompletedHistory(proof, canonical_bytes(closure).decode(), logical_hash(closure))
