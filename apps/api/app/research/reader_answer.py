"""Saved-field answer receipt is separate from SDK finish and native computation."""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlanningLease
from app.models.research_security import ResearchCharge
from app.research import (
    advisory_receipt,
    harness_binding,
    historical_reply,
    reader_admission,
    reader_answer_fields,
    reader_contracts,
    reader_lease,
    reader_sources,
    result_contexts,
)
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import credentials, failure
from app.research.harness_serde import SafeSerializer
from app.services import research_security_service as security
from app.services import research_service as ledger

EVENT = reader_lease.ANSWER_EVENT
MAX_BYTES = 768 * 1024


@dataclass(frozen=True)
class AnswerReceipt:
    id: uuid.UUID
    document_hash: str
    document_json: str
    sequence: int


async def _reply(db, owner, run, attempt, admission, value, *, fresh=False):
    proof = await historical_reply.prove(
        db,
        owner,
        run.id,
        attempt.id,
        uuid.UUID(value["charge_id"]),
        reply_hash=value["reply_hash"],
        binding_hash=value["binding_hash"],
        call_hash=value["call_hash"],
    )
    request = parse_document(proof.request_json)
    document = parse_document(proof.document_json)
    if request["selection"] != admission["selection"] or request["tools"] != admission["tools"]:
        raise failure("READER_ANSWER_UNPROVEN")
    if fresh:
        charge = await security.charge(db, owner, uuid.UUID(value["charge_id"]))
        approval = await security.approval(db, owner, charge.approval_id)
        actual = await advisory_receipt.read(db, owner, approval, charge)
        if actual is None or actual["reply_receipt_hash"] != value["reply_hash"]:
            raise failure("READER_ANSWER_UNPROVEN")
    calls = [
        call
        for call in document["advisory"]["tool_calls"]
        if call.get("id") == value["tool_call_id"]
    ]
    if (
        len(calls) != 1
        or calls[0]["name"] != "explain_results"
        or set(calls[0]["args"]) != {"spec"}
    ):
        raise failure("READER_ANSWER_UNPROVEN")
    selected = reader_contracts.explanation(calls[0]["args"]["spec"], admission["version"])
    if selected.model_dump(mode="json") != value["request"]:
        raise failure("READER_ANSWER_UNPROVEN")
    return proof, document["execution_kind"]


async def record(
    db,
    token: reader_contracts.ReaderToken,
    binding,
    charge_id: uuid.UUID,
    reply_hash: str,
    call_hash: str,
    tool_call_id: str,
    request,
):
    """Internal only. Runtime and replay activation must be enabled together.

    A model selects fields; the server supplies all values, templates and
    citations. A free-text Reply and an SDK END cannot create this receipt.
    Caller owns the transaction; this function does not complete a Run.
    """
    if type(token) is not reader_contracts.ReaderToken or (
        type(tool_call_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", tool_call_id) is None
    ):
        raise failure("READER_ANSWER_UNPROVEN")
    run, scope, lease, _ = await reader_lease.validate(db, token, current_source=False)
    request = reader_contracts.explanation(
        request.model_dump(mode="json"),
        reader_contracts.view_version(parse_document(run.input_json)["parameters"]),
    )
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    old = await reader_lease.answer_event(db, attempt.id)
    reference = dict(
        charge_id=str(charge_id),
        reply_hash=reply_hash,
        call_hash=call_hash,
        binding_hash=binding.binding_hash,
        tool_call_id=tool_call_id,
        request=request.model_dump(mode="json"),
    )
    if old is not None:
        receipt = await prove(db, token.owner_id, run, attempt)
        value = parse_document(receipt.document_json)
        if any(value[k] != v for k, v in reference.items()):
            raise failure("READER_ANSWER_CONFLICT")
        return receipt  # Historical retry returns the original bytes; no new tool/model rights.
    run, scope, lease, _ = await reader_lease.validate(db, token)
    if lease.pause_requested:
        raise failure("MODEL_PAUSED")
    row = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
    if row is None or any(
        getattr(row, k) != getattr(binding, k) for k in binding.__dataclass_fields__
    ):
        raise failure("READER_ANSWER_UNPROVEN")
    harness_binding._verify(row, token, run)
    if row.finish_hash is not None:
        raise failure("READER_ANSWER_UNPROVEN")
    if (
        await db.scalar(
            select(ResearchCharge.id).where(
                ResearchCharge.run_id == run.id,
                ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
            )
        )
        is not None
    ):
        raise failure("READER_ANSWER_FINANCIAL_PENDING")
    admission = await reader_admission.load(db, token.owner_id, run, attempt)
    if admission is None:
        raise failure("READER_ANSWER_UNPROVEN")
    proof, kind = await _reply(db, token.owner_id, run, attempt, admission, reference, fresh=True)
    source = await reader_sources.verify(
        db,
        token.owner_id,
        run.session_id,
        admission["selection"],
        admission["source_version"],
        admission["source_proof_hash"],
        projection_version=admission["version"],
    )
    if admission["version"] == 1:
        actual = await _live_v1(db, token.owner_id, run, admission, request)
        if actual != await reader_answer_fields.project(db, token.owner_id, source, request):
            raise failure("READER_ANSWER_UNPROVEN")
    else:
        actual = await reader_answer_fields.project(db, token.owner_id, source, request)
    value = dict(
        version=admission["version"],
        owner_id=str(token.owner_id),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        input_hash=run.input_hash,
        admission_hash=logical_hash(admission),
        source_version=admission["source_version"],
        source_proof_hash=source.document_hash,
        source=reader_sources.document(source),
        fence=token.fence,
        run_revision=run.revision,
        reply_resumed_sequence=proof.resumed_sequence,
        execution_kind=kind,
        answer=actual,
        **reference,
    )
    SafeSerializer().check(value)
    if credentials(value) or len(canonical_bytes(value)) > MAX_BYTES:
        raise failure("READER_ANSWER_LIMIT")
    await reader_sources.verify(
        db,
        token.owner_id,
        run.session_id,
        admission["selection"],
        admission["source_version"],
        admission["source_proof_hash"],
        projection_version=admission["version"],
    )
    _, _, final_lease, _ = await reader_lease.validate(db, token, current_source=False)
    if final_lease.pause_requested:
        raise failure("MODEL_PAUSED")
    event = await ledger._append(
        db, scope, token.owner_id, "reader_answer", attempt.id, EVENT, value
    )
    return AnswerReceipt(event.id, event.logical_hash, event.payload_json, event.sequence)


async def _live_v1(db, owner, run, admission, request):
    values = await result_contexts.read(
        db,
        owner,
        run.session_id,
        admission["selection"],
        admission["source_version"],
        artifact_only=True,
    )
    from app.models.user import User

    return await result_contexts.explain(db, await db.get(User, owner), request, values)


async def prove(db, owner, run, attempt):
    """Original source commitment and Reply facts, not fresh send authorization."""
    try:
        return await _prove(db, owner, run, attempt)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure("READER_ANSWER_UNPROVEN") from None


async def _prove(db, owner, run, attempt):
    if attempt is None or run.kind != reader_contracts.KIND or attempt.run_id != run.id:
        raise failure("READER_ANSWER_UNPROVEN")
    current, scope = await ledger.run(db, run.id, owner)
    if current.input_hash != run.input_hash:
        raise failure("READER_ANSWER_UNPROVEN")
    event = await reader_lease.answer_event(db, attempt.id)
    lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
    if (
        event is None
        or lease is None
        or event.workspace_id != scope.id
        or event.actor_id != owner
        or event.entity_kind != "reader_answer"
    ):
        raise failure("READER_ANSWER_UNPROVEN")
    value = parse_document(event.payload_json)
    if set(value) != {
        "version",
        "owner_id",
        "run_id",
        "attempt_id",
        "input_hash",
        "admission_hash",
        "source_version",
        "source_proof_hash",
        "source",
        "fence",
        "run_revision",
        "reply_resumed_sequence",
        "execution_kind",
        "answer",
        "charge_id",
        "reply_hash",
        "call_hash",
        "binding_hash",
        "tool_call_id",
        "request",
    }:
        raise failure("READER_ANSWER_UNPROVEN")
    admission = await reader_admission.load(db, owner, run, attempt)
    if admission is None or (
        type(value["version"]) is not int
        or value["version"] != admission["version"]
        or reader_contracts.source_view(value["source"]) != admission["version"]
        or value["owner_id"] != str(owner)
        or value["run_id"] != str(run.id)
        or value["attempt_id"] != str(attempt.id)
        or value["input_hash"] != run.input_hash
        or logical_hash(parse_document(run.input_json)) != run.input_hash
        or event.logical_hash != logical_hash(value)
        or len(event.payload_json.encode()) > MAX_BYTES
        or value["admission_hash"] != logical_hash(admission)
        or value["source_version"] != admission["source_version"]
        or value["source_proof_hash"] != admission["source_proof_hash"]
        or logical_hash(value["source"]) != value["source_proof_hash"]
        or value["source"]["session_id"] != str(run.session_id)
        or value["source"]["source_version"] != value["source_version"]
        or value["source"]["selection_hash"] != logical_hash(admission["selection"])
        or lease.owner_id != owner
        or type(value["fence"]) is not int
        or not 1 <= value["fence"] <= lease.fence
        or type(value["run_revision"]) is not int
        or not 0 <= value["run_revision"] <= run.revision
    ):
        raise failure("READER_ANSWER_UNPROVEN")
    SafeSerializer().check(value)
    if credentials(value):
        raise failure("READER_ANSWER_UNPROVEN")
    selected_ids = set(admission["selection"]["artifact_ids"])
    captured_ids = [
        citation["artifact_id"]
        for item in value["source"]["results"]
        for citation in item["citations"]
    ]
    if len(captured_ids) != len(set(captured_ids)) or set(captured_ids) != selected_ids:
        raise failure("READER_ANSWER_UNPROVEN")
    proof, kind = await _reply(db, owner, run, attempt, admission, value)
    if (
        kind != value["execution_kind"]
        or proof.resumed_sequence != value["reply_resumed_sequence"]
        or proof.resumed_sequence >= event.sequence
    ):
        raise failure("READER_ANSWER_UNPROVEN")
    source = reader_sources.ReaderSourceProof(
        canonical_bytes(value["source"]).decode(), value["source_proof_hash"]
    )
    request = reader_contracts.explanation(value["request"], admission["version"])
    if value["answer"] != await reader_answer_fields.project(db, owner, source, request):
        raise failure("READER_ANSWER_UNPROVEN")
    # historical_reply.prove checked the entire single kernel snapshot containing
    # this event. No recursive business replay and no current schema/Provider.
    return AnswerReceipt(event.id, event.logical_hash, event.payload_json, event.sequence)
