"""Typed independent review result; actual settled Reply provenance is mandatory."""

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
    reviewer_admission,
    reviewer_contracts,
    reviewer_lease,
    reviewer_sources,
)
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import credentials, failure
from app.research.harness_serde import SafeSerializer
from app.research.reviewer_contracts import ReviewInput, ReviewReport, validate_review_report
from app.services import research_security_service as security
from app.services import research_service as ledger

EVENT = reviewer_lease.REVIEW_EVENT
MAX_BYTES = 1024 * 1024
FIELDS = {
    "version",
    "owner_id",
    "run_id",
    "attempt_id",
    "input_hash",
    "admission_hash",
    "source_version",
    "source_proof_hash",
    "source",
    "review_input_hash",
    "fence",
    "run_revision",
    "reply_resumed_sequence",
    "execution_kind",
    "report",
    "charge_id",
    "reply_hash",
    "call_hash",
    "binding_hash",
    "tool_call_id",
}


@dataclass(frozen=True)
class ReviewReceipt:
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
    reply = parse_document(proof.document_json)
    saved = reviewer_sources.captured(run)
    if (
        request["version"] != 4
        or type(request["version"]) is not int
        or request["selection"] != admission["selection"]
        or request["tools"] != admission["tools"]
        or request["source_proof_hash"] != admission["source_proof_hash"]
        or request["review_context"] != reviewer_sources.disclosure(saved)
        or request["profile_hash"] != admission["profile_hash"]
    ):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    if fresh:
        charge = await security.charge(db, owner, uuid.UUID(value["charge_id"]))
        approval = await security.approval(db, owner, charge.approval_id)
        actual = await advisory_receipt.read(db, owner, approval, charge)
        if actual is None or actual["reply_receipt_hash"] != value["reply_hash"]:
            raise failure("REVIEWER_REVIEW_UNPROVEN")
    calls = [
        call for call in reply["advisory"]["tool_calls"] if call.get("id") == value["tool_call_id"]
    ]
    if (
        len(calls) != 1
        or calls[0]["name"] != "submit_review"
        or set(calls[0]["args"]) != {"report"}
    ):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    context = ReviewInput.model_validate(saved["review_context"])
    report = validate_review_report(
        ReviewReport.model_validate(calls[0]["args"]["report"]), context
    )
    if report.model_dump(mode="json") != value["report"]:
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    return proof, reply["execution_kind"]


async def record(db, token, binding, charge_id, reply_hash, call_hash, tool_call_id, report):
    """Internal only; SDK END/free text/structural validity never creates a review."""
    if type(token) is not reviewer_contracts.ReviewerToken or (
        type(tool_call_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", tool_call_id) is None
    ):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    run, scope, _, _ = await reviewer_lease.validate(db, token, current_source=False)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    saved = reviewer_sources.captured(run)
    checked = validate_review_report(
        ReviewReport.model_validate(report.model_dump(mode="json")),
        ReviewInput.model_validate(saved["review_context"]),
    )
    reference = {
        "charge_id": str(charge_id),
        "reply_hash": reply_hash,
        "call_hash": call_hash,
        "binding_hash": binding.binding_hash,
        "tool_call_id": tool_call_id,
        "report": checked.model_dump(mode="json"),
    }
    old = await reviewer_lease.review_event(db, attempt.id)
    if old is not None:
        receipt = await prove(db, token.owner_id, run, attempt)
        old_value = parse_document(receipt.document_json)
        if any(old_value[key] != value for key, value in reference.items()):
            raise failure("REVIEWER_REVIEW_CONFLICT")
        return receipt
    run, scope, lease, _ = await reviewer_lease.validate(db, token)
    row = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
    if row is None or any(
        getattr(row, key) != getattr(binding, key) for key in binding.__dataclass_fields__
    ):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    harness_binding._verify(row, token, run)
    if row.finish_hash is not None or lease.pause_requested:
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    pending = await db.scalar(
        select(ResearchCharge.id).where(
            ResearchCharge.run_id == run.id,
            ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
        )
    )
    if pending is not None:
        raise failure("REVIEWER_REVIEW_FINANCIAL_PENDING")
    admission = await reviewer_admission.load(db, token.owner_id, run, attempt)
    if admission is None:
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    proof, kind = await _reply(db, token.owner_id, run, attempt, admission, reference, fresh=True)
    source = await reviewer_sources.live(db, token.owner_id, run)
    value = {
        "version": 1,
        "owner_id": str(token.owner_id),
        "run_id": str(run.id),
        "attempt_id": str(attempt.id),
        "input_hash": run.input_hash,
        "admission_hash": logical_hash(admission),
        "source_version": admission["source_version"],
        "source_proof_hash": logical_hash(source),
        "source": source,
        "review_input_hash": source["review_input_hash"],
        "fence": token.fence,
        "run_revision": run.revision,
        "reply_resumed_sequence": proof.resumed_sequence,
        "execution_kind": kind,
        **reference,
    }
    SafeSerializer().check(value)
    if credentials(value) or len(canonical_bytes(value)) > MAX_BYTES:
        raise failure("REVIEWER_REVIEW_LIMIT")
    await reviewer_sources.live(db, token.owner_id, run)
    _, _, final_lease, _ = await reviewer_lease.validate(db, token, current_source=False)
    if final_lease.pause_requested:
        raise failure("MODEL_PAUSED")
    event = await ledger._append(
        db, scope, token.owner_id, "reviewer_receipt", attempt.id, EVENT, value
    )
    return ReviewReceipt(event.id, event.logical_hash, event.payload_json, event.sequence)


async def prove(db, owner, run, attempt):
    try:
        return await _prove(db, owner, run, attempt)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        raise failure("REVIEWER_REVIEW_UNPROVEN") from None


async def _prove(db, owner, run, attempt):
    if attempt is None or run.kind != reviewer_contracts.KIND or attempt.run_id != run.id:
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    current, scope = await ledger.run(db, run.id, owner)
    saved = reviewer_sources.captured(run)
    event = await reviewer_lease.review_event(db, attempt.id)
    lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
    if (
        current.input_hash != run.input_hash
        or event is None
        or lease is None
        or event.workspace_id != scope.id
        or event.actor_id != owner
        or event.entity_kind != "reviewer_receipt"
    ):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    value = parse_document(event.payload_json)
    admission = await reviewer_admission.load(db, owner, run, attempt)
    if (
        admission is None
        or set(value) != FIELDS
        or (
            type(value["version"]) is not int
            or value["version"] != 1
            or value["owner_id"] != str(owner)
            or value["run_id"] != str(run.id)
            or value["attempt_id"] != str(attempt.id)
            or value["input_hash"] != run.input_hash
            or value["admission_hash"] != logical_hash(admission)
            or value["source_version"] != admission["source_version"]
            or value["source_proof_hash"] != admission["source_proof_hash"]
            or value["source"] != saved
            or logical_hash(saved) != value["source_proof_hash"]
            or value["review_input_hash"] != saved["review_input_hash"]
            or event.logical_hash != logical_hash(value)
            or len(event.payload_json.encode()) > MAX_BYTES
            or lease.owner_id != owner
            or type(value["fence"]) is not int
            or not 1 <= value["fence"] <= lease.fence
            or type(value["run_revision"]) is not int
            or not 0 <= value["run_revision"] <= run.revision
        )
    ):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    SafeSerializer().check(value)
    if credentials(value):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    checked = validate_review_report(
        ReviewReport.model_validate(value["report"]),
        ReviewInput.model_validate(saved["review_context"]),
    )
    if checked.model_dump(mode="json") != value["report"]:
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    proof, kind = await _reply(db, owner, run, attempt, admission, value)
    if (
        kind != value["execution_kind"]
        or proof.resumed_sequence != value["reply_resumed_sequence"]
        or proof.resumed_sequence >= event.sequence
    ):
        raise failure("REVIEWER_REVIEW_UNPROVEN")
    return ReviewReceipt(event.id, event.logical_hash, event.payload_json, event.sequence)
