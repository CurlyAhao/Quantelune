"""Transactional internal execution claim/fencing, separate from business state."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import ResearchAttempt, ResearchRun
from app.models.research_execution import ResearchExecutionLease
from app.models.user import User
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import State, logical_hash, parse_document
from app.research.execution_identity import WorkerIdentity, producer_dead
from app.services import research_service as ledger

LEASE_MS = 15_000
HEARTBEAT_MS = 3_000
_ADMISSION_KEY = 0x53464F5247453039


@dataclass(frozen=True)
class ExecutionToken:
    owner_id: uuid.UUID
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    worker_id: str
    fence: int
    origin_fence: int | None = None


async def _boundary(db: AsyncSession, owner: uuid.UUID) -> int:
    await acquire_write_fence(db, owner)
    connection = await db.connection()
    if connection.dialect.name == "postgresql":
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMISSION_KEY}
        )
        result = await connection.exec_driver_sql(
            "SELECT floor(extract(epoch from clock_timestamp()) * 1000)::bigint"
        )
    else:
        result = await connection.exec_driver_sql(
            "SELECT CAST((julianday('now') - 2440587.5)*86400000 AS INTEGER)"
        )
    return result.scalar_one()


async def _source(
    db: AsyncSession, owner: uuid.UUID, item: ResearchRun, *, mode="execution"
) -> None:
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    captured = parse_document(item.input_json)
    if (
        logical_hash(captured) != item.input_hash
        or captured.get("kind") != item.kind
        or captured.get("attachments")
        != await ledger.bindings(db, owner, item.session_id, mode=mode)
    ):
        raise ledger.error("BINDING_CHANGED")


async def claim(
    db: AsyncSession,
    owner: uuid.UUID,
    run_id: uuid.UUID,
    expected_revision: int,
    worker: WorkerIdentity,
) -> ExecutionToken:
    try:
        valid = worker.validate_current()
    except (OSError, ValueError, RuntimeError):
        valid = False
    if not valid:
        raise ledger.error("EXECUTION_IDENTITY_UNKNOWN")
    now = await _boundary(db, owner)
    item, _scope = await ledger.run(db, run_id, owner)
    if item.revision != expected_revision or item.state != State.QUEUED.value:
        raise ledger.error("STALE_REVISION")
    await _source(db, owner, item, mode="live")
    from app.research.plan_execution import authorize_child

    await authorize_child(db, owner, item, stage="claim")
    document = parse_document(item.input_json)
    if document["parameters"].get("operation") != "execute" or item.kind not in {
        "quality",
        "statistics",
        "ml",
        "workflow",
    }:
        raise ledger.error("EXECUTION_UNSUPPORTED", 422)
    active = await db.scalar(
        select(ResearchExecutionLease.attempt_id).where(ResearchExecutionLease.active_slot == 1)
    )
    if active is not None:
        raise ledger.error("EXECUTION_BUSY", 429)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == item.id, ResearchAttempt.number == item.attempt_count
        )
    )
    if attempt is None or attempt.state != State.QUEUED.value:
        raise ledger.error("STATE_INCONSISTENT")
    previous = await db.get(ResearchExecutionLease, attempt.id, populate_existing=True)
    if previous is not None:
        # A queued attempt cannot silently reuse a former completed computation.
        raise ledger.error("EXECUTION_REUSE_UNPROVEN")
    from app.models.research_execution import ResearchExecutionDispatch, ResearchExecutionIntent

    if (
        await db.get(ResearchExecutionIntent, attempt.id, populate_existing=True) is not None
        or await db.get(ResearchExecutionDispatch, attempt.id, populate_existing=True) is not None
    ):
        raise ledger.error("EXECUTION_REUSE_UNPROVEN")
    token = ExecutionToken(owner, item.id, attempt.id, worker.worker_id, 1)
    db.add(
        ResearchExecutionLease(
            attempt_id=attempt.id,
            worker_id=worker.worker_id,
            fence=1,
            producer_host=worker.host,
            producer_pid=worker.pid,
            producer_birth=worker.birth,
            claimed_at_ms=now,
            heartbeat_at_ms=now,
            lease_until_ms=now + LEASE_MS,
            active_slot=1,
        )
    )
    await ledger.transition(db, owner, item.id, expected_revision, State.RUNNING)
    return token


async def validate(db: AsyncSession, token: ExecutionToken, *, running: bool = True):
    if (
        type(token.fence) is not int
        or token.fence <= 0
        or (
            token.origin_fence is not None
            and (type(token.origin_fence) is not int or token.origin_fence <= 0)
        )
    ):
        raise ledger.error("EXECUTION_FENCE_LOST")
    now = await _boundary(db, token.owner_id)
    item, scope = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    lease = await db.get(ResearchExecutionLease, token.attempt_id, populate_existing=True)
    if (
        attempt is None
        or lease is None
        or attempt.run_id != item.id
        or attempt.number != item.attempt_count
        or lease.worker_id != token.worker_id
        or lease.fence != token.fence
        or lease.active_slot != 1
        or lease.released_at_ms is not None
        or not lease.heartbeat_at_ms <= now < lease.lease_until_ms
        or (running and (item.state != State.RUNNING.value or attempt.state != item.state))
    ):
        raise ledger.error("EXECUTION_FENCE_LOST")
    holder = WorkerIdentity(
        token.worker_id, lease.producer_host, lease.producer_pid, lease.producer_birth
    )
    if not holder.validate_current():
        raise ledger.error("EXECUTION_FENCE_LOST")
    if lease.result_only:
        from app.models.research_execution import ResearchExecutionIntent

        intent = await db.get(ResearchExecutionIntent, token.attempt_id, populate_existing=True)
        if intent is None or token.origin_fence != intent.fence or token.fence <= intent.fence:
            raise ledger.error("EXECUTION_FENCE_LOST")
    elif token.origin_fence is not None:
        raise ledger.error("EXECUTION_FENCE_LOST")
    if running:
        await _source(db, token.owner_id, item)
    return item, scope, lease, now


async def heartbeat(db: AsyncSession, token: ExecutionToken) -> None:
    item, _scope, lease, now = await validate(db, token, running=False)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    if (
        item.state not in {State.RUNNING.value, State.WAITING_APPROVAL.value, State.PAUSED.value}
        or (item.state == State.PAUSED.value and not lease.pause_requested)
        or attempt is None
        or attempt.state != item.state
    ):
        raise ledger.error("EXECUTION_FENCE_LOST")
    await _source(db, token.owner_id, item)
    lease.heartbeat_at_ms = now
    lease.lease_until_ms = now + LEASE_MS
    await db.flush()


async def reconcile_dead(db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID) -> bool:
    now = await _boundary(db, owner)
    item, scope = await ledger.run(db, run_id, owner)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == item.id, ResearchAttempt.number == item.attempt_count
        )
    )
    if attempt is None:
        raise ledger.error("STATE_INCONSISTENT")
    lease = await db.get(ResearchExecutionLease, attempt.id, populate_existing=True)
    if lease is None or lease.active_slot != 1:
        return False
    if now < lease.heartbeat_at_ms or not producer_dead(
        lease.producer_host, lease.producer_pid, lease.producer_birth
    ):
        return False
    from app.models.research_execution import ResearchExecutionDispatch, ResearchExecutionIntent
    from app.research.execution_quiescence import native_quiescent

    intent = await db.get(ResearchExecutionIntent, attempt.id, populate_existing=True)
    dispatch = await db.get(ResearchExecutionDispatch, attempt.id, populate_existing=True)
    origin = intent.fence if lease.result_only and intent is not None else None
    token = ExecutionToken(owner, item.id, attempt.id, lease.worker_id, lease.fence, origin)
    if not native_quiescent(intent, dispatch, token, lease.producer_host, item.input_hash):
        return False
    # Recovery of durable results/effects will run before this fallback; no
    # disappearance can be turned into business completed without real proof.
    if item.state not in {
        State.RUNNING.value,
        State.WAITING_APPROVAL.value,
        State.PAUSED.value,
        State.CANCEL_REQUESTED.value,
    }:
        raise ledger.error("STATE_INCONSISTENT")
    await ledger.transition(db, owner, item.id, item.revision, State.INTERRUPTED)
    attempt.error_code = "EXECUTION_PRODUCER_EXITED"
    lease.active_slot = None
    lease.released_at_ms = now
    await ledger._append(
        db,
        scope,
        owner,
        "run",
        item.id,
        "execution.producer_exited",
        {"attempt_id": str(attempt.id), "fence": lease.fence},
    )
    return True
