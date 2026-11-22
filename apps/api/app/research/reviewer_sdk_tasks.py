"""Internal Reviewer SDK start barrier and observed termination, without stop rights."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock

from sqlalchemy import event, select
from sqlalchemy.orm import Session, SessionTransaction

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchHarnessThread
from app.research import harness_binding, reviewer_identity, reviewer_lease, reviewer_sdk_events
from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_contracts import ReviewerToken
from app.research.reviewer_recovery_contracts import producer
from app.research.supervisor import _await_terminal
from app.services import research_service as ledger


@dataclass
class Active:
    token: ReviewerToken
    task: asyncio.Task
    invocation_id: str
    entry: ResearchEvent | None = None
    value: reviewer_sdk_events.Entry | None = None
    callbacks: dict[asyncio.Task, int] = field(default_factory=dict)
    closing: bool = False


_active: dict[uuid.UUID, Active] = {}
_context: ContextVar[Active | None] = ContextVar("reviewer_sdk_invocation", default=None)
_mutex = RLock()
_frozen: dict[uuid.UUID, tuple[ReviewerToken, SessionTransaction]] = {}
_FREEZE_KEY = "statforge_reviewer_sdk_boundary"


def _registerable(token):
    if token.attempt_id in _active or token.attempt_id in _frozen:
        raise ledger.error("REVIEWER_SDK_BUSY")


def freeze(db, token):
    """Internal only, after live DB owner validation; hold admission through root TX end."""
    if type(token) is not ReviewerToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    transaction = db.sync_session.get_transaction()
    if transaction is None or transaction.parent is not None or not transaction.is_active:
        raise ledger.error("REVIEWER_BOUNDARY_UNPROVEN")
    with _mutex:
        marker = db.info.get(_FREEZE_KEY)
        if marker is not None and marker[0] is not transaction:
            raise ledger.error("REVIEWER_BOUNDARY_UNPROVEN")
        previous = _frozen.get(token.attempt_id)
        if token.attempt_id in _active or (
            previous is not None and previous != (token, transaction)
        ):
            raise ledger.error("REVIEWER_SDK_BUSY")
        if marker is None:
            marker = (transaction, set())
            db.info[_FREEZE_KEY] = marker
        marker[1].add(token.attempt_id)
        _frozen[token.attempt_id] = (token, transaction)


def require_frozen(db, token):
    """A completion transition must stay inside its actual observation transaction."""
    if type(token) is not ReviewerToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    transaction = db.sync_session.get_transaction()
    with _mutex:
        if (
            transaction is None
            or not transaction.is_active
            or (_frozen.get(token.attempt_id) != (token, transaction))
        ):
            raise ledger.error("REVIEWER_BOUNDARY_UNPROVEN")


@event.listens_for(Session, "after_transaction_end")
def _release_freeze(session, transaction):
    with _mutex:
        marker = session.info.get(_FREEZE_KEY)
        if marker is None or marker[0] is not transaction or transaction.parent is not None:
            return
        session.info.pop(_FREEZE_KEY)
        for attempt_id in marker[1]:
            held = _frozen.get(attempt_id)
            if held is not None and held[1] is transaction:
                _frozen.pop(attempt_id)


def require_active(token, binding_hash):
    """Only actual SDK descendants inherit the live server-owned invocation context."""
    if type(token) is not ReviewerToken or type(token.fence) is not int or token.fence <= 0:
        raise ledger.error("EXECUTION_FENCE_LOST")
    active = _context.get()
    with _mutex:
        if (
            active is None
            or _active.get(token.attempt_id) is not active
            or active.token != token
            or active.task.done()
            or active.closing
            or active.task.get_loop() is not asyncio.get_running_loop()
            or active.entry is None
            or active.value is None
            or active.value.binding_hash != binding_hash
            or token.attempt_id in _frozen
        ):
            raise ledger.error("REVIEWER_SDK_CONTEXT_UNPROVEN")
    return active.value


@asynccontextmanager
async def callback(token, binding_hash):
    """Track the actual effects-bearing callback across all of its awaits/cleanup."""
    with _mutex:
        require_active(token, binding_hash)
        active = _context.get()
        task = asyncio.current_task()
        if active is None or task is None:
            raise ledger.error("REVIEWER_SDK_CONTEXT_UNPROVEN")
        child = task is not active.task
        if child:
            active.callbacks[task] = active.callbacks.get(task, 0) + 1
    try:
        yield
    finally:
        if child:
            with _mutex:
                count = active.callbacks[task] - 1
                if count:
                    active.callbacks[task] = count
                else:
                    active.callbacks.pop(task)


def require_callback(token, binding_hash):
    """Gateway effects need a tracked lifetime, not just an inherited context probe."""
    with _mutex:
        value = require_active(token, binding_hash)
        active = _context.get()
        task = asyncio.current_task()
        if active is None or (task is not active.task and task not in active.callbacks):
            raise ledger.error("REVIEWER_SDK_CALLBACK_UNPROVEN")
        return value


def writer_origin(token, binding_hash):
    """Actual committed entry, only inside the registered effects lifetime."""
    with _mutex:
        entry = require_callback(token, binding_hash)
        active = _context.get()
        event = active.entry if active is not None else None
        if (
            event is None
            or event.event_type != reviewer_sdk_events.ENTERED
            or event.entity_kind != "run"
            or event.entity_id != token.run_id
            or event.actor_id != token.owner_id
            or event.logical_hash != logical_hash(entry.model_dump(mode="json"))
            or parse_document(event.payload_json) != entry.model_dump(mode="json")
            or entry.owner_id != str(token.owner_id)
            or entry.run_id != str(token.run_id)
            or entry.attempt_id != str(token.attempt_id)
            or entry.producer.worker_id != token.worker_id
            or entry.producer.fence != token.fence
        ):
            raise ledger.error("REVIEWER_WRITER_UNPROVEN")
        return event, entry


def drained(token):
    if type(token) is not ReviewerToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    with _mutex:
        if token.attempt_id in _active:
            # Keep registration through the quiescence commit, even after Task.done().
            raise ledger.error("REVIEWER_SDK_BUSY")


async def _entered(factory, active: Active, binding):
    from app.research.replay import verify_business

    token = active.token
    if _active.get(token.attempt_id) is not active or active.task.done():
        raise ledger.error("REVIEWER_SDK_BUSY")
    async with factory() as db:
        run, scope, lease, now = await reviewer_lease.validate(db, token, running=False)
        if run.state not in {"running", "waiting_approval"}:
            raise ledger.error("REVIEWER_SDK_STATE_REFUSED")
        if token.fence != 1:
            raise ledger.error("REVIEWER_RECEIPT_ONLY")
        await verify_business(db, token.owner_id, scope.id)
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        claim_event, claim = await reviewer_identity.original(
            db, token.owner_id, run, attempt, lease
        )
        row = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
        if row is None or any(
            getattr(row, name) != getattr(binding, name)
            for name in harness_binding.Binding.__dataclass_fields__
        ):
            raise ledger.error("HARNESS_BINDING_CHANGED")
        harness_binding._verify(row, token, run)
        await reviewer_sdk_events.binding_before(
            db, token.owner_id, run, row, scope.event_sequence + 1
        )
        rows = (
            await db.scalars(
                select(ResearchEvent)
                .where(
                    ResearchEvent.entity_id == run.id,
                    ResearchEvent.event_type.in_(reviewer_sdk_events.KINDS),
                )
                .limit(reviewer_sdk_events.LIMIT * 2 + 1)
            )
        ).all()
        previous: set[str] = set()
        closed: set[str] = set()
        for event in rows:
            value = parse_document(event.payload_json)
            if value.get("attempt_id") == str(token.attempt_id):
                (previous if event.event_type == reviewer_sdk_events.ENTERED else closed).add(
                    value["invocation_id"]
                )
        if (
            len(rows) > reviewer_sdk_events.LIMIT * 2
            or previous != closed
            or len(previous) >= reviewer_sdk_events.LIMIT
        ):
            raise ledger.error("REVIEWER_SDK_UNQUIESCED")
        value = reviewer_sdk_events.Entry(
            version=1,
            invocation_id=active.invocation_id,
            owner_id=str(token.owner_id),
            run_id=str(run.id),
            attempt_id=str(token.attempt_id),
            input_hash=run.input_hash,
            claim_event_id=str(claim_event.id),
            claim_hash=claim_event.logical_hash,
            producer=producer(claim),
            recovery_event_id=None,
            recovery_hash=None,
            binding_id=str(binding.id),
            binding_hash=binding.binding_hash,
            thread_id=binding.thread_id,
            entered_at_ms=now,
        )
        event = await ledger._append(
            db,
            scope,
            token.owner_id,
            "run",
            run.id,
            reviewer_sdk_events.ENTERED,
            value.model_dump(mode="json"),
        )
        await db.commit()
        # Only a known successful commit permits the start barrier to open.
        active.entry, active.value = event, value


async def _quiesced(factory, active: Active, outcome):
    from app.research.replay import verify_business

    if (
        _active.get(active.token.attempt_id) is not active
        or not active.task.done()
        or active.callbacks
        or asyncio.current_task() is active.task
    ):
        raise ledger.error("REVIEWER_SDK_BUSY")
    if active.entry is None or active.value is None:
        return  # No known committed entry: never manufacture its observation.
    token, entry = active.token, active.value
    async with factory() as db:
        run, scope, lease, now = await reviewer_lease.validate(
            db, token, running=False, current_source=False
        )
        if producer(lease) != entry.producer:
            raise ledger.error("EXECUTION_FENCE_LOST")
        await verify_business(db, token.owner_id, scope.id)
        prior = await db.scalar(
            select(ResearchEvent.id).where(
                ResearchEvent.entity_id == run.id,
                ResearchEvent.event_type == reviewer_sdk_events.QUIESCED,
                ResearchEvent.payload_json.contains(entry.invocation_id),
            )
        )
        if prior is not None:
            raise ledger.error("REVIEWER_SDK_ENTRY_UNPROVEN")
        actual = await db.get(ResearchEvent, active.entry.id, populate_existing=True)
        if actual is None or (
            actual.logical_hash != active.entry.logical_hash
            or parse_document(actual.payload_json) != entry.model_dump(mode="json")
        ):
            raise ledger.error("REVIEWER_SDK_ENTRY_UNPROVEN")
        value = reviewer_sdk_events.Quiesced(
            version=1,
            invocation_id=entry.invocation_id,
            owner_id=entry.owner_id,
            run_id=entry.run_id,
            attempt_id=entry.attempt_id,
            producer=entry.producer,
            entry_event_id=str(actual.id),
            entry_hash=actual.logical_hash,
            observed_at_ms=now,
            outcome=outcome,
        )
        await ledger._append(
            db,
            scope,
            token.owner_id,
            "run",
            run.id,
            reviewer_sdk_events.QUIESCED,
            value.model_dump(mode="json"),
        )
        await db.commit()


async def invoke(factory, token, binding, operation):
    """Internal supervisor operation includes actual graph, saver and Gateway cleanup."""
    if type(token) is not ReviewerToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()
        marker = _context.set(active)
        try:
            return await operation()
        finally:
            _context.reset(marker)

    with _mutex:
        _registerable(token)
        active = Active(
            token, asyncio.create_task(blocked(), name="reviewer-sdk"), str(uuid.uuid4())
        )
        _active[token.attempt_id] = active
    try:
        await _entered(factory, active, binding)
        gate.set()
        return await active.task
    finally:

        async def finish():
            if not active.task.done():
                active.task.cancel()
            result = (await asyncio.gather(active.task, return_exceptions=True))[0]
            with _mutex:
                active.closing = True
                callbacks = tuple(active.callbacks)
            for task in callbacks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*callbacks, return_exceptions=True)
            with _mutex:
                if active.callbacks:
                    raise ledger.error("REVIEWER_SDK_CALLBACKS_UNQUIESCED")
            if active.task.cancelled():
                outcome = "cancelled"
            elif active.task.exception() is not None:
                outcome = "rejected"
            else:
                outcome = (
                    "approval_wait"
                    if isinstance(result, dict) and result.get("__interrupt__")
                    else "returned"
                )
            detached = bool(callbacks) and outcome in {"returned", "approval_wait"}
            if detached:
                outcome = "rejected"
            await _quiesced(factory, active, outcome)
            if detached:
                raise ledger.error("REVIEWER_SDK_CALLBACKS_DETACHED")

        try:
            await _await_terminal(asyncio.create_task(finish(), name="reviewer-sdk-quiesce"))
        finally:
            # An uncommitted observation leaves a durable open entry, never proof of drain.
            with _mutex:
                if _active.get(token.attempt_id) is active:
                    _active.pop(token.attempt_id)
