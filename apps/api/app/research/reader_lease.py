"""Actual reader owner claim, sharing the bounded non-native admission slot."""

import sqlite3

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.exceptions import StatForgeError
from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import ResearchPlanningLease
from app.models.user import User
from app.research import execution_lease, reader_admission, reader_identity
from app.research.contracts import State
from app.research.execution_identity import WorkerIdentity
from app.research.reader_contracts import KIND, ReaderToken
from app.services import research_service as ledger

LEASE_MS = execution_lease.LEASE_MS
HEARTBEAT_MS = execution_lease.HEARTBEAT_MS
ANSWER_EVENT = "reader.answer_receipted"


async def answer_event(db, attempt_id):
    rows = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.entity_id == attempt_id,
                ResearchEvent.event_type == ANSWER_EVENT,
            )
            .limit(2)
        )
    ).all()
    if len(rows) > 1:
        raise ledger.error("READER_RECEIPT_CONFLICT")
    return rows[0] if rows else None


async def source(db, owner, run):
    if run.kind != KIND:
        raise ledger.error("READER_UNSUPPORTED", 422)
    return await reader_admission.source(db, owner, run)


async def claim(db, owner, run_id, expected_revision, worker: WorkerIdentity):
    from app.research.replay import verify_business

    if not worker.validate_current():
        raise ledger.error("EXECUTION_IDENTITY_UNKNOWN")
    now = await execution_lease._boundary(db, owner)
    run, scope = await ledger.run(db, run_id, owner)
    await verify_business(db, owner, scope.id)
    if run.revision != expected_revision or run.state != State.QUEUED.value:
        raise ledger.error("STALE_REVISION")
    await source(db, owner, run)
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
    token = ReaderToken(owner, run.id, attempt.id, worker.worker_id, 1)
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
    value = reader_identity.ReaderClaim(
        version=1,
        owner_id=str(owner),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        input_hash=run.input_hash,
        fence=1,
        worker_id=worker.worker_id,
        producer_host=worker.host,
        producer_pid=worker.pid,
        producer_birth=worker.birth,
        claimed_at_ms=now,
    )
    await ledger._append(
        db, scope, owner, "run", run.id, reader_identity.EVENT, value.model_dump(mode="json")
    )
    await ledger.transition(db, owner, run.id, expected_revision, State.RUNNING)
    return token


async def validate(db, token: ReaderToken, *, running=True, current_source=True):
    if type(token) is not ReaderToken or type(token.fence) is not int or token.fence <= 0:
        raise ledger.error("EXECUTION_FENCE_LOST")
    now = await execution_lease._boundary(db, token.owner_id)
    actor = await db.get(User, token.owner_id, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    lease = await db.get(ResearchPlanningLease, token.attempt_id, populate_existing=True)
    if (
        run.kind != KIND
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
    if token.fence > 1:
        from app.research import reader_recovery

        await reader_recovery.current(db, token.owner_id, run, attempt, lease)
        if current_source:
            raise ledger.error("READER_RECEIPT_ONLY")
    if current_source:
        from app.research import reader_control

        await reader_identity.original(db, token.owner_id, run, attempt, lease)
        if await reader_control.cancelled(db, token.owner_id, run, attempt):
            raise ledger.error("MODEL_CANCELLED")
        if lease.pause_requested or run.state == "paused":
            raise ledger.error("MODEL_PAUSED")
        if await answer_event(db, attempt.id) is not None:
            raise ledger.error("READER_RECEIPT_ONLY")
        await source(db, token.owner_id, run)
    return run, scope, lease, now


async def heartbeat(db, token):
    # Renewal proves ownership, not new source/model/tool authorization. Each
    # operation still performs validate(current_source=True) independently.
    run, _, lease, now = await validate(db, token, running=False, current_source=False)
    if run.state not in {"running", "waiting_approval", "paused", "cancel_requested"}:
        raise ledger.error("EXECUTION_FENCE_LOST")
    lease.heartbeat_at_ms, lease.lease_until_ms = now, now + LEASE_MS
    await db.flush()


async def renew(factory, token, *, allow_completed=False, allow_stopped=False):
    """One renewal plus one retry for a proved, unwritten SQLite BEGIN busy.

    Retrying with a new transaction never renews an expired lease: heartbeat
    still checks the actual current DB time, worker identity and exact fence.
    Other conflicts are not transient authority and are never retried.
    """
    for attempt in range(2):
        async with factory() as db:
            try:
                if allow_completed or allow_stopped:
                    # The supervisor's final HB and business finish share this
                    # owner fence. Never decide to renew from an earlier read.
                    await execution_lease._boundary(db, token.owner_id)
                    run, _ = await ledger.run(db, token.run_id, token.owner_id)
                    if run.state == "completed" and allow_completed:
                        from app.research import reader_finish

                        await reader_finish.terminal_claim(db, token)
                    elif run.state in {"cancelled", "failed", "interrupted"} and allow_stopped:
                        await ack_owner(db, token, stopped=True)
                    else:
                        await heartbeat(db, token)
                else:
                    await heartbeat(db, token)
                await db.commit()
                return
            except StatForgeError as error:
                cause = error.__context__
                if not (
                    attempt == 0
                    and error.error_code == "PREVIEW_WRITE_CONFLICT"
                    and isinstance(cause, OperationalError)
                    and cause.statement == "BEGIN IMMEDIATE"
                    and isinstance(cause.orig, sqlite3.OperationalError)
                    and getattr(cause.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
                    and not (db.new or db.dirty or db.deleted)
                ):
                    raise
                connection = await db.connection()
                raw = await connection.get_raw_connection()
                driver = raw.driver_connection
                if driver is None or driver.in_transaction:
                    raise
                await db.rollback()


async def ack_owner(db, token, *, stopped=False):
    """An idempotent ACK still belongs to the exact actual producer."""
    if type(token) is not ReaderToken or type(token.fence) is not int or token.fence <= 0:
        raise ledger.error("EXECUTION_FENCE_LOST")
    run, _ = await ledger.run(db, token.run_id, token.owner_id)
    terminal = {"cancelled", "failed", "interrupted"} if stopped else {"cancelled"}
    if run.state not in terminal:
        await validate(db, token, running=False, current_source=False)
        return
    now = await execution_lease._boundary(db, token.owner_id)
    actor = await db.get(User, token.owner_id, populate_existing=True)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    lease = await db.get(ResearchPlanningLease, token.attempt_id, populate_existing=True)
    if (
        actor is None
        or not actor.is_active
        or run.kind != KIND
        or attempt is None
        or attempt.run_id != run.id
        or attempt.number != run.attempt_count
        or attempt.state != run.state
        or lease is None
        or lease.owner_id != token.owner_id
        or lease.worker_id != token.worker_id
        or lease.fence != token.fence
        or lease.active_owner_id is not None
        or lease.released_at_ms is None
        or lease.released_at_ms > now
        or not WorkerIdentity(
            token.worker_id, lease.producer_host, lease.producer_pid, lease.producer_birth
        ).validate_current()
    ):
        raise ledger.error("EXECUTION_FENCE_LOST")
    await prove_stop(db, token.owner_id, run, lease)


async def prove_stop(db, owner, run, lease):
    """Release fields alone are never a Reader terminal acknowledgement."""
    from app.research.contracts import logical_hash, parse_document

    _, scope = await ledger.run(db, run.id, owner)
    events = (
        await db.scalars(
            select(ResearchEvent).where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == run.id,
                ResearchEvent.event_type == "reader.stopped",
            )
        )
    ).all()
    matching = [
        row
        for row in events
        if parse_document(row.payload_json).get("attempt_id") == str(lease.attempt_id)
    ]
    if len(matching) != 1:
        raise ledger.error("READER_STOP_UNPROVEN")
    event = matching[0]
    value = parse_document(event.payload_json)
    fields = {
        "attempt_id",
        "fence",
        "worker_id",
        "producer_host",
        "producer_pid",
        "producer_birth",
        "state",
        "proof",
        "released_at_ms",
    }
    if value.get("proof") == "verified_death":
        fields |= {"death_event_id", "death_hash"}
    if (
        event.actor_id != owner
        or event.entity_kind != "run"
        or event.logical_hash != logical_hash(value)
        or set(value) != fields
        or type(value["fence"]) is not int
        or type(value["producer_pid"]) is not int
        or type(value["released_at_ms"]) is not int
        or lease.owner_id != owner
        or lease.active_owner_id is not None
        or lease.released_at_ms is None
        or value.get("released_at_ms") != lease.released_at_ms
        or any(
            value.get(name) != getattr(lease, name)
            for name in ("fence", "worker_id", "producer_host", "producer_pid", "producer_birth")
        )
        or value.get("proof") not in {"owned_boundary", "verified_death"}
        or value.get("state") not in {"failed", "cancelled", "interrupted"}
    ):
        raise ledger.error("READER_STOP_UNPROVEN")
    if value["proof"] == "verified_death":
        from app.research import reader_death_stop

        attempt = await db.get(ResearchAttempt, lease.attempt_id, populate_existing=True)
        await reader_death_stop.prove(db, owner, run, attempt, lease, event, value)
    return value


async def stop_owned(db, token, after):
    """Called only after the owned SDK/transport has drained, not on user request."""
    from app.models.research_security import ResearchCharge
    from app.research import reader_control, reader_transport
    from app.research.contracts import require_transition

    if after not in {State.FAILED, State.CANCELLED, State.INTERRUPTED}:
        raise ledger.error("ILLEGAL_TRANSITION")
    run, scope, lease, now = await validate(db, token, running=False, current_source=False)
    reader_transport.drained(token)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    if after == State.CANCELLED:
        if not await reader_control.cancelled(db, token.owner_id, run, attempt):
            raise ledger.error("READER_STOP_UNPROVEN")
    elif await answer_event(db, token.attempt_id) is not None:
        raise ledger.error("READER_RECEIPT_ONLY")
    try:
        require_transition(State(run.state), after)
    except ValueError:
        raise ledger.error("ILLEGAL_TRANSITION") from None
    if (
        await db.scalar(
            select(ResearchCharge.id).where(
                ResearchCharge.run_id == run.id, ResearchCharge.state == "dispatched"
            )
        )
        is not None
    ):
        raise ledger.error("READER_STOP_UNPROVEN")
    value = dict(
        attempt_id=str(attempt.id),
        fence=lease.fence,
        worker_id=lease.worker_id,
        producer_host=lease.producer_host,
        producer_pid=lease.producer_pid,
        producer_birth=lease.producer_birth,
        state=after.value,
        proof="owned_boundary",
        released_at_ms=now,
    )
    # Unknown reservations stay intact; stopping never guesses the financial outcome.
    lease.active_owner_id, lease.released_at_ms, lease.pause_requested = None, now, False
    await ledger._append(db, scope, token.owner_id, "run", run.id, "reader.stopped", value)
    await ledger.transition(db, token.owner_id, run.id, run.revision, after)
    await db.flush()
    return run
