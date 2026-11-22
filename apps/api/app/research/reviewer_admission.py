"""Independent Reviewer admission; no statistical execution or Reader identity."""

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.preview.write_fence import acquire_write_fence
from app.research import execution_lease, reviewer_contracts, reviewer_sources, reviewer_tools
from app.research.contracts import State, logical_hash, parse_document
from app.research.egress import failure
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger

EVENT = "reviewer.admitted"
FIELDS = {
    "version",
    "owner_id",
    "run_id",
    "attempt_id",
    "input_hash",
    "source_version",
    "source_proof_hash",
    "review_input_hash",
    "profile_hash",
    "selection",
    "tools",
}


async def create(db, owner, session_id, request, gateway):
    request = reviewer_contracts.ReviewerRunCreate.model_validate(request)
    from app.research.model_history import validate as validate_history

    # The same initial question becomes actual SDK history. Refuse its existing
    # byte limit before persisting a Run which the first model turn cannot use.
    validate_history([{"role": "user", "content": request.question}], tool_profile="reviewer")
    proof = await reviewer_sources.prove(db, owner, session_id, request)
    captured = reviewer_sources.document(proof)
    run = await ledger.start_run(
        db,
        owner,
        session_id,
        reviewer_contracts.KIND,
        "reviewer-admission:" + logical_hash(request.idempotency_key),
        reviewer_sources.parameters(proof),
    )
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    if attempt is None or attempt.state != run.state:
        raise ledger.error("STATE_INCONSISTENT")
    value = {
        "version": 1,
        "owner_id": str(owner),
        "run_id": str(run.id),
        "attempt_id": str(attempt.id),
        "input_hash": run.input_hash,
        "source_version": request.source_version,
        "source_proof_hash": proof.document_hash,
        "review_input_hash": captured["review_input_hash"],
        "profile_hash": logical_hash(reviewer_contracts.PROFILE),
        "selection": captured["selection"],
        "tools": reviewer_tools.schemas(),
    }
    old = await load(db, owner, run, attempt)
    if old is not None:
        if old != value:
            raise failure("INVOCATION_CONFLICT")
        return run
    if run.state != State.QUEUED.value or run.attempt_count != 1:
        raise failure("INVOCATION_ADMISSION_REFUSED")
    from app.research.egress import DisclosureSelection

    # Actual Gateway checks owned secrets, approved projection and qualified price.
    # Admission never sends a model call or treats this as scientific acceptance.
    prepared = await gateway.prepare(
        db,
        owner,
        run.id,
        DisclosureSelection.model_validate(value["selection"]),
        model_tools=value["tools"],
    )
    gateway.guard(prepared)
    SafeSerializer().check(value)
    _, scope = await ledger.run(db, run.id, owner)
    await ledger._append(db, scope, owner, "reviewer_invocation", attempt.id, EVENT, value)
    return run


async def load(db, owner, run, attempt):
    if attempt is None or attempt.run_id != run.id:
        raise failure("INVOCATION_BINDING_CHANGED")
    saved = reviewer_sources.captured(run)
    if saved["owner_id"] != str(owner):
        raise failure("INVOCATION_BINDING_CHANGED")
    _, scope = await ledger.run(db, run.id, owner)
    rows = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == attempt.id,
                ResearchEvent.event_type == EVENT,
            )
            .limit(2)
        )
    ).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise failure("INVOCATION_CONFLICT")
    event = rows[0]
    value = parse_document(event.payload_json)
    if (
        type(value) is not dict
        or set(value) != FIELDS
        or type(value["version"]) is not int
        or value["version"] != 1
        or event.entity_kind != "reviewer_invocation"
        or event.actor_id != owner
        or event.logical_hash != logical_hash(value)
        or value["owner_id"] != str(owner)
        or value["run_id"] != str(run.id)
        or value["attempt_id"] != str(attempt.id)
        or value["input_hash"] != run.input_hash
        or value["source_version"] != saved["request"]["source_version"]
        or value["source_proof_hash"] != logical_hash(saved)
        or value["review_input_hash"] != saved["review_input_hash"]
        or value["profile_hash"] != logical_hash(reviewer_contracts.PROFILE)
        or value["selection"] != saved["selection"]
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    reviewer_tools.assert_tools(value["tools"])
    SafeSerializer().check(value)
    return value


async def source(db, owner, run):
    await ledger.run(db, run.id, owner)
    # Preserve the original actor lock and validation order. The mandatory
    # live proof below reads each nonempty step through the normal result
    # reader, which performs full business replay in this same transaction.
    await acquire_write_fence(db, owner)
    await execution_lease._source(db, owner, run, mode="live")
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    if await load(db, owner, run, attempt) is None:
        raise failure("INVOCATION_REQUIRED")
    return await reviewer_sources.live(db, owner, run)


async def before_event(db, owner, run, event, prior_steps):
    """Historical content must agree with independently proved prior step bytes."""
    import uuid

    from app.models.research_planning import ResearchPlanStep
    from app.research import p4_result_reader

    saved = reviewer_sources.captured(run)
    if saved["owner_id"] != str(owner):
        raise failure("INVOCATION_BINDING_CHANGED")
    for source in saved["review_context"]["sources"]:
        facts = source["result_facts"]
        reference = facts["binding"]
        accepted = uuid.UUID(reference["acceptance_id"])
        step = await db.scalar(
            select(ResearchPlanStep).where(
                ResearchPlanStep.acceptance_id == accepted,
                ResearchPlanStep.step_id == reference["step_id"],
            )
        )
        if (
            step is None
            or step.id not in prior_steps
            or (logical_hash(prior_steps[step.id]) != reference["step_result_hash"])
        ):
            raise failure("REVIEWER_SOURCE_UNPROVEN")
        actual = await p4_result_reader.read_in_verified_replay(
            db, owner, run.session_id, accepted, reference["step_id"]
        )
        if actual != facts:
            raise failure("REVIEWER_SOURCE_CHANGED")
    attempt = await db.get(ResearchAttempt, event.entity_id, populate_existing=True)
    value = await load(db, owner, run, attempt)
    if value != parse_document(event.payload_json):
        raise failure("INVOCATION_BINDING_CHANGED")
