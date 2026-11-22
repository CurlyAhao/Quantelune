"""Owned Reviewer dispatch tasks must really terminate before a stop ACK."""

import asyncio
from threading import RLock

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import ResearchPlanningLease
from app.research.reviewer_contracts import ReviewerToken
from app.services import research_service as ledger

_active: dict[ReviewerToken, dict[asyncio.Task, set]] = {}
_mutex = RLock()


async def register(db, owner, run, charge_id):
    from app.research import model_request, reviewer_sdk_tasks
    from app.research.contracts import logical_hash, parse_document
    from app.research.reviewer_lease import validate
    from app.services import research_security_service as security

    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    lease = await db.get(ResearchPlanningLease, attempt.id) if attempt else None
    if lease is None:
        raise ledger.error("EXECUTION_FENCE_LOST")
    token = ReviewerToken(owner, run.id, attempt.id, lease.worker_id, lease.fence)
    await validate(db, token, running=False)
    charge = await security.charge(db, owner, charge_id)
    if charge.run_id != run.id or charge.state != "dispatched":
        raise ledger.error("REVIEWER_TRANSPORT_UNPROVEN")
    approval = await security.approval(db, owner, charge.approval_id)
    request = await model_request.load(db, owner, approval)
    if request is None or request["version"] != 4:
        raise ledger.error("REVIEWER_TRANSPORT_UNPROVEN")
    invocation = reviewer_sdk_tasks.require_callback(token, request["binding_hash"])
    if invocation.binding_id != request["binding_id"]:
        raise ledger.error("REVIEWER_TRANSPORT_UNPROVEN")
    scope = await security._charge_scope(db, owner, charge)
    await security._registered(db, owner, scope, charge)
    dispatch_event = await db.scalar(
        select(ResearchEvent)
        .where(
            ResearchEvent.workspace_id == scope.id,
            ResearchEvent.entity_id == charge.id,
            ResearchEvent.event_type == "security.charge",
        )
        .order_by(ResearchEvent.sequence.desc())
        .limit(1)
    )
    entries = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == run.id,
                ResearchEvent.event_type == "reviewer.sdk_entered",
                ResearchEvent.payload_json.contains(invocation.invocation_id),
            )
            .limit(2)
        )
    ).all()
    if (
        dispatch_event is None
        or len(entries) != 1
        or entries[0].actor_id != owner
        or entries[0].entity_kind != "run"
        or parse_document(entries[0].payload_json) != invocation.model_dump(mode="json")
        or entries[0].logical_hash != logical_hash(invocation.model_dump(mode="json"))
        or dispatch_event.sequence <= entries[0].sequence
        or dispatch_event.actor_id != owner
        or dispatch_event.entity_kind != "security_charge"
    ):
        raise ledger.error("REVIEWER_TRANSPORT_UNPROVEN")
    task = asyncio.current_task()
    if task is None:
        raise ledger.error("REVIEWER_STOP_UNPROVEN")
    reviewer_sdk_tasks.require_callback(token, request["binding_hash"])
    with _mutex:
        values = _active.setdefault(token, {})
        if any(
            not active.done() and active is not task
            for key, registered in _active.items()
            if key.attempt_id == token.attempt_id
            for active in registered
        ):
            raise ledger.error("REVIEWER_TRANSPORT_BUSY")
        values.setdefault(task, set()).add(
            (charge.id, dispatch_event.id, dispatch_event.logical_hash)
        )

    def ended(actual):
        with _mutex:
            values.pop(actual, None)
            if not values and _active.get(token) is values:
                _active.pop(token, None)

    task.add_done_callback(ended)


def drained(token):
    if type(token) is not ReviewerToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    with _mutex:
        if any(
            not task.done()
            for key, values in _active.items()
            if key.attempt_id == token.attempt_id
            for task in values
        ):
            raise ledger.error("REVIEWER_STOP_UNPROVEN")
