"""Only a server-recorded never-dispatched stop may bypass uncommitted Intent."""

from app.models.research_execution import ResearchNoDispatchReceipt
from app.research.execution_lease import ExecutionToken
from app.research.execution_quiescence import stage_not_created


async def verified(db, owner, run, attempt, lease, intent) -> bool:
    receipt = await db.get(ResearchNoDispatchReceipt, attempt.id, populate_existing=True)
    if (
        receipt is None
        or lease is None
        or intent is None
        or receipt.owner_id != owner
        or receipt.input_hash != run.input_hash
        or receipt.intent_hash != intent.intent_hash
        or receipt.state != attempt.state
        or attempt.state not in {"cancelled", "interrupted"}
        or lease.active_slot is not None
        or lease.result_only
        or lease.released_at_ms is None
        or receipt.released_at_ms != lease.released_at_ms
        or (
            receipt.fence,
            receipt.worker_id,
            receipt.producer_host,
            receipt.producer_pid,
            receipt.producer_birth,
        )
        != (
            lease.fence,
            lease.worker_id,
            lease.producer_host,
            lease.producer_pid,
            lease.producer_birth,
        )
    ):
        return False
    token = ExecutionToken(owner, run.id, attempt.id, lease.worker_id, lease.fence)
    return stage_not_created(intent, token, run.input_hash)
