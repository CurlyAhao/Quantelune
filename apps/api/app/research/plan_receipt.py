"""PlanVersion receipts are business evidence; SDK termination is not."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import (
    ResearchContextVersion,
    ResearchPlanningLease,
    ResearchPlanReceipt,
    ResearchPlanVersion,
)
from app.models.research_security import ResearchCharge
from app.models.user import User
from app.research import planning_lease
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.services import research_plan_service as plans
from app.services import research_service as ledger

EVENT = "planning.receipt_recorded"


async def _actor(db, owner):
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)


async def finish(db, token: planning_lease.PlanningToken):
    from app.research.contracts import State
    from app.research.replay import verify_business

    await planning_lease.execution_lease._boundary(db, token.owner_id)
    await _actor(db, token.owner_id)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    receipt = await prove(db, token.owner_id, run, attempt)
    await verify_business(db, token.owner_id, scope.id)
    if run.state == State.COMPLETED.value:
        if run.completion_hash != receipt.document_hash:
            raise ledger.error("PLAN_RECEIPT_UNPROVEN")
        return receipt
    # Receipt is already durable and verified. Historical closeout needs the
    # actual live claim, but cannot make a new model/tool request or Plan version.
    run, _, lease, now = await planning_lease.validate(db, token, current_source=False)
    if lease.pause_requested or receipt.fence > token.fence:
        raise ledger.error("PLAN_RECEIPT_UNPROVEN")
    run.domain_kind, run.domain_run_id = "plan", receipt.id
    await ledger.transition(db, token.owner_id, run.id, run.revision, State.COMPLETED)
    lease.active_owner_id = None
    lease.released_at_ms = now
    await db.flush()
    return receipt


async def _version(db, owner, run, version_id):
    row = await db.get(ResearchPlanVersion, version_id, populate_existing=True)
    if row is None or row.owner_id != owner or row.session_id != run.session_id:
        raise ledger.error("PLAN_RECEIPT_UNPROVEN")
    value = parse_document(row.document_json)
    context = await db.get(ResearchContextVersion, row.context_id, populate_existing=True)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.entity_id == row.draft_id,
                ResearchEvent.event_type == plans.EVENT,
                ResearchEvent.actor_id == owner,
            )
            .limit(101)
        )
    ).all()
    matches = [
        event
        for event in events
        if parse_document(event.payload_json).get("version_id") == str(row.id)
    ]
    if (
        len(events) > 100
        or len(matches) != 1
        or context is None
        or row.document_hash != logical_hash(value)
        or parse_document(matches[0].payload_json) != value
        or matches[0].logical_hash != row.document_hash
        or context.owner_id != owner
        or context.session_id != run.session_id
        or context.source_version != value["plan"]["source_version"]
        or context.document_hash != logical_hash(parse_document(context.document_json))
    ):
        raise ledger.error("PLAN_RECEIPT_UNPROVEN")
    # Independent row + original event, pure Plan and Context normalization. The
    # caller verifies temporal lifecycle/source through the actual ledger replay.
    await plans.verify_event(
        db,
        matches[0],
        owner,
        value["attachments"],
        {"revision": row.revision - 1, "session_id": str(run.session_id)}
        if row.revision > 1
        else None,
    )
    from app.research import plan_versioning, planner_v2

    planner_v2.plan_matches(run, plan_versioning.parse(value["plan"]))
    return row, context, matches[0]


async def record(db, token: planning_lease.PlanningToken, version_id: uuid.UUID):
    from app.research.replay import verify_business

    await planning_lease.execution_lease._boundary(db, token.owner_id)
    await _actor(db, token.owner_id)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    old = await db.scalar(
        select(ResearchPlanReceipt).where(ResearchPlanReceipt.attempt_id == token.attempt_id)
    )
    if old is not None:
        if old.plan_version_id != version_id:
            raise ledger.error("IDEMPOTENCY_CONFLICT")
        await verify_business(db, token.owner_id, scope.id)
        return await prove(db, token.owner_id, run, await db.get(ResearchAttempt, token.attempt_id))
    run, scope, lease, _ = await planning_lease.validate(db, token)
    if lease.pause_requested:
        raise ledger.error("PLANNING_PAUSED")
    if (
        await db.scalar(
            select(ResearchCharge.id).where(
                ResearchCharge.run_id == run.id,
                ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
            )
        )
        is not None
    ):
        raise ledger.error("PLAN_RECEIPT_FINANCIAL_PENDING")
    await verify_business(db, token.owner_id, scope.id)
    version, context, _ = await _version(db, token.owner_id, run, version_id)
    parameters = parse_document(run.input_json)["parameters"]
    if context.source_version != parameters["source_version"]:
        raise ledger.error("PLAN_RECEIPT_UNPROVEN")
    value = dict(
        version=1,
        owner_id=str(token.owner_id),
        run_id=str(run.id),
        attempt_id=str(token.attempt_id),
        plan_version_id=str(version.id),
        input_hash=run.input_hash,
        source_version=context.source_version,
        plan_hash=version.plan_hash,
        plan_document_hash=version.document_hash,
        fence=token.fence,
    )
    receipt = ResearchPlanReceipt(
        id=uuid.uuid4(),
        owner_id=token.owner_id,
        run_id=run.id,
        attempt_id=token.attempt_id,
        plan_version_id=version.id,
        input_hash=run.input_hash,
        source_version=context.source_version,
        fence=token.fence,
        document_json=canonical_bytes(value).decode(),
        document_hash=logical_hash(value),
    )
    db.add(receipt)
    await db.flush()
    await ledger._append(db, scope, token.owner_id, "plan_receipt", receipt.id, EVENT, value)
    return receipt


async def prove(db, owner, run, attempt):
    if attempt is None or attempt.run_id != run.id or run.kind != "planner":
        raise ledger.error("PLAN_RECEIPT_UNPROVEN")
    receipt = await db.scalar(
        select(ResearchPlanReceipt)
        .where(ResearchPlanReceipt.attempt_id == attempt.id)
        .execution_options(populate_existing=True)
    )
    lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
    if receipt is None or lease is None:
        raise ledger.error("PLAN_RECEIPT_UNPROVEN")
    version, context, draft_event = await _version(db, owner, run, receipt.plan_version_id)
    value = dict(
        version=1,
        owner_id=str(owner),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        plan_version_id=str(version.id),
        input_hash=run.input_hash,
        source_version=context.source_version,
        plan_hash=version.plan_hash,
        plan_document_hash=version.document_hash,
        fence=receipt.fence,
    )
    events = (
        await db.scalars(
            select(ResearchEvent).where(
                ResearchEvent.event_type == EVENT, ResearchEvent.entity_id == receipt.id
            )
        )
    ).all()
    captured = parse_document(run.input_json)
    from app.research import planner_v2

    snapshot = planner_v2.captured(run)
    expected_parameters = {"operation": "plan", "source_version": context.source_version}
    if snapshot is not None:
        expected_parameters.update(planner_version=planner_v2.VERSION, protocol=snapshot)
        wire_version = planner_v2.wire_version(run)
        if wire_version is not None:
            expected_parameters["planner_wire_version"] = wire_version
    if (
        receipt.owner_id != owner
        or receipt.run_id != run.id
        or lease.owner_id != owner
        or receipt.input_hash != run.input_hash
        or receipt.source_version != context.source_version
        or receipt.fence > lease.fence
        or receipt.document_hash != logical_hash(value)
        or parse_document(receipt.document_json) != value
        or len(events) != 1
        or events[0].entity_kind != "plan_receipt"
        or events[0].actor_id != owner
        or events[0].workspace_id != draft_event.workspace_id
        or events[0].sequence <= draft_event.sequence
        or events[0].logical_hash != receipt.document_hash
        or parse_document(events[0].payload_json) != value
        or logical_hash(captured) != run.input_hash
        or captured["kind"] != "planner"
        or captured["parameters"] != expected_parameters
        or captured["attachments"] != parse_document(context.document_json)["attachments"]
    ):
        raise ledger.error("PLAN_RECEIPT_UNPROVEN")
    return receipt
