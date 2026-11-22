"""Fresh authorized P4 review inputs; no native compute or executor conversation."""

import uuid
from dataclasses import dataclass

from app.models.user import User
from app.research import (
    execution_lease,
    p4_result_reader,
    plan_acceptance,
    plan_versioning,
    reader_sources,
)
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import DisclosureSelection, failure
from app.research.harness_serde import SafeSerializer
from app.research.reviewer_contracts import (
    KIND,
    OPERATION,
    PROFILE,
    ReviewerRunCreate,
    ReviewEvidence,
    ReviewInput,
    review_input_hash,
)
from app.services import research_service as ledger

MAX_SOURCE_BYTES = 768 * 1024
PARAMETERS = {"operation", "source_version", "reviewer_source", "source_proof_hash"}


@dataclass(frozen=True)
class ReviewerSourceProof:
    document_json: str
    document_hash: str


def request_document(request: ReviewerRunCreate) -> dict:
    """Private action idempotency text is not part of the evidence projection."""
    request = ReviewerRunCreate.model_validate(request.model_dump(mode="json"))
    return {
        "source_version": request.source_version,
        "question": request.question,
        "steps": [step.model_dump(mode="json") for step in request.steps],
    }


def document(proof: ReviewerSourceProof) -> dict:
    try:
        return _document(proof)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        raise failure("REVIEWER_SOURCE_CHANGED") from None


def _document(proof: ReviewerSourceProof) -> dict:
    if type(proof) is not ReviewerSourceProof or (
        len(proof.document_json.encode("utf-8")) > MAX_SOURCE_BYTES
    ):
        raise failure("REVIEWER_SOURCE_CHANGED")
    value = parse_document(proof.document_json)
    if (
        type(value) is not dict
        or set(value)
        != {
            "version",
            "owner_id",
            "session_id",
            "request",
            "selection",
            "selected_access",
            "selected_access_hash",
            "review_context",
            "review_input_hash",
        }
        or value["version"] != "reviewer-source-v1"
        or logical_hash(value) != proof.document_hash
        or logical_hash(value["selected_access"]) != value["selected_access_hash"]
    ):
        raise failure("REVIEWER_SOURCE_CHANGED")
    context = ReviewInput.model_validate(value["review_context"])
    request = ReviewerRunCreate.model_validate(
        {**value["request"], "idempotency_key": "reviewer-source-identity"}
    )
    selected = DisclosureSelection.model_validate(value["selection"])
    access = value["selected_access"]
    identifiers = [str(source.artifact_id) for source in context.sources]
    references = [
        {
            "acceptance_id": source.result_facts["binding"]["acceptance_id"],
            "step_id": source.result_facts["binding"]["step_id"],
        }
        for source in context.sources
    ]
    if (
        review_input_hash(context) != value["review_input_hash"]
        or request_document(request) != value["request"]
        or context.question != request.question
        or selected.question != request.question
        or selected.purpose != "review"
        or selected.scope != "metadata_only"
        or selected.datasets
        or selected.rows != 0
        or selected.acknowledge_raw
        or selected.artifact_ids != identifiers
        or references != [step.model_dump(mode="json") for step in request.steps]
        or access.get("version") != "reader-source-v1"
        or access.get("owner_id") != value["owner_id"]
        or access.get("session_id") != value["session_id"]
        or access.get("source_version") != request.source_version
        or access.get("selection_hash") != logical_hash(selected.model_dump(mode="json"))
        or {
            citation["artifact_id"]
            for result in access.get("results", [])
            for citation in result["citations"]
        }
        != set(identifiers)
    ):
        raise failure("REVIEWER_SOURCE_CHANGED")
    return value


async def prove(db, owner, session_id, request: ReviewerRunCreate) -> ReviewerSourceProof:
    """Caller owns the transaction; structural DTOs never authorize these reads."""
    request = ReviewerRunCreate.model_validate(request.model_dump(mode="json"))
    await execution_lease._boundary(db, owner)
    await ledger.session(db, session_id, owner, mode="live")
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)

    sources = []
    captured = None
    for reference in request.steps:
        facts = await p4_result_reader.read(
            db, owner, session_id, reference.acceptance_id, reference.step_id
        )
        sources.append(
            ReviewEvidence(
                artifact_id=uuid.UUID(facts["binding"]["root"]["root_artifact_id"]),
                result_facts=facts,
            )
        )
        if captured is None:
            _, _, saved = await plan_acceptance.version(
                db, owner, session_id, uuid.UUID(facts["binding"]["plan_version_id"])
            )
            plan = plan_versioning.parse(saved["plan"])
            if not plan_versioning.is_v2(plan):
                raise failure("REVIEWER_SOURCE_UNPROVEN")
            protocol = plan_versioning.captured_protocol(
                plan, ContextSnapshot.model_validate(saved["context"]), saved["protocol"]
            )
            captured = (protocol, plan_versioning.binding(plan))
    if captured is None:
        raise failure("REVIEWER_RESULT_REQUIRED")
    protocol, binding = captured
    context = ReviewInput(
        version="research-review-context-v1",
        question=request.question,
        protocol=protocol,
        protocol_version_id=uuid.UUID(binding["version_id"]),
        protocol_hash=binding["protocol_hash"],
        generation=binding["generation"],
        sources=sources,
    )
    selected = DisclosureSelection(
        question=request.question,
        purpose="review",
        scope="metadata_only",
        datasets=[],
        artifact_ids=[str(source.artifact_id) for source in context.sources],
        rows=0,
        acknowledge_raw=False,
    )
    # This low-level existing selected-result gate proves current attachment,
    # project/dataset/Artifact ACL and source SHA. It supplies no Reader admission,
    # model identity, answer, execution authority or conversation to the Reviewer.
    selected_access = await reader_sources.prove(
        db, owner, session_id, selected, request.source_version, projection_version=1
    )
    value = {
        "version": "reviewer-source-v1",
        "owner_id": str(owner),
        "session_id": str(session_id),
        "request": request_document(request),
        "selection": selected.model_dump(mode="json"),
        "selected_access": reader_sources.document(selected_access),
        "selected_access_hash": selected_access.document_hash,
        "review_context": context.model_dump(mode="json"),
        "review_input_hash": review_input_hash(context),
    }
    SafeSerializer().check(value)
    content = canonical_bytes(value)
    if len(content) > MAX_SOURCE_BYTES:
        raise failure("REVIEWER_SOURCE_LIMIT")
    return ReviewerSourceProof(content.decode("utf-8"), logical_hash(value))


async def verify(db, owner, session_id, request, expected_hash) -> ReviewerSourceProof:
    proof = await prove(db, owner, session_id, request)
    if proof.document_hash != expected_hash:
        raise failure("REVIEWER_SOURCE_CHANGED")
    return proof


def parameters(proof: ReviewerSourceProof) -> dict:
    value = document(proof)
    return {
        "operation": OPERATION,
        "source_version": value["request"]["source_version"],
        "reviewer_source": value,
        "source_proof_hash": proof.document_hash,
    }


def captured(run) -> dict:
    """Historical capture consistency; fresh ACL/source proof remains separate."""
    try:
        value = parse_document(run.input_json)
        saved = value["parameters"]
        if (
            run.kind != KIND
            or value["kind"] != KIND
            or logical_hash(value) != run.input_hash
            or type(saved) is not dict
            or set(saved) != PARAMETERS
            or saved["operation"] != OPERATION
        ):
            raise ValueError("Reviewer capture changed.")
        proof = ReviewerSourceProof(
            canonical_bytes(saved["reviewer_source"]).decode("utf-8"), saved["source_proof_hash"]
        )
        source = document(proof)
        if (
            source["session_id"] != str(run.session_id)
            or saved["source_version"] != source["request"]["source_version"]
        ):
            raise ValueError("Reviewer source scope changed.")
        return source
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        raise failure("REVIEWER_SOURCE_CHANGED") from None


def disclosure(value: dict) -> dict:
    context = ReviewInput.model_validate(value["review_context"])
    digest = review_input_hash(context)
    if digest != value["review_input_hash"]:
        raise failure("REVIEWER_SOURCE_CHANGED")
    return {
        "version": "research-review-disclosure-v1",
        "context": context.model_dump(mode="json"),
        "input_hash": digest,
        "profile_hash": logical_hash(PROFILE),
    }


def selected_metadata(value: dict, selection: DisclosureSelection) -> dict:
    """Strict pure wire consistency, never an access authorization."""
    try:
        if type(value) is not dict or set(value) != {
            "version",
            "context",
            "input_hash",
            "profile_hash",
        }:
            raise ValueError("Reviewer disclosure shape changed.")
        context = ReviewInput.model_validate(value["context"])
        selection = DisclosureSelection.model_validate(selection.model_dump(mode="json"))
        if (
            value["version"] != "research-review-disclosure-v1"
            or value["input_hash"] != review_input_hash(context)
            or value["profile_hash"] != logical_hash(PROFILE)
            or selection.question != context.question
            or selection.purpose != "review"
            or selection.scope != "metadata_only"
            or selection.datasets
            or selection.rows != 0
            or selection.acknowledge_raw
            or selection.artifact_ids != [str(source.artifact_id) for source in context.sources]
        ):
            raise ValueError("Reviewer disclosure scope changed.")
        normalized = {**value, "context": context.model_dump(mode="json")}
        if normalized != value:
            raise ValueError("Reviewer disclosure normalization changed.")
        return normalized
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        raise failure("REVIEWER_DISCLOSURE_REFUSED") from None


async def live(db, owner, run) -> dict:
    saved = captured(run)
    if saved["owner_id"] != str(owner):
        raise failure("REVIEWER_SOURCE_CHANGED")
    request = ReviewerRunCreate.model_validate(
        {**saved["request"], "idempotency_key": "reviewer-source-identity"}
    )
    await verify(db, owner, run.session_id, request, logical_hash(saved))
    return saved
