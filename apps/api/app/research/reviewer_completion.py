"""Owned completion observation and atomic release; never a source/send authority."""

import uuid

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import ResearchPlanningLease
from app.models.research_security import ResearchCharge
from app.models.user import User
from app.research import (
    reviewer_boundary,
    reviewer_control,
    reviewer_lease,
    reviewer_receipt,
    reviewer_sdk_tasks,
    reviewer_transport,
)
from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_completion_contracts import OBSERVED, RELEASED, Observation, Release
from app.research.reviewer_completion_prefix import collect
from app.research.reviewer_contracts import ReviewerToken
from app.research.reviewer_recovery_contracts import producer
from app.services import research_security_service as security
from app.services import research_service as ledger

_MARKER = "statforge_reviewer_completion"


@event.listens_for(Session, "after_transaction_end")
def _forget(session, transaction):
    marker = session.info.get(_MARKER)
    if marker is not None and marker[0] is transaction:
        session.info.pop(_MARKER, None)


def _envelope(event, kind, owner, scope, run, value):
    if (
        event.event_type != kind
        or event.entity_kind != "run"
        or event.entity_id != run.id
        or event.workspace_id != scope.id
        or event.actor_id != owner
        or event.logical_hash != logical_hash(value.model_dump(mode="json"))
        or value.owner_id != str(owner)
        or value.run_id != str(run.id)
        or value.input_hash != run.input_hash
    ):
        raise ValueError("Reviewer completion event identity changed.")


async def _prefix(db, owner, run, attempt, lease, scope, before, now):
    events = await reviewer_boundary._events(db, scope, before)
    binding = await reviewer_boundary._binding(db, owner, run, attempt, lease, events, before)
    if binding is None:
        raise ValueError("Completion has no actual prior assistant binding.")
    prior = events[-1] if events else None
    if prior is None:
        raise ValueError("Completion original prefix is absent.")
    proof = collect(
        events, owner, scope.id, run, attempt, binding, before - 1, prior.chain_hash, now
    )
    return proof, binding


async def prove_observation(db, owner, run, attempt, lease, observed_event):
    """Historical only; final lease producer is never substituted for the earlier epoch."""
    _, scope = await ledger.run(db, run.id, owner)
    value = Observation.model_validate(parse_document(observed_event.payload_json))
    _envelope(observed_event, OBSERVED, owner, scope, run, value)
    if (
        run.kind != "reviewer"
        or attempt is None
        or attempt.run_id != run.id
        or lease is None
        or lease.owner_id != owner
        or lease.attempt_id != attempt.id
        or value.attempt_id != str(attempt.id)
        or observed_event.sequence != value.horizon_sequence + 1
        or observed_event.previous_hash != value.horizon_hash
    ):
        raise ValueError("Completion stable Attempt/lease or horizon changed.")
    await ledger.verify_events(db, owner, scope.id)
    proof, binding = await _prefix(
        db, owner, run, attempt, lease, scope, observed_event.sequence, value.observed_at_ms
    )
    receipt = await reviewer_receipt.prove(db, owner, run, attempt)
    if (
        value.claim_event_id != str(proof.root.id)
        or value.claim_hash != proof.root.logical_hash
        or value.producer != proof.epoch
        or value.recovery_event_id != (str(proof.recovery.id) if proof.recovery else None)
        or value.recovery_hash != (proof.recovery.logical_hash if proof.recovery else None)
        or (value.binding_id, value.binding_hash, value.thread_id)
        != (str(binding.id), binding.binding_hash, binding.thread_id)
        or value.review_id != str(receipt.id)
        or value.review_hash != receipt.document_hash
        or receipt.sequence >= observed_event.sequence
        or value.sdk_terminal_refs != proof.terminals
        or value.financial_refs != proof.finances
    ):
        raise ValueError("Completion original epoch/Review or complete prefix references changed.")
    return value


async def _finances(db, run, owner, proof):
    rows = (
        await db.scalars(
            select(ResearchCharge).where(ResearchCharge.run_id == run.id).limit(101),
            execution_options={"populate_existing": True},
        )
    ).all()
    if len(rows) > 100 or {str(r.id) for r in rows} != {r.charge_id for r in proof.finances}:
        raise ledger.error("REVIEWER_COMPLETION_FINANCIAL_PENDING")
    for row in rows:
        ref = next(r for r in proof.finances if r.charge_id == str(row.id))
        actual = await db.get(ResearchEvent, uuid.UUID(ref.event_id), populate_existing=True)
        if (
            row.owner_id != owner
            or actual is None
            or security.snapshot(row) != parse_document(actual.payload_json)
        ):
            raise ledger.error("REVIEWER_COMPLETION_FINANCIAL_PENDING")


async def _owned(db, token):
    """Fresh live fields only; caller separately proves this locked prefix/epoch."""
    from app.research.execution_identity import WorkerIdentity
    from app.research.execution_lease import _boundary

    if type(token) is not ReviewerToken or type(token.fence) is not int or token.fence < 1:
        raise ledger.error("EXECUTION_FENCE_LOST")
    now = await _boundary(db, token.owner_id)
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    lease = await db.get(ResearchPlanningLease, token.attempt_id, populate_existing=True)
    actor = await db.get(User, token.owner_id, populate_existing=True)
    if (
        actor is None
        or not actor.is_active
        or run.kind != "reviewer"
        or run.state != "running"
        or attempt is None
        or attempt.run_id != run.id
        or attempt.number != run.attempt_count
        or attempt.state != run.state
        or lease is None
        or lease.owner_id != token.owner_id
        or lease.attempt_id != attempt.id
        or lease.active_owner_id != token.owner_id
        or lease.released_at_ms is not None
        or lease.pause_requested
        or lease.fence != token.fence
        or lease.worker_id != token.worker_id
        or not lease.heartbeat_at_ms <= now < lease.lease_until_ms
        or not WorkerIdentity(
            lease.worker_id, lease.producer_host, lease.producer_pid, lease.producer_birth
        ).validate_current()
    ):
        raise ledger.error("EXECUTION_FENCE_LOST")
    return run, scope, attempt, lease, now


async def observe(db, token):
    """Caller retains the root transaction until completed transition and release commit."""
    from app.research.replay import verify_business

    run, scope, lease, _ = await reviewer_lease.validate(db, token, current_source=False)
    reviewer_sdk_tasks.freeze(db, token)
    reviewer_transport.drained(token)
    if token.fence == 1:
        await verify_business(db, token.owner_id, scope.id)
    # For a recovered epoch validate() already called recovery.current(), which
    # proves this same full prefix. Do not repeat it while holding the 15s lease.
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    if lease.pause_requested or await reviewer_control.cancelled(db, token.owner_id, run, attempt):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    if (
        await db.scalar(
            select(ResearchCharge.id).where(
                ResearchCharge.run_id == run.id,
                ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
            )
        )
        is not None
    ):
        raise ledger.error("REVIEWER_COMPLETION_FINANCIAL_PENDING")
    receipt = await reviewer_receipt.prove(db, token.owner_id, run, attempt)
    run, scope, attempt, lease, now = await _owned(db, token)
    proof, binding = await _prefix(
        db, token.owner_id, run, attempt, lease, scope, scope.event_sequence + 1, now
    )
    await _finances(db, run, token.owner_id, proof)
    if proof.epoch != producer(lease):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    before = (run.state, run.revision, producer(lease))
    run, _, _, lease, final_now = await _owned(db, token)
    if before != (run.state, run.revision, producer(lease)):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    reviewer_sdk_tasks.require_frozen(db, token)
    reviewer_sdk_tasks.drained(token)
    reviewer_transport.drained(token)
    value = Observation(
        version=1,
        owner_id=str(token.owner_id),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        input_hash=run.input_hash,
        claim_event_id=str(proof.root.id),
        claim_hash=proof.root.logical_hash,
        producer=proof.epoch,
        recovery_event_id=str(proof.recovery.id) if proof.recovery else None,
        recovery_hash=proof.recovery.logical_hash if proof.recovery else None,
        binding_id=str(binding.id),
        binding_hash=binding.binding_hash,
        thread_id=binding.thread_id,
        review_id=str(receipt.id),
        review_hash=receipt.document_hash,
        before_state=run.state,
        before_revision=run.revision,
        observed_at_ms=final_now,
        horizon_sequence=scope.event_sequence,
        horizon_hash=scope.event_head_hash,
        sdk_terminal_refs=proof.terminals,
        financial_refs=proof.finances,
    )
    transaction = db.sync_session.get_transaction()
    if db.info.get(_MARKER, (None,))[0] is transaction:
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    recorded = await ledger._append(
        db, scope, token.owner_id, "run", run.id, OBSERVED, value.model_dump(mode="json")
    )
    db.info[_MARKER] = (transaction, token, recorded.id, recorded.logical_hash)
    return recorded, value, receipt


def _marker(db, owner, run):
    value = db.info.get(_MARKER)
    if (
        value is None
        or value[0] is not db.sync_session.get_transaction()
        or value[1].owner_id != owner
        or value[1].run_id != run.id
    ):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    reviewer_sdk_tasks.require_frozen(db, value[1])
    return value


async def allow_transition(db, owner, run, attempt):
    """No generic completed path can bypass this transaction's actual live observation."""
    _, token, identity, digest = _marker(db, owner, run)

    # The observation is deliberately an incomplete consumer set until this
    # transaction appends transition/release. Do not call recovery.current's
    # full replay here; the already-validated root TX marker and exact prefix
    # prove the epoch, while this final live check proves the actual owner.
    current, scope, _, lease, _ = await _owned(db, token)
    observed_event = await db.get(ResearchEvent, identity, populate_existing=True)
    if (
        observed_event is None
        or observed_event.logical_hash != digest
        or (
            scope.event_sequence != observed_event.sequence
            or scope.event_head_hash != observed_event.chain_hash
        )
    ):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    observed = await prove_observation(db, owner, run, attempt, lease, observed_event)
    if (current.state, current.revision, producer(lease)) != (
        observed.before_state,
        observed.before_revision,
        observed.producer,
    ):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    reviewer_sdk_tasks.drained(token)
    reviewer_transport.drained(token)
    return observed_event, observed


async def record_release(db, owner, run, attempt, transition):
    _, token, identity, digest = _marker(db, owner, run)
    _, scope = await ledger.run(db, run.id, owner)
    lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
    observed_event = await db.get(ResearchEvent, identity, populate_existing=True)
    if observed_event is None or observed_event.logical_hash != digest or lease is None:
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    observed = await prove_observation(db, owner, run, attempt, lease, observed_event)
    if (
        producer(lease) != observed.producer
        or lease.worker_id != token.worker_id
        or (scope.event_sequence != transition.sequence)
    ):
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    value = Release(
        version=1,
        owner_id=str(owner),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        input_hash=run.input_hash,
        producer=observed.producer,
        review_id=observed.review_id,
        review_hash=observed.review_hash,
        observation_event_id=str(observed_event.id),
        observation_hash=observed_event.logical_hash,
        transition_event_id=str(transition.id),
        transition_hash=transition.logical_hash,
        completed_revision=run.revision,
        released_at_ms=lease.released_at_ms,
    )
    recorded = await ledger._append(
        db, scope, owner, "run", run.id, RELEASED, value.model_dump(mode="json")
    )
    await prove_release(db, owner, run, attempt, lease, recorded)
    return recorded


async def prove_release(db, owner, run, attempt, lease, released_event):
    _, scope = await ledger.run(db, run.id, owner)
    value = Release.model_validate(parse_document(released_event.payload_json))
    _envelope(released_event, RELEASED, owner, scope, run, value)
    observed_event = await db.get(
        ResearchEvent, uuid.UUID(value.observation_event_id), populate_existing=True
    )
    transition = await db.get(
        ResearchEvent, uuid.UUID(value.transition_event_id), populate_existing=True
    )
    if observed_event is None or transition is None:
        raise ValueError("Completion release references absent events.")
    observed = await prove_observation(db, owner, run, attempt, lease, observed_event)
    expected_transition = dict(
        before=observed.before_state,
        state="completed",
        revision=observed.before_revision + 1,
        attempt=attempt.number,
        domain_kind="reviewer_receipt",
        domain_run_id=observed.review_id,
        completion_hash=observed.review_hash,
    )
    if (
        value.attempt_id != str(attempt.id)
        or value.observation_hash != observed_event.logical_hash
        or value.transition_hash != transition.logical_hash
        or value.producer != observed.producer
        or value.review_id != observed.review_id
        or value.review_hash != observed.review_hash
        or value.completed_revision != observed.before_revision + 1
        or value.released_at_ms < observed.observed_at_ms
        or transition.event_type != "run.state_changed"
        or transition.actor_id != owner
        or transition.entity_kind != "run"
        or transition.entity_id != run.id
        or transition.workspace_id != scope.id
        or transition.sequence != observed_event.sequence + 1
        or released_event.sequence != transition.sequence + 1
        or parse_document(transition.payload_json) != expected_transition
        or (run.state, run.revision, attempt.state)
        != ("completed", value.completed_revision, "completed")
        or (run.domain_kind, attempt.domain_kind) != ("reviewer_receipt", "reviewer_receipt")
        or str(run.domain_run_id) != value.review_id
        or str(attempt.domain_run_id) != value.review_id
        or run.completion_hash != value.review_hash
        or attempt.completion_hash != value.review_hash
        or lease.active_owner_id is not None
        or lease.released_at_ms != value.released_at_ms
    ):
        raise ValueError("Completion observation, transition or actual released row changed.")
    return value


async def terminal(db, owner, run, attempt, lease):
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(ResearchEvent.entity_id == run.id, ResearchEvent.event_type == RELEASED)
            .limit(2)
        )
    ).all()
    if len(events) != 1:
        raise ledger.error("REVIEWER_COMPLETION_UNPROVEN")
    return events[0], await prove_release(db, owner, run, attempt, lease, events[0])
