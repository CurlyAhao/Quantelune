"""Same-host process observation bound to the original Reviewer and fresh DB state.

No signal, timeout inference, source capture, takeover or financial mutation.
The observation becomes durable only in the later atomic recovery transaction.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import ResearchAttempt, ResearchEvent, ResearchRun
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlanningLease
from app.models.research_security import ResearchApproval, ResearchCharge
from app.models.user import User
from app.research import (
    execution_identity,
    execution_lease,
    harness_binding,
    reviewer_finish,
    reviewer_identity,
    reviewer_lease,
    reviewer_receipt,
)
from app.research.contracts import logical_hash
from app.research.execution_identity import WorkerIdentity
from app.research.reviewer_contracts import PROFILE
from app.research.reviewer_recovery_contracts import DeathObservation, Observer, Producer, producer
from app.services import research_service as ledger


@dataclass(frozen=True)
class ReviewerSnapshot:
    run: ResearchRun
    attempt: ResearchAttempt
    lease: ResearchPlanningLease
    claim_event: ResearchEvent
    claim: reviewer_identity.ReviewerClaim
    state_hash: str
    producer: Producer
    previous_recovery_id: str | None
    previous_recovery_hash: str | None
    review: reviewer_receipt.ReviewReceipt | None
    binding: ResearchHarnessThread | None


def observer_value(worker: WorkerIdentity) -> Observer:
    if type(worker) is not WorkerIdentity or not worker.validate_current():
        raise ledger.error("EXECUTION_IDENTITY_UNKNOWN")
    return Observer(
        worker_id=worker.worker_id, host=worker.host, pid=worker.pid, birth=worker.birth
    )


def process_observation(
    claim: reviewer_identity.ReviewerClaim | Producer,
) -> tuple[Literal["dead", "birth_changed"], str | None]:
    """Probe only the recorded PID on the actual same host, never enumerate."""
    try:
        if execution_identity.host_identity() != claim.producer_host:
            raise ledger.error("REVIEWER_DEATH_UNPROVEN")
        state, birth = execution_identity.process_identity(claim.producer_pid)
    except (OSError, ValueError, RuntimeError):
        raise ledger.error("REVIEWER_DEATH_UNPROVEN") from None
    if state == "dead":
        return "dead", None
    if state == "alive" and birth is not None and birth != claim.producer_birth:
        return "birth_changed", birth
    raise ledger.error("REVIEWER_DEATH_UNPROVEN")


def row_document(row) -> dict:
    # Only a digest leaves this function. Full current row values catch changes
    # in an Review, binding, fee or control without storing source/credential data.
    return {
        column.key: value if value is None or type(value) in {str, int, bool} else str(value)
        for column in row.__table__.columns
        for value in [getattr(row, column.key)]
    }


async def snapshot(db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID) -> ReviewerSnapshot:
    await execution_lease._boundary(db, owner)
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    run, scope = await ledger.run(db, run_id, owner)
    if run.kind != "reviewer":
        raise ledger.error("REVIEWER_UNSUPPORTED", 422)
    from app.research.replay import verify_business

    await verify_business(db, owner, scope.id)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        ),
        execution_options={"populate_existing": True},
    )
    if attempt is None or attempt.state != run.state:
        raise ledger.error("STATE_INCONSISTENT")
    lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
    if lease is None:
        raise ledger.error("REVIEWER_CLAIM_UNPROVEN")
    if lease.fence == 1:
        claim_event, claim = await reviewer_identity.original(db, owner, run, attempt, lease)
        epoch, previous = producer(claim), None
        prior_id, prior_hash = None, None
    else:
        from app.research import reviewer_recovery

        claim_event, claim, epoch, previous = await reviewer_recovery.current(
            db, owner, run, attempt, lease
        )
        if previous is None:
            raise ledger.error("REVIEWER_RECOVERY_UNPROVEN")
        prior_id, prior_hash = str(previous.id), previous.logical_hash
    bindings = (
        await db.scalars(
            select(ResearchHarnessThread).where(ResearchHarnessThread.attempt_id == attempt.id),
            execution_options={"populate_existing": True},
        )
    ).all()
    if len(bindings) > 1:
        raise ledger.error("HARNESS_BINDING_CHANGED")
    binding = bindings[0] if bindings else None
    if binding is not None and (
        binding.owner_id != owner
        or binding.run_id != run.id
        or binding.purpose != "assistant"
        or binding.input_hash != run.input_hash
        or binding.profile_hash != logical_hash(PROFILE)
        or binding.binding_hash != logical_hash(harness_binding.document(binding))
    ):
        raise ledger.error("HARNESS_BINDING_CHANGED")
    if binding is not None and binding.finish_hash is not None:
        # A proved completed SDK never needs a new epoch or producer identity.
        from app.research import reviewer_sdk_finish

        await reviewer_sdk_finish.verify(
            db,
            owner,
            run,
            binding,
            claim_event=claim_event,
            claim=claim,
            epoch=epoch,
            recovery=previous,
        )
        raise ledger.error("REVIEWER_RECOVERY_ALREADY_FINISHED")
    review_event = await reviewer_lease.review_event(db, attempt.id)
    review = (
        await reviewer_receipt.prove(db, owner, run, attempt) if review_event is not None else None
    )
    if review is not None and binding is None:
        raise ledger.error("HARNESS_BINDING_CHANGED")
    if run.state == "completed":
        await reviewer_finish.domain(db, owner, run, attempt)
    controls = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == run.id,
                ResearchEvent.event_type.in_(
                    ["reviewer.control_requested", "reviewer.control_acknowledged"]
                ),
            )
            .order_by(ResearchEvent.sequence),
            execution_options={"populate_existing": True},
        )
    ).all()
    charges = (
        await db.scalars(
            select(ResearchCharge)
            .where(ResearchCharge.run_id == run.id)
            .order_by(ResearchCharge.id),
            execution_options={"populate_existing": True},
        )
    ).all()
    approvals = (
        await db.scalars(
            select(ResearchApproval)
            .where(ResearchApproval.run_id == run.id)
            .order_by(ResearchApproval.id),
            execution_options={"populate_existing": True},
        )
    ).all()
    if any(row.owner_id != owner for row in charges) or any(
        row.owner_id != owner for row in approvals
    ):
        raise ledger.error("ACCESS_DENIED", 404)
    digest = logical_hash(
        dict(
            workspace_id=str(scope.id),
            run=row_document(run),
            attempt=row_document(attempt),
            lease=row_document(lease),
            claim_event_id=str(claim_event.id),
            claim_hash=claim_event.logical_hash,
            review=row_document(review_event) if review_event is not None else None,
            binding=row_document(binding) if binding is not None else None,
            controls=[row_document(row) for row in controls],
            charges=[row_document(row) for row in charges],
            approvals=[row_document(row) for row in approvals],
        )
    )
    return ReviewerSnapshot(
        run,
        attempt,
        lease,
        claim_event,
        claim,
        digest,
        producer(lease),
        prior_id,
        prior_hash,
        review,
        binding,
    )


async def observe(
    factory, owner: uuid.UUID, run_id: uuid.UUID, worker: WorkerIdentity
) -> DeathObservation:
    observer = observer_value(worker)
    async with factory() as db:
        current = await snapshot(db, owner, run_id)
        state, birth = process_observation(current.producer)
        now = await execution_lease._boundary(db, owner)
        return DeathObservation(
            version=1,
            claim_event_id=str(current.claim_event.id),
            claim_hash=current.claim_event.logical_hash,
            claim=current.claim,
            producer=current.producer,
            previous_recovery_id=current.previous_recovery_id,
            previous_recovery_hash=current.previous_recovery_hash,
            state_hash=current.state_hash,
            observed_at_ms=now,
            observer=observer,
            process_state=state,
            observed_birth=birth,
        )


async def confirm(
    db: AsyncSession, observation: DeathObservation, worker: WorkerIdentity
) -> ReviewerSnapshot:
    """Final owner-fenced fresh check; caller keeps this transaction for recovery."""
    if type(observation) is not DeathObservation or observation.observer != observer_value(worker):
        raise ledger.error("REVIEWER_DEATH_UNPROVEN")
    owner, run_id = uuid.UUID(observation.claim.owner_id), uuid.UUID(observation.claim.run_id)
    current = await snapshot(db, owner, run_id)
    now = await execution_lease._boundary(db, owner)
    if (
        now < observation.observed_at_ms
        or current.claim != observation.claim
        or current.producer != observation.producer
        or current.previous_recovery_id != observation.previous_recovery_id
        or current.previous_recovery_hash != observation.previous_recovery_hash
        or str(current.claim_event.id) != observation.claim_event_id
        or current.claim_event.logical_hash != observation.claim_hash
        or current.state_hash != observation.state_hash
    ):
        raise ledger.error("REVIEWER_DEATH_UNPROVEN")
    # The old dead PID may since have been reused and exited again. Fresh actual
    # proof of that original birth being absent is mandatory; it is not a timer.
    process_observation(current.producer)
    return current
