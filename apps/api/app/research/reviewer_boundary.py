"""Owned Reviewer boundary observations, separate from stop/control consumers."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchHarnessThread
from app.models.research_security import ResearchCharge
from app.research import (
    harness_binding,
    reviewer_control,
    reviewer_identity,
    reviewer_lease,
    reviewer_sdk_events,
    reviewer_sdk_tasks,
    reviewer_transport,
)
from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_boundary_contracts import Observation
from app.research.reviewer_boundary_prefix import collect
from app.research.reviewer_contracts import ReviewerToken
from app.research.reviewer_recovery_contracts import producer
from app.services import research_security_service as security
from app.services import research_service as ledger

EVENT = "reviewer.boundary_observed"


def action(record, controls, pause_requested):
    """Same event-time control predicate for live recording and replay."""
    latest = controls[-1] if controls else None
    cancelled = any(value["action"] == "cancel" for value in controls)
    if record.action in {"failed", "interrupted"}:
        if cancelled or record.control_index is not None:
            raise ValueError("Boundary cannot replace cancellation with failure.")
    elif (
        latest is None
        or latest["action"] != record.action
        or latest["control_index"] != record.control_index
        or bool(pause_requested) != (record.action == "pause")
        or (record.action != "cancel" and cancelled)
    ):
        raise ValueError("Boundary has no exact prior control request.")


async def _events(db, scope, before):
    rows = (
        await db.scalars(
            select(ResearchEvent)
            .where(ResearchEvent.workspace_id == scope.id, ResearchEvent.sequence < before)
            .order_by(ResearchEvent.sequence)
            .limit(10001),
            execution_options={"populate_existing": True},
        )
    ).all()
    if len(rows) > 10000:
        raise ledger.error("VERIFY_LIMIT")
    return rows


async def _binding(db, owner, run, attempt, lease, events, before):
    prior = [
        event
        for event in events
        if event.entity_id == run.id and event.event_type == "harness.thread_bound"
    ]
    if not prior:
        return None  # Historical absence is as-of the prefix, not a later row.
    rows = (
        await db.scalars(
            select(ResearchHarnessThread)
            .where(
                ResearchHarnessThread.attempt_id == attempt.id,
                ResearchHarnessThread.purpose == "assistant",
            )
            .limit(2),
            execution_options={"populate_existing": True},
        )
    ).all()
    if len(rows) != 1:
        raise ValueError("Boundary has no unique actual thread.")
    binding = rows[0]
    token = ReviewerToken(owner, run.id, attempt.id, lease.worker_id, lease.fence)
    harness_binding._verify(binding, token, run)
    await reviewer_sdk_events.binding_before(db, owner, run, binding, before)
    return binding


def _references(events, record, scope_id):
    pairs, finances = collect(
        events,
        uuid.UUID(record.owner_id),
        scope_id,
        uuid.UUID(record.run_id),
        uuid.UUID(record.attempt_id),
        record.binding_hash,
        record.horizon_sequence,
        record.horizon_hash,
        record.observed_at_ms,
    )
    if (pairs, finances) != (record.sdk_pairs, record.financial_refs):
        raise ValueError("Boundary does not commit the complete earlier SDK/financial prefix.")
    by_id = {str(event.id): event for event in events}
    for pair in pairs:
        entered = reviewer_sdk_events.Entry.model_validate(
            parse_document(by_id[pair.entry_event_id].payload_json)
        )
        if (
            entered.producer != record.producer
            or entered.claim_event_id != record.claim_event_id
            or entered.claim_hash != record.claim_hash
            or entered.input_hash != record.input_hash
            or entered.binding_id != record.binding_id
            or entered.thread_id != record.thread_id
        ):
            raise ValueError("Boundary SDK pair belongs to a different claim/binding.")


async def prove(db, owner, run, attempt, lease, event):
    """Historical proof only: no OS probe, new source or later finance-state substitution."""
    _, scope = await ledger.run(db, run.id, owner)
    record = Observation.model_validate(parse_document(event.payload_json))
    await ledger.verify_events(db, owner, scope.id)
    root = await db.get(ResearchEvent, uuid.UUID(record.claim_event_id), populate_existing=True)
    if root is None or attempt is None or lease is None:
        raise ValueError("Boundary original claim or stable lease identity is absent.")
    claim = reviewer_identity.ReviewerClaim.model_validate(parse_document(root.payload_json))
    if (
        run.kind != "reviewer"
        or attempt.run_id != run.id
        or lease.owner_id != owner
        or lease.attempt_id != attempt.id
        or root.event_type != reviewer_identity.EVENT
        or root.entity_kind != "run"
        or root.entity_id != run.id
        or root.workspace_id != scope.id
        or root.actor_id != owner
        or root.logical_hash != logical_hash(claim.model_dump(mode="json"))
        or claim.owner_id != str(owner)
        or claim.run_id != str(run.id)
        or claim.attempt_id != str(attempt.id)
        or claim.input_hash != run.input_hash
        or event.event_type != EVENT
        or event.entity_kind != "run"
        or event.entity_id != run.id
        or event.workspace_id != scope.id
        or event.actor_id != owner
        or event.logical_hash != logical_hash(record.model_dump(mode="json"))
        or record.owner_id != str(owner)
        or record.run_id != str(run.id)
        or record.attempt_id != str(attempt.id)
        or record.input_hash != run.input_hash
        or record.claim_event_id != str(root.id)
        or record.claim_hash != root.logical_hash
        or record.producer != producer(claim)
        or event.sequence != record.horizon_sequence + 1
        or event.previous_hash != record.horizon_hash
        or root.sequence >= event.sequence
    ):
        raise ValueError("Boundary original identity or chain horizon changed.")
    events = await _events(db, scope, event.sequence)
    claims = [
        row
        for row in events
        if row.event_type == reviewer_identity.EVENT
        and row.entity_id == run.id
        and parse_document(row.payload_json).get("attempt_id") == str(attempt.id)
    ]
    if len(claims) != 1 or claims[0].id != root.id:
        raise ValueError("Boundary original claim is absent or duplicated in its prefix.")
    binding = await _binding(db, owner, run, attempt, lease, events, event.sequence)
    actual_binding = (
        (str(binding.id), binding.binding_hash, binding.thread_id)
        if binding is not None
        else (None, None, None)
    )
    if actual_binding != (record.binding_id, record.binding_hash, record.thread_id):
        raise ValueError("Boundary binding presence/identity changed.")
    _references(events, record, scope.id)
    return record


async def observe(db, token, requested_action, after_state, control_index=None):
    """Caller owns commit and exact following stop/ACK; this event alone grants neither."""
    from app.research.replay import verify_business

    run, scope, lease, _ = await reviewer_lease.validate(
        db, token, running=False, current_source=False
    )
    if token.fence != 1:
        raise ledger.error("REVIEWER_RECEIPT_ONLY")
    reviewer_sdk_tasks.freeze(db, token)
    reviewer_transport.drained(token)
    await verify_business(db, token.owner_id, scope.id)
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    root, claim = await reviewer_identity.original(db, token.owner_id, run, attempt, lease)
    if (
        requested_action != "cancel"
        and await reviewer_lease.review_event(db, attempt.id) is not None
    ):
        raise ledger.error("REVIEWER_RECEIPT_ONLY")
    before = scope.event_sequence + 1
    events = await _events(db, scope, before)
    binding = await _binding(db, token.owner_id, run, attempt, lease, events, before)
    if (
        binding is None
        and await db.scalar(
            select(ResearchHarnessThread.id).where(ResearchHarnessThread.attempt_id == attempt.id)
        )
        is not None
    ):
        raise ledger.error("REVIEWER_BOUNDARY_UNPROVEN")
    # Recheck actual producer/lease under the same lock and use its final DB time.
    run, _, lease, now = await reviewer_lease.validate(
        db, token, running=False, current_source=False
    )
    pairs, finances = collect(
        events,
        token.owner_id,
        scope.id,
        run.id,
        attempt.id,
        binding.binding_hash if binding is not None else None,
        scope.event_sequence,
        scope.event_head_hash,
        now,
    )
    charges = (
        await db.scalars(
            select(ResearchCharge).where(ResearchCharge.run_id == run.id).limit(101),
            execution_options={"populate_existing": True},
        )
    ).all()
    if len(charges) > 100 or {str(row.id) for row in charges} != {r.charge_id for r in finances}:
        raise ledger.error("REVIEWER_BOUNDARY_UNPROVEN")
    by_id = {str(event.id): event for event in events}
    indexed = {ref.charge_id: ref for ref in finances}
    for charge in charges:
        reference = indexed[str(charge.id)]
        if charge.owner_id != token.owner_id or security.snapshot(charge) != parse_document(
            by_id[reference.event_id].payload_json
        ):
            raise ledger.error("REVIEWER_BOUNDARY_UNPROVEN")
    value = Observation(
        version=1,
        owner_id=str(token.owner_id),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        input_hash=run.input_hash,
        claim_event_id=str(root.id),
        claim_hash=root.logical_hash,
        producer=producer(claim),
        recovery_event_id=None,
        recovery_hash=None,
        binding_id=str(binding.id) if binding is not None else None,
        binding_hash=binding.binding_hash if binding is not None else None,
        thread_id=binding.thread_id if binding is not None else None,
        before_state=run.state,
        before_revision=run.revision,
        action=requested_action,
        after_state=after_state,
        control_index=control_index,
        observed_at_ms=now,
        horizon_sequence=scope.event_sequence,
        horizon_hash=scope.event_head_hash,
        sdk_pairs=pairs,
        financial_refs=finances,
    )
    controls = [
        value
        for kind, value in await reviewer_control.rows(db, token.owner_id, run, attempt)
        if kind == reviewer_control.REQUEST
    ]
    action(value, controls, lease.pause_requested)
    _references(events, value, scope.id)
    current, _, lease, final_now = await reviewer_lease.validate(
        db, token, running=False, current_source=False
    )
    if (current.state, current.revision, producer(lease)) != (
        value.before_state,
        value.before_revision,
        value.producer,
    ):
        raise ledger.error("REVIEWER_BOUNDARY_UNPROVEN")
    value = Observation.model_validate(
        {**value.model_dump(mode="json"), "observed_at_ms": final_now}
    )
    reviewer_sdk_tasks.drained(token)
    reviewer_transport.drained(token)
    event = await ledger._append(
        db, scope, token.owner_id, "run", run.id, EVENT, value.model_dump(mode="json")
    )
    return event, value
