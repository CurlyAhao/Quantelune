"""Bounded stop-observation documents; structural checks grant no live authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.research.contracts import State, require_transition
from app.research.reviewer_contracts import HashText, UuidText
from app.research.reviewer_recovery_contracts import Producer


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    invocation_id: UuidText
    entry_event_id: UuidText
    entry_hash: HashText
    quiesced_event_id: UuidText
    quiesced_hash: HashText


class ChargeRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    charge_id: UuidText
    event_id: UuidText
    event_hash: HashText
    state: Literal["reserved", "unknown", "settled", "released"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: StrictInt
    owner_id: UuidText
    run_id: UuidText
    attempt_id: UuidText
    input_hash: HashText
    claim_event_id: UuidText
    claim_hash: HashText
    producer: Producer
    recovery_event_id: UuidText | None
    recovery_hash: HashText | None
    binding_id: UuidText | None
    binding_hash: HashText | None
    thread_id: str | None = Field(pattern=r"^[0-9a-f]{32}$")
    before_state: Literal["running", "waiting_approval", "paused", "cancel_requested"]
    before_revision: StrictInt = Field(ge=0)
    action: Literal["failed", "interrupted", "cancel", "pause", "resume"]
    after_state: Literal[
        "failed", "interrupted", "cancelled", "paused", "running", "waiting_approval"
    ]
    control_index: StrictInt | None = Field(gt=0)
    observed_at_ms: StrictInt = Field(gt=0)
    horizon_sequence: StrictInt = Field(gt=0)
    horizon_hash: HashText
    sdk_pairs: tuple[Pair, ...] = Field(max_length=100)
    financial_refs: tuple[ChargeRef, ...] = Field(max_length=100)

    @field_validator("sdk_pairs", "financial_refs", mode="before")
    @classmethod
    def freeze_sequences(cls, value):
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def exact_shape(self):
        absent = (self.binding_id is None, self.binding_hash is None, self.thread_id is None)
        if any(absent) and (not all(absent) or self.sdk_pairs or self.financial_refs):
            raise ValueError("Pre-start boundary has a partial binding or prior operations.")
        if (
            self.version != 1
            or self.producer.fence != 1
            or self.recovery_event_id is not None
            or self.recovery_hash is not None
            or self.observed_at_ms < self.producer.claimed_at_ms
        ):
            raise ValueError("Boundary has no original producer epoch.")
        if self.action in {"failed", "interrupted", "cancel"}:
            expected = "cancelled" if self.action == "cancel" else self.action
            if self.after_state != expected or (
                (self.control_index is not None) != (self.action == "cancel")
            ):
                raise ValueError("Boundary stop action changed.")
            require_transition(State(self.before_state), State(self.after_state))
        elif (
            self.control_index is None
            or self.before_state not in {"running", "waiting_approval"}
            or self.after_state
            != (
                "paused"
                if self.action == "pause" and self.before_state == "running"
                else self.before_state
            )
        ):
            raise ValueError("Boundary control action changed.")
        if len({p.invocation_id for p in self.sdk_pairs}) != len(self.sdk_pairs):
            raise ValueError("Duplicate SDK invocation in boundary.")
        event_ids = [
            identifier
            for p in self.sdk_pairs
            for identifier in (p.entry_event_id, p.quiesced_event_id)
        ]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("SDK boundary event reference is reused.")
        charge_ids = [ref.charge_id for ref in self.financial_refs]
        if charge_ids != sorted(set(charge_ids)) or len(
            {r.event_id for r in self.financial_refs}
        ) != len(charge_ids):
            raise ValueError("Financial boundary references are duplicated or unordered.")
        return self
