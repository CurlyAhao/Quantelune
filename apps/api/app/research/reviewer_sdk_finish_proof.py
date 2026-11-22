"""Historical SDK finish proof; never opens a saver or re-enters live authority."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import ResearchPlanningLease
from app.research import (
    harness_binding,
    reviewer_completed_history,
    reviewer_completion,
    reviewer_finish,
)
from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_contracts import PROFILE
from app.research.reviewer_recovery_contracts import producer
from app.research.reviewer_sdk_finish_contracts import EVENT, load
from app.services import research_service as ledger


def _refuse():
    return ledger.error("REVIEWER_CHECKPOINT_UNPROVEN")


async def verify(db, owner, run, thread, *, claim_event, claim, epoch, recovery):
    """Caller supplies its already-proved original claim and event-time epoch.

    Replay passes its current prefix, not the final lease producer. The final
    lease epoch remains a separate replay/recovery obligation.
    """
    transaction = db.sync_session.get_transaction()
    if (
        transaction is None
        or not transaction.is_active
        or db.sync_session.get_nested_transaction() is not None
    ):
        raise _refuse()
    actual, scope = await ledger.run(db, run.id, owner)
    attempt = await db.get(ResearchAttempt, thread.attempt_id, populate_existing=True)
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
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(events) != 1 or attempt is None or lease is None:
        raise _refuse()
    event = events[0]
    value = load(event.payload_json)
    receipt = await reviewer_finish.domain(db, owner, actual, attempt)
    if (
        thread.owner_id != owner
        or thread.run_id != actual.id
        or thread.input_hash != actual.input_hash
        or thread.purpose != "assistant"
        or thread.profile_hash != logical_hash(PROFILE)
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or actual.kind != "reviewer"
        or actual.state != "completed"
        or run.input_hash != actual.input_hash
        or attempt.run_id != actual.id
        or attempt.number != actual.attempt_count
        or attempt.state != "completed"
        or lease.owner_id != owner
        or lease.active_owner_id is not None
        or lease.released_at_ms is None
        or event.actor_id != owner
        or event.entity_kind != "reviewer_sdk"
        or event.sequence != value.horizon_sequence + 1
        or event.sequence > scope.event_sequence
        or event.previous_hash != value.horizon_hash
        or event.logical_hash != logical_hash(value.model_dump(mode="json"))
        or thread.finish_hash != event.logical_hash
        or thread.finish_checkpoint_id != value.observation.writer.route.checkpoint_id
        or (value.owner_id, value.run_id, value.attempt_id, value.input_hash)
        != (str(owner), str(actual.id), str(attempt.id), actual.input_hash)
        or (value.binding_id, value.binding_hash, value.thread_id, value.profile_hash)
        != (str(thread.id), thread.binding_hash, thread.thread_id, thread.profile_hash)
        or (value.review_id, value.review_hash) != (str(receipt.id), receipt.document_hash)
        or value.completed_revision != actual.revision
        or value.producer != epoch
        or value.claim_event_id != str(claim_event.id)
        or value.claim_hash != claim_event.logical_hash
        or value.recovery_event_id != (str(recovery.id) if recovery is not None else None)
        or value.recovery_hash != (recovery.logical_hash if recovery is not None else None)
        or claim_event.sequence >= event.sequence
        or claim_event.workspace_id != scope.id
        or claim_event.actor_id != owner
        or claim_event.entity_kind != "run"
        or claim_event.entity_id != actual.id
        or claim_event.event_type != "reviewer.claimed"
        or parse_document(claim_event.payload_json) != claim.model_dump(mode="json")
        or claim_event.logical_hash != logical_hash(claim.model_dump(mode="json"))
    ):
        raise _refuse()
    await ledger.verify_events(db, owner, scope.id)
    if recovery is not None:
        from app.research.reviewer_recovery_contracts import RecoveryRecord

        recovered = RecoveryRecord.model_validate(parse_document(recovery.payload_json))
        if (
            recovery.sequence >= event.sequence
            or recovery.workspace_id != scope.id
            or recovery.actor_id != owner
            or recovery.entity_kind != "run"
            or recovery.entity_id != actual.id
            or recovery.event_type != "reviewer.recovered"
            or recovery.logical_hash != logical_hash(recovered.model_dump(mode="json"))
            or recovered.after != epoch
            or value.observed_at_ms < recovered.recovered_at_ms
        ):
            raise _refuse()
    release_event = await db.get(
        ResearchEvent, uuid.UUID(value.completion_event_id), populate_existing=True
    )
    if release_event is None or release_event.sequence >= event.sequence:
        raise _refuse()
    release = await reviewer_completion.prove_release(
        db, owner, actual, attempt, lease, release_event
    )
    if (
        release_event.logical_hash != value.completion_hash
        or release.completed_revision != value.completed_revision
        or release.released_at_ms > value.observed_at_ms
    ):
        raise _refuse()
    observed_event = await db.get(ResearchEvent, uuid.UUID(release.observation_event_id))
    observed = await reviewer_completion.prove_observation(
        db, owner, actual, attempt, lease, observed_event
    )
    writer = value.observation.writer
    source_event = await db.get(
        ResearchEvent, uuid.UUID(writer.entry_event_id), populate_existing=True
    )
    if (
        source_event is None
        or source_event.sequence >= observed_event.sequence
        or source_event.actor_id != owner
        or source_event.workspace_id != scope.id
        or source_event.entity_kind != "run"
        or source_event.entity_id != actual.id
        or source_event.event_type != "reviewer.sdk_entered"
        or source_event.logical_hash != writer.entry_hash
        or parse_document(source_event.payload_json) != writer.entry.model_dump(mode="json")
        or writer.entry.producer != producer(claim)
        or not any(
            ref.entry_event_id == writer.entry_event_id
            and ref.entry_hash == writer.entry_hash
            and ref.invocation_id == writer.entry.invocation_id
            for ref in observed.sdk_terminal_refs
        )
    ):
        raise _refuse()
    transition = await db.get(ResearchEvent, uuid.UUID(release.transition_event_id))
    if transition.sequence != value.closure.completion_sequence:
        raise _refuse()
    binding = harness_binding.Binding(
        **{name: getattr(thread, name) for name in harness_binding.Binding.__dataclass_fields__}
    )
    completed = await reviewer_completed_history.prove_completed_messages(
        db,
        owner,
        actual,
        attempt,
        binding,
        value.observation,
        before_sequence=event.sequence,
        completion_sequence=transition.sequence,
    )
    if (
        completed.history.document_json != value.history_proof_json
        or completed.history.document_hash != value.history_proof_hash
        or completed.closure_hash != value.closure_hash
        or parse_document(completed.closure_json) != value.closure.model_dump(mode="json")
        or db.sync_session.get_transaction() is not transaction
        or not transaction.is_active
        or db.sync_session.get_nested_transaction() is not None
    ):
        raise _refuse()
    return event
