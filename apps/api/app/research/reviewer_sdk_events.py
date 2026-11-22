"""Reviewer SDK task observations; bookkeeping never authorizes business completion."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import select

from app.models.research import ResearchEvent
from app.research import harness_binding
from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_contracts import PROFILE, HashText, UuidText
from app.research.reviewer_recovery_contracts import Producer, producer
from app.services import research_service as ledger

ENTERED = "reviewer.sdk_entered"
QUIESCED = "reviewer.sdk_quiesced"
KINDS = frozenset({ENTERED, QUIESCED})
LIMIT = 100


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: StrictInt
    invocation_id: UuidText
    owner_id: UuidText
    run_id: UuidText
    attempt_id: UuidText
    input_hash: HashText
    claim_event_id: UuidText
    claim_hash: HashText
    producer: Producer
    recovery_event_id: UuidText | None
    recovery_hash: HashText | None
    binding_id: UuidText
    binding_hash: HashText
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    entered_at_ms: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def original_only(self):
        if (
            self.version != 1
            or self.producer.fence != 1
            or self.recovery_event_id is not None
            or self.recovery_hash is not None
            or self.entered_at_ms < self.producer.claimed_at_ms
        ):
            raise ValueError("SDK invocation has no original live epoch.")
        return self


class Quiesced(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: StrictInt
    invocation_id: UuidText
    owner_id: UuidText
    run_id: UuidText
    attempt_id: UuidText
    producer: Producer
    entry_event_id: UuidText
    entry_hash: HashText
    observed_at_ms: StrictInt = Field(gt=0)
    outcome: Literal["returned", "approval_wait", "cancelled", "rejected"]

    @model_validator(mode="after")
    def original_only(self):
        if self.version != 1 or self.producer.fence != 1:
            raise ValueError("SDK quiescence has no original live epoch.")
        return self


async def binding_before(db, owner, run, binding, before):
    """Actual immutable binding plus its unique strictly earlier ledger event."""
    _, scope = await ledger.run(db, run.id, owner)
    expected = harness_binding.document(binding)
    if (
        binding.owner_id != owner
        or binding.run_id != run.id
        or binding.purpose != "assistant"
        or binding.input_hash != run.input_hash
        or binding.profile_hash != logical_hash(PROFILE)
        or binding.binding_hash != logical_hash(expected)
        or uuid.UUID(binding.thread_id).hex != binding.thread_id
    ):
        raise ValueError("SDK binding identity changed.")
    rows = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == run.id,
                ResearchEvent.event_type == "harness.thread_bound",
            )
            .limit(LIMIT + 1)
        )
    ).all()
    matching = [row for row in rows if parse_document(row.payload_json) == expected]
    if len(rows) > LIMIT or len(matching) != 1:
        raise ValueError("SDK binding event absent or ambiguous.")
    event = matching[0]
    if (
        event.entity_kind != "run"
        or event.actor_id != owner
        or event.logical_hash != binding.binding_hash
        or event.sequence >= before
    ):
        raise ValueError("SDK binding event is not strictly prior.")


class Timeline:
    """Pure prefix projection. Actual claim/binding authorities are passed by replay."""

    def __init__(self):
        self.entries: dict[str, tuple[ResearchEvent, Entry]] = {}
        self.quiesced: dict[str, tuple[ResearchEvent, Quiesced]] = {}

    def entry(self, event, value, owner, run, binding, claim_event, claim, epoch, state):
        record = Entry.model_validate(value)
        if (
            sum(previous.attempt_id == record.attempt_id for _, previous in self.entries.values())
            >= LIMIT
            or record.invocation_id in self.entries
            or state not in {"running", "waiting_approval"}
            or run.kind != "reviewer"
            or event.event_type != ENTERED
            or event.entity_kind != "run"
            or event.entity_id != run.id
            or event.actor_id != owner
            or event.logical_hash != logical_hash(value)
            or record.owner_id != str(owner)
            or record.run_id != str(run.id)
            or record.attempt_id != str(binding.attempt_id)
            or record.attempt_id != claim.attempt_id
            or record.input_hash != run.input_hash
            or record.claim_event_id != str(claim_event.id)
            or record.claim_hash != claim_event.logical_hash
            or claim_event.sequence >= event.sequence
            or record.producer != Producer.model_validate(epoch)
            or record.producer != producer(claim)
            or record.binding_id != str(binding.id)
            or record.binding_hash != binding.binding_hash
            or record.thread_id != binding.thread_id
            or any(
                previous.attempt_id == record.attempt_id and key not in self.quiesced
                for key, (_, previous) in self.entries.items()
            )
        ):
            raise ValueError("SDK entry temporal identity or active invocation changed.")
        self.entries[record.invocation_id] = (event, record)

    def quiesce(self, event, value, owner, epoch, state):
        record = Quiesced.model_validate(value)
        previous = self.entries.get(record.invocation_id)
        if previous is None or record.invocation_id in self.quiesced:
            raise ValueError("SDK quiescence has no unique prior entry.")
        entered_event, entered = previous
        if (
            state not in {"running", "waiting_approval", "paused", "cancel_requested"}
            or event.event_type != QUIESCED
            or event.entity_kind != "run"
            or str(event.entity_id) != entered.run_id
            or event.actor_id != owner
            or event.logical_hash != logical_hash(value)
            or record.owner_id != str(owner)
            or record.owner_id != entered.owner_id
            or record.run_id != entered.run_id
            or record.attempt_id != entered.attempt_id
            or record.producer != entered.producer
            or record.producer != Producer.model_validate(epoch)
            or record.entry_event_id != str(entered_event.id)
            or record.entry_hash != entered_event.logical_hash
            or event.sequence <= entered_event.sequence
            or record.observed_at_ms < entered.entered_at_ms
        ):
            raise ValueError("SDK quiescence temporal identity changed.")
        self.quiesced[record.invocation_id] = (event, record)

    def open_for(self, attempt_id):
        return [
            key
            for key, (_, entry) in self.entries.items()
            if entry.attempt_id == str(attempt_id) and key not in self.quiesced
        ]
