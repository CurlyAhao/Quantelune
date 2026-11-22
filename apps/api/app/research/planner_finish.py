"""Only a fixed genuine final root SDK checkpoint can yield a planner finish."""

import hashlib
import uuid

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import (
    ResearchPlannerSDKFinish,
    ResearchPlannerThread,
    ResearchPlanningLease,
)
from app.research import harness_binding, plan_receipt
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document
from app.research.egress import failure
from app.research.execution_lease import _boundary
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger

EVENT = "planning.sdk_finished"


async def fixed_snapshot(graph, saver, binding):
    config = dict(
        configurable=dict(thread_id=binding.thread_id, checkpoint_ns=""),
        callbacks=[],
        recursion_limit=12,
    )
    checkpoint = await saver.aget_tuple(config)
    if checkpoint is None:
        raise failure("PLANNER_CHECKPOINT_UNPROVEN")
    values = checkpoint.config.get("configurable", {})
    identifier = values.get("checkpoint_id")
    if (
        values.get("thread_id") != binding.thread_id
        or values.get("checkpoint_ns", "") != ""
        or not isinstance(identifier, str)
        or not 1 <= len(identifier) <= 128
        or checkpoint.checkpoint.get("id") != identifier
        or checkpoint.metadata.get("source") != "loop"
    ):
        raise failure("PLANNER_CHECKPOINT_UNPROVEN")
    fixed = await graph.aget_state(
        checkpoint.config
    )  # Explicit ID: never latest pending-write projection.
    if (
        fixed.next
        or not fixed.values
        or fixed.interrupts
        or any(item.error or item.interrupts for item in fixed.tasks)
        or fixed.config.get("configurable", {}).get("checkpoint_id") != identifier
    ):
        raise failure("PLANNER_CHECKPOINT_UNPROVEN")
    return fixed, identifier


async def record(factory, token, binding, graph, saver, model):
    state, checkpoint_id = await fixed_snapshot(graph, saver, binding)
    await model.verify_state(state.values)
    state_hash = hashlib.sha256(SafeSerializer().dumps_typed(state.values)[1]).hexdigest()
    await harness_binding.authorize(factory, binding, token)
    async with factory() as db:
        await _boundary(db, token.owner_id)
        run, scope = await ledger.run(db, token.run_id, token.owner_id)
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        receipt = await plan_receipt.prove(db, token.owner_id, run, attempt)
        thread = await db.get(ResearchPlannerThread, binding.id, populate_existing=True)
        lease = await db.get(ResearchPlanningLease, token.attempt_id, populate_existing=True)
        await plan_receipt._actor(db, token.owner_id)
        if (
            run.state != State.COMPLETED.value
            or run.completion_hash != receipt.document_hash
            or thread is None
            or thread.binding_hash != binding.binding_hash
            or thread.input_hash != run.input_hash
            or lease is None
            or lease.owner_id != token.owner_id
            or lease.worker_id != token.worker_id
            or lease.fence != token.fence
            or lease.released_at_ms is None
            or lease.active_owner_id is not None
        ):
            raise failure("PLANNER_BUSINESS_UNPROVEN")
        harness_binding._verify(thread, token, run)
        if any(
            getattr(thread, field) != getattr(binding, field)
            for field in harness_binding.Binding.__dataclass_fields__
        ):
            raise failure("HARNESS_BINDING_CHANGED")
        value = dict(
            version=1,
            owner_id=str(token.owner_id),
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            binding_id=str(binding.id),
            binding_hash=binding.binding_hash,
            input_hash=run.input_hash,
            source_version=receipt.source_version,
            fence=token.fence,
            receipt_id=str(receipt.id),
            receipt_hash=receipt.document_hash,
            checkpoint_id=checkpoint_id,
            state_hash=state_hash,
        )
        old = await db.scalar(
            select(ResearchPlannerSDKFinish).where(
                ResearchPlannerSDKFinish.attempt_id == attempt.id
            )
        )
        if old is not None:
            if (
                old.document_hash != logical_hash(value)
                or parse_document(old.document_json) != value
            ):
                raise failure("PLANNER_CHECKPOINT_CHANGED")
            await verify(db, token.owner_id, run, old)
            return old
        row = ResearchPlannerSDKFinish(
            id=uuid.uuid4(),
            owner_id=token.owner_id,
            run_id=run.id,
            attempt_id=attempt.id,
            thread_id=binding.id,
            receipt_id=receipt.id,
            checkpoint_id=checkpoint_id,
            state_hash=state_hash,
            document_json=canonical_bytes(value).decode(),
            document_hash=logical_hash(value),
        )
        db.add(row)
        await db.flush()
        await ledger._append(db, scope, token.owner_id, "planner_sdk_finish", row.id, EVENT, value)
        await db.commit()
        return row


async def verify(db, owner, run, row):
    attempt = await db.get(ResearchAttempt, row.attempt_id, populate_existing=True)
    receipt = await plan_receipt.prove(db, owner, run, attempt)
    thread = await db.get(ResearchPlannerThread, row.thread_id, populate_existing=True)
    lease = await db.get(ResearchPlanningLease, row.attempt_id, populate_existing=True)
    value = parse_document(row.document_json)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(ResearchEvent.event_type == EVENT, ResearchEvent.entity_id == row.id)
            .limit(2)
        )
    ).all()
    if (
        thread is None
        or lease is None
        or thread.owner_id != owner
        or thread.run_id != run.id
        or thread.attempt_id != attempt.id
        or thread.input_hash != run.input_hash
        or thread.profile_hash
        != logical_hash(
            harness_binding.profile(
                "planner", run.kind, parse_document(run.input_json)["parameters"]
            )
        )
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or lease.owner_id != owner
        or value.get("fence") != lease.fence
        or row.owner_id != owner
        or row.run_id != run.id
        or row.receipt_id != receipt.id
        or row.document_hash != logical_hash(value)
        or run.state != "completed"
        or run.completion_hash != receipt.document_hash
        or value
        != dict(
            version=1,
            owner_id=str(owner),
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            binding_id=str(thread.id),
            binding_hash=thread.binding_hash,
            input_hash=run.input_hash,
            source_version=receipt.source_version,
            fence=value.get("fence"),
            receipt_id=str(receipt.id),
            receipt_hash=receipt.document_hash,
            checkpoint_id=row.checkpoint_id,
            state_hash=row.state_hash,
        )
        or type(value["fence"]) is not int
        or value["fence"] < receipt.fence
        or len(events) != 1
        or events[0].actor_id != owner
        or events[0].entity_kind != "planner_sdk_finish"
        or parse_document(events[0].payload_json) != value
        or events[0].logical_hash != row.document_hash
    ):
        raise failure("PLANNER_CHECKPOINT_UNPROVEN")
    return value
