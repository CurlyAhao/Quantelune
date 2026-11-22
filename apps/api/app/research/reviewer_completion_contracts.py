"""Strict completion observations and consumers; documents alone grant no authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.research.reviewer_contracts import HashText, UuidText
from app.research.reviewer_recovery_contracts import Producer

OBSERVED = "reviewer.completion_observed"
RELEASED = "reviewer.completion_released"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SDKTerminal(_Record):
    invocation_id: UuidText
    entry_event_id: UuidText
    entry_hash: HashText
    terminal_kind: Literal["quiesced", "recovered"]
    terminal_event_id: UuidText
    terminal_hash: HashText


class Finance(_Record):
    charge_id: UuidText
    event_id: UuidText
    event_hash: HashText
    state: Literal["settled", "released"]


class Observation(_Record):
    version: Literal[1]
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
    review_id: UuidText
    review_hash: HashText
    before_state: Literal["running"]
    before_revision: StrictInt = Field(ge=1)
    observed_at_ms: StrictInt = Field(gt=0)
    horizon_sequence: StrictInt = Field(gt=0)
    horizon_hash: HashText
    sdk_terminal_refs: tuple[SDKTerminal, ...] = Field(min_length=1, max_length=100)
    financial_refs: tuple[Finance, ...] = Field(min_length=1, max_length=100)

    @field_validator("sdk_terminal_refs", "financial_refs", mode="before")
    @classmethod
    def tuples(cls, value):
        return tuple(value) if type(value) is list else value

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported completion version.")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if (
            (self.producer.fence == 1)
            != (self.recovery_event_id is None and self.recovery_hash is None)
            or (self.recovery_event_id is None) != (self.recovery_hash is None)
            or self.observed_at_ms < self.producer.claimed_at_ms
        ):
            raise ValueError("Completion epoch or DB time changed.")
        invocations = [r.invocation_id for r in self.sdk_terminal_refs]
        entries = [r.entry_event_id for r in self.sdk_terminal_refs]
        if len(set(invocations)) != len(invocations) or len(set(entries)) != len(entries):
            raise ValueError("Completion repeats an SDK entry.")
        charges = [r.charge_id for r in self.financial_refs]
        if charges != sorted(set(charges)) or len({r.event_id for r in self.financial_refs}) != len(
            charges
        ):
            raise ValueError("Completion financial references repeat or change order.")
        return self


class Release(_Record):
    version: Literal[1]
    owner_id: UuidText
    run_id: UuidText
    attempt_id: UuidText
    input_hash: HashText
    producer: Producer
    review_id: UuidText
    review_hash: HashText
    observation_event_id: UuidText
    observation_hash: HashText
    transition_event_id: UuidText
    transition_hash: HashText
    completed_revision: StrictInt = Field(ge=2)
    released_at_ms: StrictInt = Field(gt=0)

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported completion version.")
        return value
