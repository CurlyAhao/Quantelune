"""Reviewer business closeout proves a saved Review, never an SDK END."""

from app.models.research import ResearchAttempt
from app.models.research_planning import ResearchPlanningLease
from app.research import execution_lease, reviewer_receipt
from app.research.contracts import State
from app.research.execution_identity import WorkerIdentity
from app.research.reviewer_contracts import ReviewerToken
from app.services import research_service as ledger


async def domain(db, owner, run, attempt):
    receipt = await reviewer_receipt.prove(db, owner, run, attempt)
    if (
        run.kind != "reviewer"
        or run.domain_kind != "reviewer_receipt"
        or run.domain_run_id != receipt.id
        or attempt.domain_kind != "reviewer_receipt"
        or attempt.domain_run_id != receipt.id
    ):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    return receipt


async def terminal_claim(db, token):
    """Released claim allows only the same owned historical closeout."""
    if type(token) is not ReviewerToken or type(token.fence) is not int or token.fence <= 0:
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
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    if token.fence > 1:
        from app.research import reviewer_recovery

        await reviewer_recovery.current(db, token.owner_id, run, attempt, lease)
    from app.research import reviewer_completion
    from app.research.replay import verify_business

    await reviewer_completion.terminal(db, token.owner_id, run, attempt, lease)
    _, scope = await ledger.run(db, run.id, token.owner_id)
    await verify_business(db, token.owner_id, scope.id)
    return receipt


async def finish(db, token):
    if type(token) is not ReviewerToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    await execution_lease._boundary(db, token.owner_id)
    run, _ = await ledger.run(db, token.run_id, token.owner_id)
    if run.state == State.COMPLETED.value:
        return await terminal_claim(db, token)
    from app.research import reviewer_completion

    _, _, receipt = await reviewer_completion.observe(db, token)
    run.domain_kind, run.domain_run_id = "reviewer_receipt", receipt.id
    await ledger.transition(db, token.owner_id, run.id, run.revision, State.COMPLETED)
    return receipt
