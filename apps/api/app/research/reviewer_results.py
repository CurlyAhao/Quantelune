"""History-only Review projection; SDK bookkeeping is a separate proved fact."""

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlanningLease
from app.research import harness_binding, reviewer_admission, reviewer_lease, reviewer_receipt
from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_contracts import PROFILE, ReviewerToken
from app.research.reviewer_sdk_finish_contracts import EVENT, load
from app.services import research_service as ledger


async def inspect(db, owner, run):
    """Prove current ledger/history without live source, saver or producer authority."""
    from app.research.replay import verify_business

    _, scope = await ledger.run(db, run.id, owner)
    await verify_business(db, owner, scope.id)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        ),
        execution_options={"populate_existing": True},
    )
    if run.kind != "reviewer" or attempt is None or attempt.state != run.state:
        raise ledger.error("REVIEWER_REVIEW_UNPROVEN")
    if await reviewer_admission.load(db, owner, run, attempt) is None:
        raise ledger.error("INVOCATION_REQUIRED")
    threads = (
        await db.scalars(
            select(ResearchHarnessThread)
            .where(ResearchHarnessThread.attempt_id == attempt.id)
            .limit(2),
            execution_options={"populate_existing": True},
        )
    ).all()
    if len(threads) > 1:
        raise ledger.error("HARNESS_BINDING_CHANGED")
    thread = threads[0] if threads else None
    events = (
        await db.scalars(
            select(ResearchEvent).where(
                ResearchEvent.workspace_id == scope.id, ResearchEvent.event_type == EVENT
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    finished = [
        event
        for event in events
        if parse_document(event.payload_json).get("attempt_id") == str(attempt.id)
    ]
    if len(finished) > 1:
        raise ledger.error("REVIEWER_CHECKPOINT_UNPROVEN")
    if thread is not None:
        lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
        if lease is None or lease.owner_id != owner:
            raise ledger.error("HARNESS_BINDING_CHANGED")
        # The recorded epoch is only an identity input, never live-worker authority.
        harness_binding._verify(
            thread, ReviewerToken(owner, run.id, attempt.id, lease.worker_id, lease.fence), run
        )
        if thread.input_hash != run.input_hash or thread.profile_hash != logical_hash(PROFILE):
            raise ledger.error("HARNESS_BINDING_CHANGED")
    has_fields = thread is not None and (
        thread.finish_hash is not None or thread.finish_checkpoint_id is not None
    )
    if has_fields != bool(finished):
        raise ledger.error("REVIEWER_CHECKPOINT_UNPROVEN")
    if finished:
        event = finished[0]
        value = load(event.payload_json)
        if (
            thread is None
            or run.state != "completed"
            or event.entity_id != thread.id
            or event.logical_hash != thread.finish_hash
            or value.binding_id != str(thread.id)
            or value.observation.writer.route.checkpoint_id != thread.finish_checkpoint_id
        ):
            raise ledger.error("REVIEWER_CHECKPOINT_UNPROVEN")
    receipt = (
        await reviewer_receipt.prove(db, owner, run, attempt)
        if await reviewer_lease.review_event(db, attempt.id) is not None
        else None
    )
    return attempt, thread, receipt, finished[0] if finished else None


async def get_review(db, owner, session_id, run_id):
    await ledger.session(db, session_id, owner)
    run, _ = await ledger.run(db, run_id, owner)
    if run.session_id != session_id or run.kind != "reviewer":
        raise ledger.error("ACCESS_DENIED", 404)
    attempt, thread, receipt, event = await inspect(db, owner, run)
    value = parse_document(receipt.document_json) if receipt is not None else None
    return dict(
        version=1,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        state=run.state,
        revision=run.revision,
        review=(
            dict(
                id=str(receipt.id),
                hash=receipt.document_hash,
                report=value["report"],
                source_version=value["source_version"],
                source_proof_hash=value["source_proof_hash"],
                execution_kind=value["execution_kind"],
                computed=False,
                expert_review=False,
                authorizes_execution=False,
            )
            if value is not None
            else None
        ),
        sdk=dict(
            state="recorded" if event else "pending" if thread else "not_started",
            event_id=str(event.id) if event else None,
            hash=event.logical_hash if event else None,
        ),
    )
