"""Own-thread history from actual settled replies and causally proved tool records."""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchAdvisoryReceipt, ResearchHarnessThread
from app.models.research_security import ResearchCharge
from app.models.user import User
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import failure
from app.research.model_history import validate
from app.research.reviewer_contracts import PROFILE, HashText, UuidText

MAX_PROOF_BYTES = 64 * 1024
_memo: ContextVar[dict | None] = ContextVar("statforge_reviewer_history_proof", default=None)


class _Origin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    message_index: StrictInt = Field(ge=0, le=31)
    kind: Literal["question", "reply", "tool", "todo"]
    source_id: UuidText | None
    call_id: str | None = Field(max_length=128, strict=True)
    sequence: StrictInt = Field(ge=0)
    origin_hash: HashText


class _Proof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["reviewer-history-proof-v1"]
    owner_id: UuidText
    run_id: UuidText
    attempt_id: UuidText
    binding_id: UuidText
    binding_hash: HashText
    input_hash: HashText
    profile_hash: HashText
    review_input_hash: HashText
    history_hash: HashText
    question_hash: HashText
    origins: list[_Origin] = Field(min_length=1, max_length=32)


@dataclass(frozen=True)
class ReviewerHistoryProof:
    """Internal immutable proof data; its hash/type never replaces DB authorization."""

    document_json: str
    document_hash: str


def document(proof: ReviewerHistoryProof) -> dict:
    try:
        if (
            type(proof) is not ReviewerHistoryProof
            or len(proof.document_json.encode()) > MAX_PROOF_BYTES
        ):
            raise ValueError("Invalid own history proof.")
        value = parse_document(proof.document_json)
        normalized = _Proof.model_validate(value).model_dump(mode="json")
        if value != normalized or logical_hash(value) != proof.document_hash:
            raise ValueError("Own history commitment changed.")
        return normalized
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise failure("REVIEWER_HISTORY_UNPROVEN") from None


def initial(value):
    """Foundation accepts empty history only; later activation needs exact own receipts.

    Even a plausible user/assistant message cannot prove its own thread origin.
    Never loosen this guard merely to make the SDK loop reach END.
    """
    checked = validate(value, tool_profile="reviewer")
    if checked:
        raise failure("REVIEWER_HISTORY_UNPROVEN")
    return checked


def checked(proof: ReviewerHistoryProof | None, history, metadata) -> dict | None:
    """Pure wire consistency only; Gateway/record/load must independently prove DB origins."""
    messages = validate(history, tool_profile="reviewer")
    if not messages:
        if proof is not None:
            raise failure("REVIEWER_HISTORY_UNPROVEN")
        return None
    if proof is None:
        initial(messages)  # Preserve the original refusal absent actual server proof.
        raise failure("REVIEWER_HISTORY_UNPROVEN")
    value = document(proof)
    question = metadata["context"]["question"]
    if (
        value["profile_hash"] != logical_hash(PROFILE)
        or value["review_input_hash"] != metadata["input_hash"]
        or value["history_hash"] != logical_hash(messages)
        or value["question_hash"] != logical_hash(question)
        or messages[0] != {"role": "user", "content": question}
        or len(value["origins"]) != len(messages)
    ):
        raise failure("REVIEWER_HISTORY_UNPROVEN")
    for index, (item, origin) in enumerate(zip(messages, value["origins"], strict=True)):
        if origin["message_index"] != index:
            raise failure("REVIEWER_HISTORY_UNPROVEN")
        if index == 0:
            if origin != {
                "message_index": 0,
                "kind": "question",
                "source_id": None,
                "call_id": None,
                "sequence": 0,
                "origin_hash": logical_hash(item),
            }:
                raise failure("REVIEWER_HISTORY_UNPROVEN")
        elif item["role"] == "assistant":
            if (
                origin["kind"] != "reply"
                or origin["source_id"] is None
                or (origin["call_id"] is not None or origin["sequence"] <= 0)
            ):
                raise failure("REVIEWER_HISTORY_UNPROVEN")
        elif item["role"] == "tool":
            if (
                origin["kind"] not in {"tool", "todo"}
                or origin["source_id"] is None
                or (origin["call_id"] != item["tool_call_id"] or origin["sequence"] <= 0)
            ):
                raise failure("REVIEWER_HISTORY_UNPROVEN")
        else:
            raise failure("REVIEWER_HISTORY_UNPROVEN")
    return value


def commitment(proof: ReviewerHistoryProof | None) -> dict | None:
    if proof is None:
        return None
    value = document(proof)
    return {
        "version": value["version"],
        "binding_hash": value["binding_hash"],
        "history_hash": value["history_hash"],
        "proof_hash": proof.document_hash,
    }


def before(proof: ReviewerHistoryProof | None, sequence: int) -> None:
    if proof is not None and any(
        origin["sequence"] >= sequence for origin in document(proof)["origins"]
    ):
        raise failure("REVIEWER_HISTORY_UNPROVEN")


def _assistant(advisory):
    from langchain_core.messages import AIMessage

    from app.preview.harness import protocol_messages

    return protocol_messages(
        [
            AIMessage(
                content=advisory["text"] if advisory["text"] is not None else "",
                tool_calls=advisory["tool_calls"],
            )
        ]
    )[0]


async def prove(db, owner, run, history, *, thread_id=None) -> ReviewerHistoryProof | None:
    """Actual owned sources; every recursive Reply request has a strictly shorter prefix."""
    messages = validate(history, tool_profile="reviewer")
    state = _memo.get()
    reset = None
    if state is None:
        state = {
            "db": db,
            "transaction": db.sync_session.get_transaction(),
            "values": {},
            "active": set(),
        }
        reset = _memo.set(state)
    try:
        transaction = db.sync_session.get_transaction()
        if state["db"] is not db or (
            state["transaction"] is not None and state["transaction"] is not transaction
        ):
            raise failure("REVIEWER_HISTORY_UNPROVEN")
        if state["transaction"] is None and transaction is not None:
            state["transaction"] = transaction
        key = (
            str(owner),
            str(run.id),
            run.input_hash,
            run.attempt_count,
            str(thread_id),
            logical_hash(messages),
        )
        if key in state["active"]:
            raise failure("REVIEWER_HISTORY_UNPROVEN")
        if key in state["values"]:
            return state["values"][key]
        if len(state["values"]) + len(state["active"]) >= 64:
            raise failure("HISTORY_LIMIT")
        state["active"].add(key)
        try:
            result = await _prove(db, owner, run, messages, thread_id=thread_id)
            state["values"][key] = result
            return result
        finally:
            state["active"].remove(key)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        raise failure("REVIEWER_HISTORY_UNPROVEN") from None
    finally:
        if reset is not None:
            _memo.reset(reset)  # No global, checkpoint or cross-transaction proof cache.


async def _prove(db, owner, run, history, *, thread_id=None):
    from app.research import harness_binding, historical_reply, reviewer_admission, reviewer_sources
    from app.services import research_service as ledger

    messages = validate(history, tool_profile="reviewer")
    if not messages:
        return None
    actor = await db.get(User, owner, populate_existing=True)
    actual, scope = await ledger.run(db, run.id, owner)
    if (
        actor is None
        or not actor.is_active
        or actual.kind != "reviewer"
        or actual.input_hash != run.input_hash
    ):
        raise failure("REVIEWER_HISTORY_UNPROVEN")
    source = reviewer_sources.captured(run)
    metadata = reviewer_sources.disclosure(source)
    if messages[0] != {"role": "user", "content": source["request"]["question"]} or any(
        item["role"] == "user" for item in messages[1:]
    ):
        raise failure("REVIEWER_HISTORY_UNPROVEN")
    query = (
        select(ResearchHarnessThread)
        .join(ResearchAttempt, ResearchAttempt.id == ResearchHarnessThread.attempt_id)
        .where(
            ResearchHarnessThread.run_id == run.id,
            ResearchHarnessThread.purpose == "assistant",
        )
    )
    query = (
        query.where(ResearchHarnessThread.id == thread_id)
        if thread_id is not None
        else query.where(ResearchAttempt.number == run.attempt_count)
    )
    rows = (await db.scalars(query.limit(2).execution_options(populate_existing=True))).all()
    if len(rows) != 1:
        raise failure("REVIEWER_HISTORY_UNPROVEN")
    thread = rows[0]
    attempt = await db.get(ResearchAttempt, thread.attempt_id, populate_existing=True)
    if (
        thread.owner_id != owner
        or thread.input_hash != run.input_hash
        or thread.profile_hash != logical_hash(PROFILE)
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or uuid.UUID(thread.thread_id).hex != thread.thread_id
        or attempt is None
        or attempt.run_id != run.id
        or attempt.number > run.attempt_count
        or await reviewer_admission.load(db, owner, run, attempt) is None
    ):
        raise failure("REVIEWER_HISTORY_UNPROVEN")
    origins = [
        {
            "message_index": 0,
            "kind": "question",
            "source_id": None,
            "call_id": None,
            "sequence": 0,
            "origin_hash": logical_hash(messages[0]),
        }
    ]
    replies = (
        await db.scalars(
            select(ResearchAdvisoryReceipt)
            .where(
                ResearchAdvisoryReceipt.owner_id == owner,
                ResearchAdvisoryReceipt.run_id == run.id,
                ResearchAdvisoryReceipt.attempt_id == attempt.id,
            )
            .limit(301)
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(replies) > 300:
        raise failure("HISTORY_LIMIT")
    active = None
    for index, item in enumerate(messages[1:], 1):
        if item["role"] == "assistant":
            matches = []
            for reply in replies:
                saved = parse_document(reply.document_json)
                if _assistant(saved["advisory"]) != item:
                    continue
                charge = await db.get(ResearchCharge, reply.charge_id, populate_existing=True)
                if charge is None:
                    raise failure("REVIEWER_HISTORY_UNPROVEN")
                events = (
                    await db.scalars(
                        select(ResearchEvent)
                        .where(
                            ResearchEvent.workspace_id == scope.id,
                            ResearchEvent.entity_id == charge.approval_id,
                            ResearchEvent.event_type == "harness.model_request",
                        )
                        .limit(2)
                    )
                ).all()
                if len(events) != 1:
                    raise failure("REVIEWER_HISTORY_UNPROVEN")
                request = parse_document(events[0].payload_json)
                # Check this BEFORE historical_reply loads/proves its own prefix.
                # It cannot refer to its own or a later conversation recursively.
                if request.get("conversation") != messages[:index] or (
                    request.get("binding_hash") != thread.binding_hash
                    or request.get("attempt_id") != str(attempt.id)
                ):
                    continue
                proof = await historical_reply.prove(
                    db,
                    owner,
                    run.id,
                    attempt.id,
                    charge.id,
                    reply_hash=reply.document_hash,
                    binding_hash=thread.binding_hash,
                    call_hash=request["call_hash"],
                )
                matches.append((reply, proof, saved["advisory"]))
            if len(matches) != 1:
                raise failure("REVIEWER_HISTORY_UNPROVEN")
            reply, proof, advisory = matches[0]
            active = (reply, proof, advisory)
            origins.append(
                {
                    "message_index": index,
                    "kind": "reply",
                    "source_id": str(reply.charge_id),
                    "call_id": None,
                    "sequence": proof.resumed_sequence,
                    "origin_hash": reply.document_hash,
                }
            )
        elif item["role"] == "tool" and active is not None:
            reply, proof, advisory = active
            calls = [call for call in advisory["tool_calls"] if call["id"] == item["tool_call_id"]]
            if len(calls) != 1:
                raise failure("REVIEWER_HISTORY_UNPROVEN")
            call = calls[0]
            if call["name"] == "write_todos":
                from app.preview.harness import planning_todos

                planning_todos(call["args"])
                if item["content"] != '{"state":"reviewer_state_updated"}':
                    raise failure("REVIEWER_HISTORY_UNPROVEN")
                origin = {
                    "kind": "todo",
                    "source_id": str(reply.charge_id),
                    "sequence": proof.resumed_sequence,
                    "origin_hash": logical_hash({"call": call, "result": item}),
                }
            else:
                from app.research import reviewer_runtime

                identity = reviewer_runtime.identity(
                    thread, {"charge_id": str(reply.charge_id), "tool_call_id": call["id"]}
                )
                events = (
                    await db.scalars(
                        select(ResearchEvent)
                        .where(
                            ResearchEvent.workspace_id == scope.id,
                            ResearchEvent.entity_id == identity,
                            ResearchEvent.event_type == reviewer_runtime.EVENT,
                        )
                        .limit(2)
                    )
                ).all()
                if len(events) != 1:
                    raise failure("REVIEWER_HISTORY_UNPROVEN")
                event = events[0]
                value = await reviewer_runtime.prove_event(db, owner, event)
                if (
                    parse_document(item["content"]) != value["result"]
                    or proof.resumed_sequence >= event.sequence
                ):
                    raise failure("REVIEWER_HISTORY_UNPROVEN")
                origin = {
                    "kind": "tool",
                    "source_id": str(event.id),
                    "sequence": event.sequence,
                    "origin_hash": event.logical_hash,
                }
            origins.append({"message_index": index, "call_id": call["id"], **origin})
        else:
            raise failure("REVIEWER_HISTORY_UNPROVEN")
    value = _Proof(
        version="reviewer-history-proof-v1",
        owner_id=str(owner),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        binding_id=str(thread.id),
        binding_hash=thread.binding_hash,
        input_hash=run.input_hash,
        profile_hash=thread.profile_hash,
        review_input_hash=source["review_input_hash"],
        history_hash=logical_hash(messages),
        question_hash=logical_hash(source["request"]["question"]),
        origins=[_Origin.model_validate(origin) for origin in origins],
    ).model_dump(mode="json")
    raw = canonical_bytes(value)
    if len(raw) > MAX_PROOF_BYTES:
        raise failure("HISTORY_LIMIT")
    result = ReviewerHistoryProof(raw.decode(), logical_hash(value))
    checked(result, messages, metadata)
    return result
