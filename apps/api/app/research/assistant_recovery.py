"""Same-attempt formal recovery only before any native side effect exists."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_execution import (
    ResearchExecutionDispatch,
    ResearchExecutionIntent,
    ResearchExecutionLease,
    ResearchPublicationReceipt,
)
from app.models.research_security import ResearchCharge
from app.research import advisory_receipt, assistant_invocation, execution_lease, model_request
from app.research.contracts import State
from app.research.execution_identity import producer_dead
from app.services import research_gateway_service as gateways
from app.services import research_security_service as security
from app.services import research_service as ledger

EVENT = "harness.assistant_recovered"


async def acquire(factory, owner, run_id, revision, worker):
    if not worker.validate_current():
        raise ledger.error("EXECUTION_IDENTITY_UNKNOWN")
    async with factory() as db:
        now = await execution_lease._boundary(db, owner)
        run, scope = await ledger.run(db, run_id, owner)
        attempt = await db.scalar(
            select(ResearchAttempt)
            .where(ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count)
            .execution_options(populate_existing=True)
        )
        if attempt is None:
            raise ledger.error("STATE_INCONSISTENT")
        lease = await db.get(ResearchExecutionLease, attempt.id, populate_existing=True)
        if (
            run.revision != revision
            or run.state
            not in {State.RUNNING.value, State.WAITING_APPROVAL.value, State.PAUSED.value}
            or attempt.state != run.state
            or lease is None
            or lease.active_slot != 1
            or lease.released_at_ms is not None
            or lease.result_only
            or not producer_dead(lease.producer_host, lease.producer_pid, lease.producer_birth)
        ):
            raise ledger.error("ASSISTANT_RECOVERY_UNPROVEN")
        for model in (
            ResearchExecutionIntent,
            ResearchExecutionDispatch,
            ResearchPublicationReceipt,
        ):
            if await db.get(model, attempt.id, populate_existing=True) is not None:
                raise ledger.error("ASSISTANT_RECOVERY_NATIVE_PHASE")
        await execution_lease._source(db, owner, run)
        await assistant_invocation.verify_current(db, owner, run, attempt)
        await ledger.verify_events(db, owner, scope.id)
        await verify_financial(db, owner, run, attempt, scope)
        old_fence = lease.fence
        document = {
            "attempt_id": str(attempt.id),
            "old_fence": old_fence,
            "fence": old_fence + 1,
            "old_worker_id": lease.worker_id,
            "old_producer_host": lease.producer_host,
            "old_producer_pid": lease.producer_pid,
            "old_producer_birth": lease.producer_birth,
            "worker_id": worker.worker_id,
            "producer_host": worker.host,
            "producer_pid": worker.pid,
            "producer_birth": worker.birth,
        }
        if not worker.validate_current() or not producer_dead(
            lease.producer_host, lease.producer_pid, lease.producer_birth
        ):
            raise ledger.error("ASSISTANT_RECOVERY_UNPROVEN")
        lease.worker_id, lease.fence = worker.worker_id, old_fence + 1
        lease.producer_host, lease.producer_pid, lease.producer_birth = (
            worker.host,
            worker.pid,
            worker.birth,
        )
        lease.heartbeat_at_ms, lease.lease_until_ms = now, now + execution_lease.LEASE_MS
        await ledger._append(db, scope, owner, "run", run.id, EVENT, document)
        await db.commit()
        return execution_lease.ExecutionToken(
            owner, run.id, attempt.id, worker.worker_id, lease.fence
        )


async def charges_for_attempt(db, owner, run, attempt, scope):
    await ledger.verify_events(db, owner, scope.id)
    await security._verify_accounts(db, owner)
    charges = (
        await db.scalars(
            select(ResearchCharge)
            .where(ResearchCharge.run_id == run.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    current = []
    for row in charges:
        charge = await security.charge(db, owner, row.id)
        approved = await security.approval(db, owner, charge.approval_id)
        await security._registered(db, owner, scope, approved)
        await security._registered(db, owner, scope, charge)
        request = await model_request.load(db, owner, approved)
        if request is None:
            raise ledger.error("ASSISTANT_RECOVERY_REPLY_UNPROVEN")
        bound_attempt = await db.get(
            ResearchAttempt, uuid.UUID(request["attempt_id"]), populate_existing=True
        )
        if (
            bound_attempt is None
            or bound_attempt.run_id != run.id
            or bound_attempt.number > attempt.number
        ):
            raise ledger.error("ASSISTANT_RECOVERY_REPLY_UNPROVEN")
        call = "h02-model:" + request["call_hash"]
        if charge.call_key != gateways.private_key(
            call, "call", owner, run.id
        ) or approved.proposal_key != gateways.private_key(call, "proposal", owner, run.id):
            raise ledger.error("ASSISTANT_RECOVERY_REPLY_UNPROVEN")
        if bound_attempt.number < attempt.number:
            continue  # Original old charge/caps remain verified, never reused or resent.
        if bound_attempt.id != attempt.id:
            raise ledger.error("ASSISTANT_RECOVERY_REPLY_UNPROVEN")
        current.append((approved, charge))
    return current


async def verify_financial(db, owner, run, attempt, scope):
    for approved, charge in await charges_for_attempt(db, owner, run, attempt, scope):
        if (
            charge.state != "settled"
            or await advisory_receipt.read(db, owner, approved, charge) is None
        ):
            raise ledger.error("ASSISTANT_RECOVERY_REPLY_UNPROVEN")
