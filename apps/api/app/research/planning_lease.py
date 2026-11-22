"""Bounded real planner claims, separate from native execution admission."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_planning import ResearchPlanningLease, ResearchPlanReceipt
from app.models.research_security import ResearchCharge
from app.research import execution_lease
from app.research.contracts import State, parse_document
from app.research.execution_identity import WorkerIdentity, producer_dead
from app.services import research_context_service as contexts
from app.services import research_service as ledger

LEASE_MS = execution_lease.LEASE_MS
HEARTBEAT_MS = execution_lease.HEARTBEAT_MS


@dataclass(frozen=True)
class PlanningToken:
    owner_id: uuid.UUID
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    worker_id: str
    fence: int


async def source(db, owner, run, *, live=True):
    from app.research.replay import verify_business

    _, scope = await ledger.run(db, run.id, owner)
    parameters = parse_document(run.input_json)["parameters"]
    from app.research import planner_v2

    protocol = planner_v2.from_parameters(parameters)
    if (
        run.kind != "planner"
        or parameters["operation"] != "plan"
        or not isinstance(parameters["source_version"], str)
        or len(parameters["source_version"]) != 64
    ):
        raise ledger.error("PLANNING_UNSUPPORTED", 422)
    if live and protocol is not None:
        await execution_lease._source(db, owner, run, mode="live")
        # The public live proof owns the complete ledger/active-Protocol checks
        # and ends with a real source snapshot. Its source version is the same
        # one checked by from_parameters; no caller-provided proof or cache.
        await planner_v2.live(db, owner, run)
        return
    await verify_business(db, owner, scope.id)
    await execution_lease._source(db, owner, run, mode="live" if live else "execution")
    # New model/tool work always needs the current source generation. A separate
    # receipt proof will authorize already committed historical closeout.
    await contexts.snapshot(
        db, owner, run.session_id, expected_source_version=parameters["source_version"]
    )
    if live:
        await planner_v2.live(db, owner, run)


async def start(
    db, owner, session_id, idempotency_key, source_version, *, protocol=None, wire_version=None
):
    from app.research.replay import verify_business

    await execution_lease._boundary(db, owner)
    _, scope = await ledger.session(db, session_id, owner, mode="live")
    await verify_business(db, owner, scope.id)
    await contexts.snapshot(db, owner, session_id, expected_source_version=source_version)
    from app.research import plan_versioning, planner_v2

    parameters: dict = {"operation": "plan", "source_version": source_version}
    if protocol is not None:
        parameters.update(planner_version=planner_v2.VERSION, protocol=protocol)
        if wire_version is not None:
            parameters["planner_wire_version"] = wire_version
        planner_v2.from_parameters(parameters)
    elif wire_version is not None:
        raise ledger.error("PLANNING_PROTOCOL_REQUIRED")
    elif await plan_versioning.enabled(db, owner, session_id):
        raise ledger.error("PLANNING_PROTOCOL_REQUIRED")
    return await ledger.start_run(
        db,
        owner,
        session_id,
        "planner",
        idempotency_key,
        parameters,
    )


async def claim(db, owner, run_id, expected_revision, worker: WorkerIdentity):
    if not worker.validate_current():
        raise ledger.error("EXECUTION_IDENTITY_UNKNOWN")
    now = await execution_lease._boundary(db, owner)
    run, _ = await ledger.run(db, run_id, owner)
    if run.revision != expected_revision or run.state != State.QUEUED.value:
        raise ledger.error("STALE_REVISION")
    await source(db, owner, run)
    from app.research import planner_admission, planner_v2

    if (
        planner_v2.captured(run) is not None
        and await planner_admission.load(db, owner, run) is None
    ):
        raise ledger.error("PLANNING_ADMISSION_REQUIRED")
    if (
        await db.scalar(
            select(ResearchPlanningLease.attempt_id).where(
                ResearchPlanningLease.active_owner_id == owner
            )
        )
        is not None
    ):
        raise ledger.error("PLANNING_BUSY", 429)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    if attempt is None or attempt.state != run.state:
        raise ledger.error("STATE_INCONSISTENT")
    if await db.get(ResearchPlanningLease, attempt.id, populate_existing=True) is not None:
        raise ledger.error("EXECUTION_REUSE_UNPROVEN")
    token = PlanningToken(owner, run.id, attempt.id, worker.worker_id, 1)
    db.add(
        ResearchPlanningLease(
            attempt_id=attempt.id,
            owner_id=owner,
            worker_id=worker.worker_id,
            fence=1,
            producer_host=worker.host,
            producer_pid=worker.pid,
            producer_birth=worker.birth,
            claimed_at_ms=now,
            heartbeat_at_ms=now,
            lease_until_ms=now + LEASE_MS,
            active_owner_id=owner,
        )
    )
    await ledger.transition(db, owner, run.id, expected_revision, State.RUNNING)
    return token


async def validate(db, token: PlanningToken, *, running=True, current_source=True):
    if type(token.fence) is not int or token.fence <= 0:
        raise ledger.error("EXECUTION_FENCE_LOST")
    now = await execution_lease._boundary(db, token.owner_id)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    lease = await db.get(ResearchPlanningLease, token.attempt_id, populate_existing=True)
    if (
        run.kind != "planner"
        or attempt is None
        or lease is None
        or lease.owner_id != token.owner_id
        or attempt.run_id != run.id
        or attempt.number != run.attempt_count
        or attempt.state != run.state
        or lease.worker_id != token.worker_id
        or lease.fence != token.fence
        or lease.active_owner_id != token.owner_id
        or lease.released_at_ms is not None
        or not lease.heartbeat_at_ms <= now < lease.lease_until_ms
        or (running and run.state != State.RUNNING.value)
    ):
        raise ledger.error("EXECUTION_FENCE_LOST")
    if not WorkerIdentity(
        token.worker_id, lease.producer_host, lease.producer_pid, lease.producer_birth
    ).validate_current():
        raise ledger.error("EXECUTION_FENCE_LOST")
    if current_source:
        receipt = await db.scalar(
            select(ResearchPlanReceipt).where(ResearchPlanReceipt.attempt_id == token.attempt_id)
        )
        if receipt is not None and receipt.fence < token.fence:
            raise ledger.error("PLANNING_RECEIPT_ONLY")
        await source(db, token.owner_id, run)
        # A long proof cannot authorize work using only its starting DB time.
        now = await execution_lease._boundary(db, token.owner_id)
        if not lease.heartbeat_at_ms <= now < lease.lease_until_ms:
            raise ledger.error("EXECUTION_FENCE_LOST")
    return run, scope, lease, now


async def heartbeat(db, token):
    run, _, lease, now = await validate(db, token, running=False)
    if run.state not in {State.RUNNING.value, State.WAITING_APPROVAL.value, State.PAUSED.value}:
        raise ledger.error("EXECUTION_FENCE_LOST")
    lease.heartbeat_at_ms = now
    lease.lease_until_ms = now + LEASE_MS
    await db.flush()


async def stop_owned(db, token: PlanningToken, after: State):
    """Worker acknowledgment at a drained SDK boundary, never a user stop request."""
    if after not in {State.FAILED, State.CANCELLED, State.INTERRUPTED}:
        raise ledger.error("ILLEGAL_TRANSITION")
    run, scope, lease, now = await validate(db, token, running=False, current_source=False)
    await _stop(db, token.owner_id, run, scope, lease, now, after, "owned_boundary")
    return run


async def _stop(db, owner, run, scope, lease, now, after, proof):
    from app.research.contracts import require_transition

    if (
        await db.scalar(
            select(ResearchPlanReceipt.id).where(ResearchPlanReceipt.attempt_id == lease.attempt_id)
        )
        is not None
    ):
        raise ledger.error("PLANNING_RECEIPT_PENDING")
    try:
        require_transition(State(run.state), after)
    except ValueError:
        raise ledger.error("ILLEGAL_TRANSITION") from None
    # A live dispatch may still hold a transport. Unknown outcomes keep their
    # financial reservations and block same-Run retry; never settle/release them here.
    dispatched = await db.scalar(
        select(ResearchCharge.id).where(
            ResearchCharge.run_id == run.id, ResearchCharge.state == "dispatched"
        )
    )
    if dispatched is not None and proof != "producer_dead":
        raise ledger.error("PLANNING_STOP_UNPROVEN")
    value = dict(
        attempt_id=str(lease.attempt_id),
        fence=lease.fence,
        worker_id=lease.worker_id,
        producer_host=lease.producer_host,
        producer_pid=lease.producer_pid,
        producer_birth=lease.producer_birth,
        state=after.value,
        proof=proof,
        released_at_ms=now,
    )
    lease.active_owner_id = None
    lease.released_at_ms = now
    lease.pause_requested = False
    await ledger._append(db, scope, owner, "run", run.id, "planning.stopped", value)
    await ledger.transition(db, owner, run.id, run.revision, after)
    await db.flush()


async def reconcile_dead(db, owner, run_id):
    now = await execution_lease._boundary(db, owner)
    run, scope = await ledger.run(db, run_id, owner)
    if run.kind != "planner":
        raise ledger.error("PLANNING_UNSUPPORTED")
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    lease = (
        await db.get(ResearchPlanningLease, attempt.id, populate_existing=True) if attempt else None
    )
    if (
        lease is None
        or lease.active_owner_id != owner
        or now < lease.heartbeat_at_ms
        or not producer_dead(lease.producer_host, lease.producer_pid, lease.producer_birth)
    ):
        return False
    if (
        await db.scalar(
            select(ResearchPlanReceipt.id).where(ResearchPlanReceipt.attempt_id == lease.attempt_id)
        )
        is not None
    ):
        return False  # Durable receipt needs receipt-only recovery, never interruption/recompute.
    await _stop(db, owner, run, scope, lease, now, State.INTERRUPTED, "producer_dead")
    return True


async def recover_receipt(db, owner, run_id, worker: WorkerIdentity):
    """Observed producer death grants receipt-only closeout, never a model retry."""
    from app.research import plan_receipt
    from app.research.replay import verify_business

    if not worker.validate_current():
        raise ledger.error("EXECUTION_IDENTITY_UNKNOWN")
    now = await execution_lease._boundary(db, owner)
    run, scope = await ledger.run(db, run_id, owner)
    await plan_receipt._actor(db, owner)
    await verify_business(db, owner, scope.id)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    lease = (
        await db.get(ResearchPlanningLease, attempt.id, populate_existing=True) if attempt else None
    )
    if (
        lease is None
        or lease.active_owner_id != owner
        or lease.released_at_ms is not None
        or run.state != State.RUNNING.value
        or lease.pause_requested
        or now < lease.heartbeat_at_ms
        or not producer_dead(lease.producer_host, lease.producer_pid, lease.producer_birth)
    ):
        raise ledger.error("PLANNING_RECOVERY_UNPROVEN")
    receipt = await plan_receipt.prove(db, owner, run, attempt)
    value = dict(
        attempt_id=str(attempt.id),
        receipt_id=str(receipt.id),
        receipt_hash=receipt.document_hash,
        old_fence=lease.fence,
        old_worker_id=lease.worker_id,
        old_producer_host=lease.producer_host,
        old_producer_pid=lease.producer_pid,
        old_producer_birth=lease.producer_birth,
        fence=lease.fence + 1,
        worker_id=worker.worker_id,
        producer_host=worker.host,
        producer_pid=worker.pid,
        producer_birth=worker.birth,
    )
    lease.fence += 1
    lease.worker_id, lease.producer_host, lease.producer_pid, lease.producer_birth = (
        worker.worker_id,
        worker.host,
        worker.pid,
        worker.birth,
    )
    lease.heartbeat_at_ms, lease.lease_until_ms = now, now + LEASE_MS
    await ledger._append(db, scope, owner, "run", run.id, "planning.receipt_recovered", value)
    await db.flush()
    return PlanningToken(owner, run.id, attempt.id, worker.worker_id, lease.fence)
