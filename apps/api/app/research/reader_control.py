"""Reader control requests are durable; a real worker separately acknowledges them."""

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import ResearchPlanningLease
from app.models.user import User
from app.research import execution_lease, reader_lease
from app.research.contracts import State, logical_hash, parse_document
from app.services import research_service as ledger

REQUEST = "reader.control_requested"
ACK = "reader.control_acknowledged"
LIMIT = 100


async def rows(db, owner, run, attempt):
    _, scope = await ledger.run(db, run.id, owner)
    await ledger.verify_events(db, owner, scope.id)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == run.id,
                ResearchEvent.event_type.in_([REQUEST, ACK]),
            )
            .order_by(ResearchEvent.sequence)
        )
    ).all()
    result = []
    for event in events:
        value = parse_document(event.payload_json)
        if value["attempt_id"] != str(attempt.id):
            continue
        if (
            event.entity_kind != "run"
            or event.actor_id != owner
            or event.logical_hash != logical_hash(value)
        ):
            raise ledger.error("READER_CONTROL_UNPROVEN")
        result.append((event.event_type, value))
    if len(result) > LIMIT * 2:
        raise ledger.error("READER_CONTROL_LIMIT")
    return result


async def latest(db, owner, run, attempt):
    values = await rows(db, owner, run, attempt)
    requests = [value for kind, value in values if kind == REQUEST]
    return requests[-1] if requests else None


async def cancelled(db, owner, run, attempt):
    values = await rows(db, owner, run, attempt)
    return any(kind == REQUEST and value["action"] == "cancel" for kind, value in values)


async def response(db, owner, run, attempt, requested):
    values = await rows(db, owner, run, attempt)
    acknowledged = any(
        kind == ACK and value["control_index"] == requested["control_index"]
        for kind, value in values
    )
    return dict(
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        action=requested["action"],
        control_index=requested["control_index"],
        revision=run.revision,
        state=run.state,
        acknowledged=acknowledged,
        pending=not acknowledged,
    )


async def request(db, owner, run_id, revision, action, idempotency_key):
    from app.research.replay import verify_business

    await execution_lease._boundary(db, owner)
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    run, scope = await ledger.run(db, run_id, owner)
    if run.kind != "reader" or action not in {"pause", "resume", "cancel"}:
        raise ledger.error("READER_UNSUPPORTED")
    if (
        type(revision) is not int
        or revision < 0
        or not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 200
    ):
        raise ledger.error("READER_CONTROL_INVALID", 422)
    await verify_business(db, owner, scope.id)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    if attempt is None or attempt.state != run.state:
        raise ledger.error("STATE_INCONSISTENT")
    key = logical_hash(idempotency_key)  # Raw caller key never enters an event.
    digest = logical_hash(
        dict(
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            revision=revision,
            action=action,
            key=key,
        )
    )
    values = await rows(db, owner, run, attempt)
    requests = [value for kind, value in values if kind == REQUEST]
    for old in requests:
        if old["key_hash"] == key:
            if old["request_hash"] != digest:
                raise ledger.error("IDEMPOTENCY_CONFLICT")
            return await response(db, owner, run, attempt, old)
    if len(requests) >= LIMIT:
        raise ledger.error("READER_CONTROL_LIMIT")
    # A durable Answer is historical evidence even when cancellation wins.
    # It cannot authorize another pause/resume or fresh model operation.
    if action != "cancel" and await reader_lease.answer_event(db, attempt.id) is not None:
        raise ledger.error("READER_RECEIPT_ONLY")
    if run.revision != revision:
        raise ledger.error("STALE_REVISION")
    if any(value["action"] == "cancel" for value in requests):
        raise ledger.error("READER_CANCEL_PENDING")
    lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
    if action != "cancel":
        await reader_lease.source(db, owner, run)
    if action == "pause" and run.state not in {"running", "waiting_approval"}:
        raise ledger.error("ILLEGAL_TRANSITION")
    if action == "resume" and (
        run.state not in {"paused", "waiting_approval", "running"}
        or lease is None
        or not lease.pause_requested
    ):
        raise ledger.error("ILLEGAL_TRANSITION")
    if action == "cancel" and run.state not in {"queued", "running", "waiting_approval", "paused"}:
        raise ledger.error("ILLEGAL_TRANSITION")
    if run.state != "queued" and (
        lease is None or lease.released_at_ms is not None or lease.active_owner_id != owner
    ):
        raise ledger.error("READER_CONTROL_UNPROVEN")
    before = run.revision
    if action == "pause" and run.state == "running" and not lease.pause_requested:
        run.revision += 1  # Preserve the existing acknowledged-pause state graph.
    if lease is not None:
        lease.pause_requested = action == "pause"
    value = dict(
        attempt_id=str(attempt.id),
        action=action,
        control_index=len(requests) + 1,
        key_hash=key,
        request_hash=digest,
        input_hash=run.input_hash,
        revision_before=before,
        revision_after=run.revision,
        pause_requested=action == "pause",
    )
    await ledger._append(db, scope, owner, "run", run.id, REQUEST, value)
    if action == "resume" and run.state == "paused":
        await ledger.transition(db, owner, run.id, run.revision, State.RUNNING)
    elif action == "cancel" and run.state in {"running", "paused"}:
        await ledger.transition(db, owner, run.id, run.revision, State.CANCEL_REQUESTED)
    elif action == "cancel" and run.state == "queued":
        if lease is not None:
            raise ledger.error("READER_CONTROL_UNPROVEN")
        await ledger.transition(db, owner, run.id, run.revision, State.CANCELLED)
        await ledger._append(
            db,
            scope,
            owner,
            "run",
            run.id,
            ACK,
            dict(
                attempt_id=str(attempt.id),
                control_index=value["control_index"],
                action=action,
                revision=run.revision,
                proof="never_claimed",
                fence=None,
                worker_id=None,
            ),
        )
    await db.flush()
    return await response(db, owner, run, attempt, value)


async def acknowledge(db, token):
    from app.research.reader_contracts import ReaderToken

    if type(token) is not ReaderToken:
        raise ledger.error("EXECUTION_FENCE_LOST")
    await execution_lease._boundary(db, token.owner_id)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    if attempt is None or attempt.run_id != run.id or attempt.number != run.attempt_count:
        raise ledger.error("EXECUTION_FENCE_LOST")
    requested = await latest(db, token.owner_id, run, attempt)
    if requested is None:
        return None
    values = await rows(db, token.owner_id, run, attempt)
    if any(
        kind == ACK and value["control_index"] == requested["control_index"]
        for kind, value in values
    ):
        # Idempotence never accepts another worker or a stale fence.
        await reader_lease.ack_owner(db, token)
        return await response(db, token.owner_id, run, attempt, requested)
    run, scope, lease, _ = await reader_lease.validate(
        db, token, running=False, current_source=False
    )
    action = requested["action"]
    if action == "cancel":
        await reader_lease.stop_owned(db, token, State.CANCELLED)
    elif action == "pause" and run.state == "running":
        if not lease.pause_requested:
            raise ledger.error("READER_CONTROL_UNPROVEN")
        await ledger.transition(db, token.owner_id, run.id, run.revision, State.PAUSED)
    elif action == "pause" and run.state != "waiting_approval":
        raise ledger.error("READER_CONTROL_UNPROVEN")
    await ledger._append(
        db,
        scope,
        token.owner_id,
        "run",
        run.id,
        ACK,
        dict(
            attempt_id=str(attempt.id),
            control_index=requested["control_index"],
            action=action,
            revision=run.revision,
            proof="worker_boundary",
            fence=token.fence,
            worker_id=token.worker_id,
        ),
    )
    await db.flush()
    return await response(db, token.owner_id, run, attempt, requested)
