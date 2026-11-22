"""Immutable provider metadata and credential intentions; never credential bytes."""

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.research.contracts import ZERO_HASH


class ResearchProviderConfiguration(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_provider_configurations"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(32), unique=True)
    document_json: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    key_state: Mapped[str] = mapped_column(String(20), default="configured")
    __table_args__ = (
        CheckConstraint(
            "key_state IN ('configured','delete_pending','deleted')",
            name="research_provider_key_state",
        ),
    )


class ResearchProviderState(Base, TimestampMixin):
    __tablename__ = "research_provider_states"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    current_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_provider_configurations.id")
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    event_head_hash: Mapped[str] = mapped_column(String(64), default=ZERO_HASH)
    __table_args__ = (
        CheckConstraint("revision >= 0 AND event_sequence >= 0", name="research_provider_counters"),
    )


class ResearchCredentialOperation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_credential_operations"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(32), index=True)
    configuration_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(30))
    result_code: Mapped[str | None] = mapped_column(String(40))
    producer_instance: Mapped[str | None] = mapped_column(String(80))
    producer_pid: Mapped[int | None] = mapped_column(Integer)
    producer_birth: Mapped[str | None] = mapped_column(String(64))
    lease_until_ms: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint("kind IN ('create','delete')", name="research_credential_kind"),
        CheckConstraint(
            "state IN ('intent','confirmed','cleanup_pending','failed','deleted')",
            name="research_credential_state",
        ),
    )


class ResearchProviderEvent(Base, UUIDMixin):
    __tablename__ = "research_provider_events"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    entity_kind: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text)
    logical_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str] = mapped_column(String(64))
    chain_hash: Mapped[str] = mapped_column(String(64))
    hash_version: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[str] = mapped_column(String(50))
    __table_args__ = (
        UniqueConstraint("owner_id", "sequence", name="uq_research_provider_event_sequence"),
        CheckConstraint("sequence > 0", name="research_provider_event_sequence"),
    )
