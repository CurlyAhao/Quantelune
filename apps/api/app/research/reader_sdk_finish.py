"""A fixed, proved SDK checkpoint follows an independently completed Reader."""

import hashlib
import re

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlanningLease
from app.research import harness_binding, reader_contracts, reader_finish
from app.research.contracts import logical_hash, parse_document
from app.research.egress import failure
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger

EVENT = "reader.sdk_finished"


async def fixed_snapshot(graph, saver, binding):
    # Pure fixed-ID SDK algorithm shared with Planner. It grants no business
    # permission; the dedicated Reader proof below remains mandatory.
    from app.exceptions import StatForgeError
    from app.research.planner_finish import fixed_snapshot as snapshot

    try:
        return await snapshot(graph, saver, binding)
    except StatForgeError:
        raise failure("READER_CHECKPOINT_UNPROVEN") from None


async def record(factory, token, binding, graph, saver, model):
    await harness_binding.authorize(factory, binding, token, checkpoint=True)
    state, checkpoint_id = await fixed_snapshot(graph, saver, binding)
    await model.verify_state(state.values)
    state_hash = hashlib.sha256(SafeSerializer().dumps_typed(state.values)[1]).hexdigest()
    async with factory() as db:
        receipt = await reader_finish.terminal_claim(db, token)
        run, scope = await ledger.run(db, token.run_id, token.owner_id)
        thread = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
        if thread is None or any(
            getattr(thread, name) != getattr(binding, name) for name in binding.__dataclass_fields__
        ):
            raise failure("READER_CHECKPOINT_UNPROVEN")
        harness_binding._verify(thread, token, run)
        value = dict(
            version=1,
            owner_id=str(token.owner_id),
            run_id=str(run.id),
            attempt_id=str(token.attempt_id),
            binding_id=str(binding.id),
            binding_hash=binding.binding_hash,
            input_hash=run.input_hash,
            fence=token.fence,
            answer_id=str(receipt.id),
            answer_hash=receipt.document_hash,
            checkpoint_id=checkpoint_id,
            state_hash=state_hash,
        )
        if thread.finish_hash is not None:
            event = await verify(db, token.owner_id, run, thread)
            if parse_document(event.payload_json) != value:
                raise failure("READER_CHECKPOINT_CHANGED")
            return event
        thread.finish_hash = logical_hash(value)
        thread.finish_checkpoint_id = checkpoint_id
        event = await ledger._append(
            db, scope, token.owner_id, "reader_sdk", thread.id, EVENT, value
        )
        await db.commit()
        return event


async def verify(db, owner, run, thread):
    _, scope = await ledger.run(db, run.id, owner)
    attempt = await db.get(ResearchAttempt, thread.attempt_id, populate_existing=True)
    receipt = await reader_finish.domain(db, owner, run, attempt)
    lease = await db.get(ResearchPlanningLease, thread.attempt_id, populate_existing=True)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == thread.id,
                ResearchEvent.event_type == EVENT,
            )
            .limit(2)
        )
    ).all()
    if len(events) != 1:
        raise failure("READER_CHECKPOINT_UNPROVEN")
    event = events[0]
    value = parse_document(event.payload_json)
    if (
        thread.owner_id != owner
        or thread.run_id != run.id
        or thread.input_hash != run.input_hash
        or thread.purpose != "assistant"
        or thread.profile_hash
        != logical_hash(
            reader_contracts.view_profile(
                reader_contracts.view_version(parse_document(run.input_json)["parameters"])
            )
        )
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or lease is None
        or lease.owner_id != owner
        or lease.active_owner_id is not None
        or lease.released_at_ms is None
        or run.state != "completed"
        or attempt.state != "completed"
        or attempt.number != run.attempt_count
        or run.completion_hash != receipt.document_hash
        or attempt.completion_hash != receipt.document_hash
        or event.actor_id != owner
        or event.entity_kind != "reader_sdk"
        or event.sequence <= receipt.sequence
        or thread.finish_hash != logical_hash(value)
        or event.logical_hash != thread.finish_hash
        or type(value.get("version")) is not int
        or type(value.get("fence")) is not int
        or value.get("fence") != lease.fence
        or value
        != dict(
            version=1,
            owner_id=str(owner),
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            binding_id=str(thread.id),
            binding_hash=thread.binding_hash,
            input_hash=run.input_hash,
            fence=lease.fence,
            answer_id=str(receipt.id),
            answer_hash=receipt.document_hash,
            checkpoint_id=thread.finish_checkpoint_id,
            state_hash=value.get("state_hash"),
        )
        or not isinstance(value.get("state_hash"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["state_hash"]) is None
        or not isinstance(thread.finish_checkpoint_id, str)
        or not 1 <= len(thread.finish_checkpoint_id) <= 128
    ):
        raise failure("READER_CHECKPOINT_UNPROVEN")
    return event
