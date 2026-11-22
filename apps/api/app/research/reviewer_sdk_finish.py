"""Owned v2 registration after the read-only backend has completely closed."""

import uuid

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlanningLease
from app.models.user import User
from app.research import (
    execution_lease,
    harness_binding,
    reviewer_completed_history,
    reviewer_completion,
    reviewer_finish,
    reviewer_identity,
    reviewer_sdk_tasks,
    reviewer_snapshot,
    reviewer_transport,
)
from app.research.contracts import logical_hash, parse_document
from app.research.execution_identity import WorkerIdentity
from app.research.reviewer_contracts import ReviewerToken
from app.research.reviewer_recovery_contracts import producer
from app.research.reviewer_sdk_finish_contracts import EVENT, Closure, Receipt, load
from app.research.reviewer_sdk_finish_proof import verify
from app.services import research_service as ledger


async def fixed_snapshot(graph, saver, binding):
    """Legacy diagnostic helper; cannot supply authority to v2 record()."""
    from app.exceptions import StatForgeError
    from app.research.planner_finish import fixed_snapshot as snapshot

    try:
        return await snapshot(graph, saver, binding)
    except StatForgeError:
        raise ledger.error("REVIEWER_CHECKPOINT_UNPROVEN") from None


async def _owned(db, token, binding):
    now = await execution_lease._boundary(db, token.owner_id)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    lease = await db.get(ResearchPlanningLease, token.attempt_id, populate_existing=True)
    thread = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
    actor = await db.get(User, token.owner_id, populate_existing=True)
    if (
        actor is None
        or not actor.is_active
        or run.kind != "reviewer"
        or run.state != "completed"
        or attempt is None
        or attempt.run_id != run.id
        or attempt.number != run.attempt_count
        or attempt.state != "completed"
        or attempt.completion_hash != run.completion_hash
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
        or thread is None
        or any(
            getattr(thread, name) != getattr(binding, name) for name in binding.__dataclass_fields__
        )
        or db.sync_session.get_nested_transaction() is not None
    ):
        raise ledger.error("REVIEWER_CHECKPOINT_UNPROVEN")
    harness_binding._verify(thread, token, run)
    return run, scope, attempt, lease, thread, now


async def record(factory, token, binding, *, lease=None):
    """No caller graph/saver/model/Observation can authorize a finish event."""
    if type(token) is not ReviewerToken or type(token.fence) is not int or token.fence < 1:
        raise ledger.error("EXECUTION_FENCE_LOST")
    from app.research.harness_runtime import open_reviewer_snapshot

    async with open_reviewer_snapshot(factory, binding, token, lease=lease) as saver:
        observation = await reviewer_snapshot.observe(saver, binding)
    # Backend transaction, read capability and exclusion have all ended here.
    async with factory() as db:
        review = await reviewer_finish.terminal_claim(db, token)
        run, scope, attempt, current, thread, _ = await _owned(db, token, binding)
        reviewer_sdk_tasks.freeze(db, token)
        reviewer_sdk_tasks.drained(token)
        reviewer_transport.drained(token)
        if token.fence == 1:
            claim_event, claim = await reviewer_identity.original(
                db, token.owner_id, run, attempt, current
            )
            epoch, recovery = producer(claim), None
        else:
            from app.research import reviewer_recovery

            claim_event, claim, epoch, recovery = await reviewer_recovery.current(
                db, token.owner_id, run, attempt, current
            )
        context = dict(claim_event=claim_event, claim=claim, epoch=epoch, recovery=recovery)
        if thread.finish_hash is not None or thread.finish_checkpoint_id is not None:
            event = await verify(db, token.owner_id, run, thread, **context)
            if load(event.payload_json).observation != observation:
                raise ledger.error("REVIEWER_CHECKPOINT_CHANGED")
            await _owned(db, token, binding)
            reviewer_sdk_tasks.require_frozen(db, token)
            return event
        release_event, release = await reviewer_completion.terminal(
            db, token.owner_id, run, attempt, current
        )
        transition = await db.get(ResearchEvent, uuid.UUID(release.transition_event_id))
        completed = await reviewer_completed_history.prove_completed_messages(
            db,
            token.owner_id,
            run,
            attempt,
            binding,
            observation,
            before_sequence=scope.event_sequence + 1,
            completion_sequence=transition.sequence,
        )
        horizon = (scope.event_sequence, scope.event_head_hash)
        run, scope, attempt, current, thread, now = await _owned(db, token, binding)
        if producer(current) != epoch or horizon != (scope.event_sequence, scope.event_head_hash):
            raise ledger.error("REVIEWER_CHECKPOINT_CHANGED")
        reviewer_sdk_tasks.require_frozen(db, token)
        reviewer_sdk_tasks.drained(token)
        reviewer_transport.drained(token)
        value = Receipt(
            version=2,
            owner_id=str(token.owner_id),
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            input_hash=run.input_hash,
            binding_id=str(thread.id),
            binding_hash=thread.binding_hash,
            thread_id=thread.thread_id,
            profile_hash=thread.profile_hash,
            review_id=str(review.id),
            review_hash=review.document_hash,
            observation=observation,
            history_proof_json=completed.history.document_json,
            history_proof_hash=completed.history.document_hash,
            closure=Closure.model_validate(parse_document(completed.closure_json)),
            closure_hash=completed.closure_hash,
            claim_event_id=str(claim_event.id),
            claim_hash=claim_event.logical_hash,
            completion_event_id=str(release_event.id),
            completion_hash=release_event.logical_hash,
            completed_revision=run.revision,
            producer=epoch,
            recovery_event_id=str(recovery.id) if recovery is not None else None,
            recovery_hash=recovery.logical_hash if recovery is not None else None,
            observed_at_ms=now,
            horizon_sequence=horizon[0],
            horizon_hash=horizon[1],
        )
        thread.finish_hash = logical_hash(value.model_dump(mode="json"))
        thread.finish_checkpoint_id = observation.writer.route.checkpoint_id
        event = await ledger._append(
            db,
            scope,
            token.owner_id,
            "reviewer_sdk",
            thread.id,
            EVENT,
            value.model_dump(mode="json"),
        )
        await verify(db, token.owner_id, run, thread, **context)
        await _owned(db, token, binding)
        reviewer_sdk_tasks.require_frozen(db, token)
        await db.commit()
        return event
