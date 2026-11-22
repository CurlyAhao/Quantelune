"""Owner-scoped durable control, with pause acknowledged by the actual worker."""

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_execution import ResearchExecutionLease
from app.research.contracts import State
from app.research.execution_lease import _boundary, _source, validate
from app.services import research_service as ledger


async def request(db, owner, run_id, revision: int, action: str):
    await _boundary(db, owner)
    run, scope = await ledger.run(db, run_id, owner)
    if run.revision != revision:
        raise ledger.error("STALE_REVISION")
    if action == "resume":
        await ledger.session(db, run.session_id, owner, mode="live")
    await _source(db, owner, run)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    if attempt is None or attempt.state != run.state:
        raise ledger.error("STATE_INCONSISTENT")
    lease = await db.get(ResearchExecutionLease, attempt.id, populate_existing=True)
    if run.state == State.WAITING_APPROVAL.value:
        from app.models.research_execution import ResearchExecutionDispatch, ResearchExecutionIntent
        from app.research import assistant_invocation

        if (
            lease is None
            or lease.active_slot != 1
            or lease.released_at_ms is not None
            or await assistant_invocation.load(db, owner, run, attempt) is None
            or await db.get(ResearchExecutionIntent, attempt.id) is not None
            or await db.get(ResearchExecutionDispatch, attempt.id) is not None
        ):
            raise ledger.error("EXECUTION_CONTROL_UNPROVEN")
        if action == "cancel":
            lease.pause_requested = False
            return await ledger.transition(db, owner, run_id, revision, State.CANCELLED)
        if action not in {"pause", "resume"}:
            raise ledger.error("ILLEGAL_TRANSITION")
        requested = action == "pause"
        if lease.pause_requested == requested:
            return run
        lease.pause_requested = requested
        await ledger._append(
            db,
            scope,
            owner,
            "run",
            run.id,
            "harness.wait_control",
            {"attempt_id": str(attempt.id), "revision": run.revision, "action": action},
        )
        await db.flush()
        return run  # Approval revision stays exact; control is independently audited.
    if action == "cancel":
        if run.state == State.QUEUED.value:
            return await ledger.transition(db, owner, run_id, revision, State.CANCELLED)
        if run.state not in {State.RUNNING.value, State.PAUSED.value}:
            raise ledger.error("ILLEGAL_TRANSITION")
        if lease is not None:
            lease.pause_requested = False
        # Cancellation is a stop request, never a temporary authorization to
        # resume computation. It is also available on retained conversations.
        return await ledger.transition(db, owner, run_id, revision, State.CANCEL_REQUESTED)
    if lease is None or lease.active_slot != 1 or lease.released_at_ms is not None:
        raise ledger.error("EXECUTION_CONTROL_UNPROVEN")
    if action == "pause" and run.state == State.RUNNING.value:
        if lease.pause_requested:
            return run
        lease.pause_requested = True
        run.revision += 1
        await ledger._append(
            db,
            scope,
            owner,
            "run",
            run.id,
            "execution.pause_requested",
            {"attempt_id": str(attempt.id), "revision": run.revision},
        )
    elif action == "resume" and run.state == State.PAUSED.value:
        lease.pause_requested = False
        await ledger.transition(db, owner, run_id, revision, State.RUNNING)
    else:
        raise ledger.error("ILLEGAL_TRANSITION")
    await db.flush()
    return run


async def acknowledge_pause(db, token) -> bool:
    run, _scope, lease, _now = await validate(db, token)
    if not lease.pause_requested:
        return False
    await ledger.transition(db, token.owner_id, run.id, run.revision, State.PAUSED)
    return True
