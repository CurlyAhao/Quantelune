"""Verified death releases an unanswered Reader; it never creates a live token."""

import uuid

from app.models.research import ResearchEvent
from app.models.user import User
from app.research import (
    execution_lease,
    reader_control,
    reader_death,
    reader_identity,
    reader_lease,
)
from app.research.contracts import State, logical_hash, parse_document
from app.research.execution_identity import WorkerIdentity
from app.research.reader_recovery_contracts import (
    DeathObservation,
    DeathStopRecord,
    Producer,
    producer,
)
from app.services import research_service as ledger

EVENT = "reader.death_stopped"


def original(record: DeathStopRecord, root_event, claim, epoch: Producer):
    observed = record.observation
    if (
        observed.claim_event_id != str(root_event.id)
        or observed.claim_hash != root_event.logical_hash
        or observed.claim != claim
        or observed.producer != epoch
        or epoch.fence != 1
        or observed.previous_recovery_id is not None
        or observed.previous_recovery_hash is not None
    ):
        raise ValueError("Reader death stop original identity changed.")


async def prove(db, owner, run, attempt, lease, stopped_event, value):
    """Historical proof, also usable before the same transaction's transition."""
    _, scope = await ledger.run(db, run.id, owner)
    try:
        event = await db.get(
            ResearchEvent, uuid.UUID(value["death_event_id"]), populate_existing=True
        )
        if event is None:
            raise ValueError("Reader death evidence is absent.")
        document = parse_document(event.payload_json)
        record = DeathStopRecord.model_validate(document)
        root_event, claim = await reader_identity.original(db, owner, run, attempt, lease)
        original(record, root_event, claim, producer(lease))
        control = await reader_control.latest(db, owner, run, attempt)
        cancelled = await reader_control.cancelled(db, owner, run, attempt)
        if (
            event.event_type != EVENT
            or event.workspace_id != scope.id
            or event.entity_kind != "run"
            or event.entity_id != run.id
            or event.actor_id != owner
            or event.logical_hash != logical_hash(document)
            or event.logical_hash != value["death_hash"]
            or str(event.id) != value["death_event_id"]
            or event.sequence <= root_event.sequence
            or attempt.run_id != run.id
            or attempt.number != run.attempt_count
            or attempt.state != run.state
            or stopped_event.sequence != event.sequence + 1
            or record.state != value["state"]
            or record.stopped_at_ms != lease.released_at_ms
            or (run.state, run.revision)
            not in {
                (record.before_state, record.run_revision),
                (record.state, record.run_revision + 1),
            }
            or cancelled != (record.state == "cancelled")
            or (
                cancelled
                and (
                    control is None
                    or control["action"] != "cancel"
                    or control["control_index"] != record.control_index
                )
            )
            or await reader_lease.answer_event(db, attempt.id) is not None
        ):
            raise ValueError("Reader death stop causal proof changed.")
    except (ValueError, TypeError, KeyError):
        raise ledger.error("READER_STOP_UNPROVEN") from None
    return event, record


async def stop(db, observation: DeathObservation, worker: WorkerIdentity):
    if type(
        observation
    ) is not DeathObservation or observation.observer != reader_death.observer_value(worker):
        raise ledger.error("READER_DEATH_UNPROVEN")
    owner, run_id = uuid.UUID(observation.claim.owner_id), uuid.UUID(observation.claim.run_id)
    await execution_lease._boundary(db, owner)
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    existing, scope = await ledger.run(db, run_id, owner)
    if existing.state in {"interrupted", "cancelled"}:
        from app.models.research_planning import ResearchPlanningLease
        from app.research.replay import verify_business

        await verify_business(db, owner, scope.id)
        old_lease = await db.get(ResearchPlanningLease, uuid.UUID(observation.claim.attempt_id))
        if old_lease is None:
            raise ledger.error("READER_STOP_UNPROVEN")
        old_stop = await reader_lease.prove_stop(db, owner, existing, old_lease)
        if old_stop["proof"] != "verified_death":
            raise ledger.error("READER_STOP_UNPROVEN")
        old_event = await db.get(ResearchEvent, uuid.UUID(old_stop["death_event_id"]))
        old_record = DeathStopRecord.model_validate(parse_document(old_event.payload_json))
        if old_record.observation != observation:
            raise ledger.error("READER_DEATH_UNPROVEN")
        return existing
    current = await reader_death.confirm(db, observation, worker)
    run, attempt, lease = current.run, current.attempt, current.lease
    if (
        current.answer is not None
        or lease.fence != 1
        or lease.active_owner_id != lease.owner_id
        or lease.released_at_ms is not None
        or run.state not in {"running", "waiting_approval", "paused", "cancel_requested"}
    ):
        raise ledger.error("READER_STOP_UNPROVEN")
    control = await reader_control.latest(db, lease.owner_id, run, attempt)
    cancelled = await reader_control.cancelled(db, lease.owner_id, run, attempt)
    if cancelled and (control is None or control["action"] != "cancel"):
        raise ledger.error("READER_STOP_UNPROVEN")
    now = await execution_lease._boundary(db, lease.owner_id)
    record = DeathStopRecord.model_validate(
        dict(
            version=1,
            observation=observation,
            observation_hash=logical_hash(observation.model_dump(mode="json")),
            before_state=run.state,
            run_revision=run.revision,
            state="cancelled" if cancelled else "interrupted",
            control_index=control["control_index"] if cancelled else None,
            stopped_at_ms=now,
        )
    )
    _, scope = await ledger.run(db, run.id, lease.owner_id)
    event = await ledger._append(
        db, scope, lease.owner_id, "run", run.id, EVENT, record.model_dump(mode="json")
    )
    value = dict(
        attempt_id=str(attempt.id),
        fence=lease.fence,
        worker_id=lease.worker_id,
        producer_host=lease.producer_host,
        producer_pid=lease.producer_pid,
        producer_birth=lease.producer_birth,
        state=record.state,
        proof="verified_death",
        released_at_ms=now,
        death_event_id=str(event.id),
        death_hash=event.logical_hash,
    )
    # Financial rows, producer identity and original lease deadline stay intact.
    lease.active_owner_id, lease.released_at_ms, lease.pause_requested = None, now, False
    await ledger._append(db, scope, lease.owner_id, "run", run.id, "reader.stopped", value)
    await ledger.transition(db, lease.owner_id, run.id, run.revision, State(record.state))
    if cancelled:
        await ledger._append(
            db,
            scope,
            lease.owner_id,
            "run",
            run.id,
            reader_control.ACK,
            dict(
                attempt_id=str(attempt.id),
                control_index=record.control_index,
                action="cancel",
                revision=run.revision,
                proof="verified_death",
                fence=lease.fence,
                worker_id=lease.worker_id,
                death_event_id=str(event.id),
                death_hash=event.logical_hash,
            ),
        )
    await db.flush()
    return run
