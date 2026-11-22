"""Reader business closeout proves a saved Answer, never an SDK END."""

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_planning import ResearchPlanningLease
from app.models.research_security import ResearchCharge
from app.research import execution_lease, reader_answer, reader_lease
from app.research.contracts import State
from app.research.execution_identity import WorkerIdentity
from app.research.reader_contracts import ReaderToken
from app.services import research_service as ledger


async def domain(db, owner, run, attempt):
    receipt = await reader_answer.prove(db, owner, run, attempt)
    if (
        run.kind != "reader"
        or run.domain_kind != "reader_answer"
        or run.domain_run_id != receipt.id
        or attempt.domain_kind != "reader_answer"
        or attempt.domain_run_id != receipt.id
    ):
        raise ledger.error("READER_COMPLETION_UNPROVEN")
    return receipt


async def terminal_claim(db, token):
    """Released claim allows only the same owned historical closeout."""
    if type(token) is not ReaderToken or type(token.fence) is not int or token.fence <= 0:
        raise ledger.error("EXECUTION_FENCE_LOST")
    now = await execution_lease._boundary(db, token.owner_id)
    run, _ = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    lease = await db.get(ResearchPlanningLease, token.attempt_id, populate_existing=True)
    receipt = await domain(db, token.owner_id, run, attempt)
    if (
        run.state != State.COMPLETED.value
        or attempt.state != run.state
        or attempt.number != run.attempt_count
        or run.completion_hash != receipt.document_hash
        or attempt.completion_hash != receipt.document_hash
        or lease is None
        or lease.owner_id != token.owner_id
        or lease.worker_id != token.worker_id
        or lease.fence != token.fence
        or lease.active_owner_id is not None
        or lease.released_at_ms is None
        or lease.released_at_ms > now
        or lease.pause_requested
        or not WorkerIdentity(
            token.worker_id, lease.producer_host, lease.producer_pid, lease.producer_birth
        ).validate_current()
    ):
        raise ledger.error("READER_COMPLETION_UNPROVEN")
    if token.fence > 1:
        from app.research import reader_recovery

        await reader_recovery.current(db, token.owner_id, run, attempt, lease)
    return receipt


async def finish(db, token):
    from app.research.replay import verify_business

    if type(token) is not ReaderToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    await execution_lease._boundary(db, token.owner_id)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    await verify_business(db, token.owner_id, scope.id)
    if run.state == State.COMPLETED.value:
        return await terminal_claim(db, token)
    run, _, lease, _ = await reader_lease.validate(db, token, current_source=False)
    from app.research import reader_control

    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    if lease.pause_requested or await reader_control.cancelled(db, token.owner_id, run, attempt):
        raise ledger.error("READER_COMPLETION_UNPROVEN")
    if (
        await db.scalar(
            select(ResearchCharge.id).where(
                ResearchCharge.run_id == run.id,
                ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
            )
        )
        is not None
    ):
        raise ledger.error("READER_COMPLETION_FINANCIAL_PENDING")
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    receipt = await reader_answer.prove(db, token.owner_id, run, attempt)
    run.domain_kind, run.domain_run_id = "reader_answer", receipt.id
    await ledger.transition(db, token.owner_id, run.id, run.revision, State.COMPLETED)
    return receipt
