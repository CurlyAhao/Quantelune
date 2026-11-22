"""Strict historical Reader recovery receipts, never new source/send authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.research.reader_identity import UUID_PATTERN, ReaderClaim


class Observer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    worker_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    host: str = Field(pattern=r"^[0-9a-f]{64}$")
    pid: StrictInt = Field(gt=0)
    birth: str = Field(pattern=r"^(win:[1-9][0-9]*|linux:[0-9a-f-]{36}:[0-9]+)$")


class Producer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    fence: StrictInt = Field(gt=0)
    worker_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    producer_host: str = Field(pattern=r"^[0-9a-f]{64}$")
    producer_pid: StrictInt = Field(gt=0)
    producer_birth: str = Field(pattern=r"^(win:[1-9][0-9]*|linux:[0-9a-f-]{36}:[0-9]+)$")
    claimed_at_ms: StrictInt = Field(gt=0)


def producer(value) -> Producer:
    return Producer.model_validate({name: getattr(value, name) for name in Producer.model_fields})


class DeathObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: StrictInt
    claim_event_id: str = Field(pattern=UUID_PATTERN)
    claim_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim: ReaderClaim
    producer: Producer
    previous_recovery_id: str | None = Field(pattern=UUID_PATTERN)
    previous_recovery_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at_ms: StrictInt = Field(gt=0)
    observer: Observer
    process_state: Literal["dead", "birth_changed"]
    observed_birth: str | None

    @model_validator(mode="after")
    def exact_observation(self):
        if (
            self.version != 1
            or self.producer.claimed_at_ms != self.claim.claimed_at_ms
            or self.producer.producer_host != self.claim.producer_host
            or (self.process_state == "dead" and self.observed_birth is not None)
            or (
                self.process_state == "birth_changed"
                and (not self.observed_birth or self.observed_birth == self.producer.producer_birth)
            )
            or self.observer.host != self.producer.producer_host
            or (
                self.producer.fence == 1
                and (
                    self.previous_recovery_id is not None
                    or self.previous_recovery_hash is not None
                    or self.producer != producer(self.claim)
                )
            )
            or (
                self.producer.fence > 1
                and (self.previous_recovery_id is None or self.previous_recovery_hash is None)
            )
        ):
            raise ValueError("Reader death observation is inconsistent.")
        return self


class RecoveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: StrictInt
    observation: DeathObservation
    observation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    after: Producer
    phase: Literal["answer_only", "sdk_only"]
    answer_id: str = Field(pattern=UUID_PATTERN)
    answer_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_id: str = Field(pattern=UUID_PATTERN)
    binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_revision: StrictInt = Field(ge=0)
    active_owner_id: str | None = Field(pattern=UUID_PATTERN)
    released_at_ms: StrictInt | None
    recovered_at_ms: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def exact_successor(self):
        from app.research.contracts import logical_hash

        observed = self.observation
        if (
            self.version != 1
            or self.observation_hash != logical_hash(observed.model_dump(mode="json"))
            or self.after.fence != observed.producer.fence + 1
            or self.after.claimed_at_ms != observed.producer.claimed_at_ms
            or self.after.worker_id != observed.observer.worker_id
            or self.after.producer_host != observed.observer.host
            or self.after.producer_pid != observed.observer.pid
            or self.after.producer_birth != observed.observer.birth
            or self.recovered_at_ms < observed.observed_at_ms
            or (
                self.phase == "answer_only"
                and (
                    self.released_at_ms is not None
                    or self.active_owner_id != observed.claim.owner_id
                )
            )
            or (
                self.phase == "sdk_only"
                and (
                    self.released_at_ms is None
                    or self.released_at_ms <= 0
                    or self.released_at_ms > self.recovered_at_ms
                    or self.active_owner_id is not None
                )
            )
        ):
            raise ValueError("Reader recovery epoch or release proof changed.")
        return self


class DeathStopRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: StrictInt
    observation: DeathObservation
    observation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_state: Literal["running", "waiting_approval", "paused", "cancel_requested"]
    run_revision: StrictInt = Field(ge=0)
    state: Literal["interrupted", "cancelled"]
    control_index: StrictInt | None = Field(gt=0)
    stopped_at_ms: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def exact_stop(self):
        from app.research.contracts import State, logical_hash, require_transition

        if (
            self.version != 1
            or self.observation_hash != logical_hash(self.observation.model_dump(mode="json"))
            or self.observation.producer.fence != 1
            or self.stopped_at_ms < self.observation.observed_at_ms
            or ((self.state == "cancelled") != (self.control_index is not None))
            or (self.before_state == "cancel_requested" and self.state != "cancelled")
        ):
            raise ValueError("Reader death stop is inconsistent.")
        require_transition(State(self.before_state), State(self.state))
        return self
