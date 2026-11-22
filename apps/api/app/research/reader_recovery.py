"""Atomic Reader receipt-only successor; source/model/Answer authority never resumes."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchEvent
from app.models.research_harness import ResearchHarnessThread
from app.models.research_security import ResearchCharge
from app.research import execution_lease, harness_binding, reader_control, reader_death
from app.research.contracts import logical_hash, parse_document
from app.research.execution_identity import WorkerIdentity
from app.research.reader_contracts import ReaderToken
from app.research.reader_identity import ReaderClaim
from app.research.reader_recovery_contracts import (
    DeathObservation,
    Producer,
    RecoveryRecord,
    producer,
)
from app.services import research_service as ledger

EVENT = "reader.recovered"


def successor(record, root_event, root_claim, epoch, previous):
    observed = record.observation
    if (
        observed.claim_event_id != str(root_event.id)
        or observed.claim_hash != root_event.logical_hash
        or observed.claim != root_claim
        or observed.producer != epoch
        or observed.previous_recovery_id != (str(previous.id) if previous is not None else None)
        or observed.previous_recovery_hash
        != (previous.logical_hash if previous is not None else None)
    ):
        raise ValueError("Reader recovery original/predecessor identity changed.")
    return record.after


async def current(db, owner, run, attempt, lease):
    """Strict historical epoch chain, with no OS probe or new source capture."""
    _, scope = await ledger.run(db, run.id, owner)
    await ledger.verify_events(db, owner, scope.id)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.event_type.in_(["reader.claimed", EVENT, "reader.sdk_finished"]),
            )
            .order_by(ResearchEvent.sequence),
            execution_options={"populate_existing": True},
        )
    ).all()
    root_event, root_claim, epoch, previous = None, None, None, None
    finished = False
    try:
        for event in events:
            value = parse_document(event.payload_json)
            if event.event_type == "reader.sdk_finished":
                if value.get("run_id") == str(run.id) and value.get("attempt_id") == str(
                    attempt.id
                ):
                    finished = True
                continue
            if event.entity_id != run.id:
                continue
            if event.event_type == "reader.claimed":
                candidate = ReaderClaim.model_validate(value)
                if candidate.attempt_id != str(attempt.id):
                    continue
                if root_event is not None or any(
                    [
                        candidate.owner_id != str(owner),
                        candidate.run_id != str(run.id),
                        candidate.input_hash != run.input_hash,
                    ]
                ):
                    raise ValueError("Reader original claim changed.")
                root_event, root_claim, epoch = event, candidate, producer(candidate)
            else:
                record = RecoveryRecord.model_validate(value)
                if record.observation.claim.attempt_id != str(attempt.id):
                    continue
                if finished or root_event is None or root_claim is None or epoch is None:
                    raise ValueError("Reader recovery has no unfinished original claim.")
                epoch = successor(record, root_event, root_claim, epoch, previous)
                previous = event
            if (
                event.entity_kind != "run"
                or event.actor_id != owner
                or event.logical_hash != logical_hash(value)
            ):
                raise ValueError("Reader identity event changed.")
        if (
            root_event is None
            or root_claim is None
            or epoch is None
            or lease is None
            or attempt.run_id != run.id
            or lease.attempt_id != attempt.id
            or lease.owner_id != owner
            or epoch != producer(lease)
        ):
            raise ValueError("Reader final epoch is not its proved successor.")
        if previous is not None:
            record = RecoveryRecord.model_validate(parse_document(previous.payload_json))
            from app.research import reader_lease

            answer = await reader_lease.answer_event(db, attempt.id)
            binding = await db.get(
                ResearchHarnessThread, uuid.UUID(record.binding_id), populate_existing=True
            )
            if (
                answer is None
                or str(answer.id) != record.answer_id
                or answer.logical_hash != record.answer_hash
                or binding is None
                or binding.owner_id != owner
                or binding.run_id != run.id
                or binding.attempt_id != attempt.id
                or binding.binding_hash != record.binding_hash
                or binding.binding_hash != logical_hash(harness_binding.document(binding))
                or (
                    record.phase == "sdk_only"
                    and (
                        lease.active_owner_id is not None
                        or lease.released_at_ms != record.released_at_ms
                    )
                )
            ):
                raise ValueError("Reader historical recovery receipt or binding changed.")
            # Check the same event-time Run revision, phase, Answer ordering
            # and exact ACK epoch as replay. The replay projection uses only
            # successor(), never this authority function, so no recursion.
            from app.research.replay import verify_business

            await verify_business(db, owner, scope.id)
    except (ValueError, TypeError, KeyError):
        raise ledger.error("READER_RECOVERY_UNPROVEN") from None
    return root_event, root_claim, epoch, previous


async def recover(db, observation: DeathObservation, worker: WorkerIdentity):
    current_state = await reader_death.confirm(db, observation, worker)
    run, attempt, lease = current_state.run, current_state.attempt, current_state.lease
    answer, binding = current_state.answer, current_state.binding
    if (
        answer is None
        or binding is None
        or run.state not in {"running", "completed"}
        or lease.pause_requested
        or await reader_control.cancelled(db, lease.owner_id, run, attempt)
        or (
            run.state == "running"
            and (lease.active_owner_id != lease.owner_id or lease.released_at_ms is not None)
        )
        or (
            run.state == "completed"
            and (lease.active_owner_id is not None or lease.released_at_ms is None)
        )
    ):
        raise ledger.error("READER_RECOVERY_UNPROVEN")
    if (
        await db.scalar(
            select(ResearchCharge.id).where(
                ResearchCharge.run_id == run.id,
                ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
            )
        )
        is not None
    ):
        raise ledger.error("READER_COMPLETION_FINANCIAL_PENDING")
    now = await execution_lease._boundary(db, lease.owner_id)
    after = Producer(
        fence=lease.fence + 1,
        worker_id=worker.worker_id,
        producer_host=worker.host,
        producer_pid=worker.pid,
        producer_birth=worker.birth,
        claimed_at_ms=lease.claimed_at_ms,
    )
    record = RecoveryRecord(
        version=1,
        observation=observation,
        observation_hash=logical_hash(observation.model_dump(mode="json")),
        after=after,
        phase="sdk_only" if run.state == "completed" else "answer_only",
        answer_id=str(answer.id),
        answer_hash=answer.document_hash,
        binding_id=str(binding.id),
        binding_hash=binding.binding_hash,
        run_revision=run.revision,
        active_owner_id=str(lease.active_owner_id) if lease.active_owner_id is not None else None,
        released_at_ms=lease.released_at_ms,
        recovered_at_ms=now,
    )
    # Same fenced transaction as the final OS/state observation. No release,
    # Answer/Binding/source rewrite, charge mutation or new shared slot occurs.
    for name in Producer.model_fields:
        setattr(lease, name, getattr(after, name))
    lease.heartbeat_at_ms, lease.lease_until_ms = now, now + execution_lease.LEASE_MS
    _, scope = await ledger.run(db, run.id, lease.owner_id)
    await ledger._append(
        db, scope, lease.owner_id, "run", run.id, EVENT, record.model_dump(mode="json")
    )
    await db.flush()
    return ReaderToken(lease.owner_id, run.id, attempt.id, lease.worker_id, lease.fence)
