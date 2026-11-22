"""Strict immutable v2 commitments; self-consistency never grants ledger authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.research import reviewer_history
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.reviewer_contracts import PROFILE, HashText, UuidText
from app.research.reviewer_recovery_contracts import Producer
from app.research.reviewer_snapshot import Observation

EVENT = "reviewer.sdk_finished"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReplyRef(_Record):
    charge_id: UuidText
    document_hash: HashText


class EventRef(_Record):
    event_id: UuidText
    event_hash: HashText


class Closure(_Record):
    version: Literal["reviewer-completed-history-v1"]
    owner_id: UuidText
    run_id: UuidText
    attempt_id: UuidText
    binding_id: UuidText
    binding_hash: HashText
    input_hash: HashText
    before_sequence: StrictInt = Field(gt=0)
    completion_sequence: StrictInt = Field(gt=0)
    raw_messages_hash: HashText
    history_hash: HashText
    history_proof_hash: HashText
    reply_refs: tuple[ReplyRef, ...] = Field(min_length=1, max_length=32)
    recorded_refs: tuple[EventRef, ...] = Field(min_length=1, max_length=32)
    tool_refs: tuple[EventRef, ...] = Field(max_length=32)

    @field_validator("reply_refs", "recorded_refs", "tool_refs", mode="before")
    @classmethod
    def tuples(cls, value):
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def ordered(self):
        if self.completion_sequence >= self.before_sequence or len(self.reply_refs) != len(
            self.recorded_refs
        ):
            raise ValueError("Completed history horizon or receipt count changed.")
        for refs, key, digest in (
            (self.reply_refs, "charge_id", "document_hash"),
            (self.recorded_refs, "event_id", "event_hash"),
            (self.tool_refs, "event_id", "event_hash"),
        ):
            pairs = [(getattr(item, key), getattr(item, digest)) for item in refs]
            if pairs != sorted(set(pairs)) or len({item[0] for item in pairs}) != len(pairs):
                raise ValueError("Completed history references repeat or change order.")
        return self


class Receipt(_Record):
    version: StrictInt
    owner_id: UuidText
    run_id: UuidText
    attempt_id: UuidText
    input_hash: HashText
    binding_id: UuidText
    binding_hash: HashText
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_hash: HashText
    review_id: UuidText
    review_hash: HashText
    observation: Observation
    history_proof_json: str = Field(max_length=reviewer_history.MAX_PROOF_BYTES)
    history_proof_hash: HashText
    closure: Closure
    closure_hash: HashText
    claim_event_id: UuidText
    claim_hash: HashText
    completion_event_id: UuidText
    completion_hash: HashText
    completed_revision: StrictInt = Field(ge=2)
    producer: Producer
    recovery_event_id: UuidText | None
    recovery_hash: HashText | None
    observed_at_ms: StrictInt = Field(gt=0)
    horizon_sequence: StrictInt = Field(gt=0)
    horizon_hash: HashText

    @model_validator(mode="after")
    def consistent(self):
        history = reviewer_history.document(
            reviewer_history.ReviewerHistoryProof(self.history_proof_json, self.history_proof_hash)
        )
        entry = self.observation.writer.entry
        closure = self.closure.model_dump(mode="json")
        if (
            self.version != 2
            or self.profile_hash != logical_hash(PROFILE)
            or self.history_proof_json != canonical_bytes(history).decode()
            or self.history_proof_hash != self.closure.history_proof_hash
            or self.closure_hash != logical_hash(closure)
            or self.closure.before_sequence != self.horizon_sequence + 1
            or self.closure.raw_messages_hash != self.observation.raw_messages_hash
            or self.closure.history_hash != self.observation.history_hash
            or history["history_hash"] != self.observation.history_hash
            or self.claim_event_id != entry.claim_event_id
            or self.claim_hash != entry.claim_hash
            or self.thread_id != entry.thread_id
            or self.observed_at_ms < max(entry.entered_at_ms, self.producer.claimed_at_ms)
            or (self.recovery_event_id is None) != (self.recovery_hash is None)
            or (self.producer.fence == 1) != (self.recovery_event_id is None)
            or (self.producer.fence == 1 and self.producer != entry.producer)
        ):
            raise ValueError("SDK finish commitments disagree.")
        for name in (
            "owner_id",
            "run_id",
            "attempt_id",
            "input_hash",
            "binding_id",
            "binding_hash",
        ):
            if any(
                value != getattr(self, name)
                for value in (getattr(entry, name), getattr(self.closure, name), history[name])
            ):
                raise ValueError("SDK finish source/history identity changed.")
        if history["profile_hash"] != self.profile_hash:
            raise ValueError("SDK finish profile changed.")
        # Applies the existing ledger 8 MiB cap, independently of the bounded
        # raw/Observation/wire fields. Do not derive a raw cap from redacted wire.
        canonical_bytes(self.model_dump(mode="json"))
        return self


def load(content: str) -> Receipt:
    value = parse_document(content)
    result = Receipt.model_validate(value)
    if value != result.model_dump(mode="json"):
        raise ValueError("SDK finish payload changed during validation.")
    return result
