"""Owned Reader dispatch tasks must really terminate before a stop ACK."""

import asyncio

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_planning import ResearchPlanningLease
from app.research.reader_contracts import ReaderToken
from app.services import research_service as ledger

_active: dict[ReaderToken, set[asyncio.Task]] = {}


async def register(db, owner, run):
    from app.research.reader_lease import validate

    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    lease = await db.get(ResearchPlanningLease, attempt.id) if attempt else None
    if lease is None:
        raise ledger.error("EXECUTION_FENCE_LOST")
    token = ReaderToken(owner, run.id, attempt.id, lease.worker_id, lease.fence)
    await validate(db, token, running=False)
    task = asyncio.current_task()
    if task is None:
        raise ledger.error("READER_STOP_UNPROVEN")
    values = _active.setdefault(token, set())
    if any(not active.done() and active is not task for active in values):
        raise ledger.error("READER_TRANSPORT_BUSY")
    values.add(task)

    def ended(actual):
        values.discard(actual)
        if not values and _active.get(token) is values:
            _active.pop(token, None)

    task.add_done_callback(ended)


def drained(token):
    if type(token) is not ReaderToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    if any(not task.done() for task in _active.get(token, ())):
        raise ledger.error("READER_STOP_UNPROVEN")
