"""Execution ownership only; ResearchRun/Attempt retain business authority."""

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
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ResearchExecutionLease(Base):
    __tablename__ = "research_execution_leases"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_attempts.id"), primary_key=True
    )
    worker_id: Mapped[str] = mapped_column(String(32))
    fence: Mapped[int] = mapped_column(Integer)
    producer_host: Mapped[str] = mapped_column(String(64))
    producer_pid: Mapped[int] = mapped_column(Integer)
    producer_birth: Mapped[str] = mapped_column(String(128))
    claimed_at_ms: Mapped[int] = mapped_column(BigInteger)
    heartbeat_at_ms: Mapped[int] = mapped_column(BigInteger)
    lease_until_ms: Mapped[int] = mapped_column(BigInteger)
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    released_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    __table_args__ = (
        UniqueConstraint("active_slot", name="uq_research_execution_global_slot"),
        CheckConstraint("fence > 0 AND producer_pid > 0", name="research_execution_identity"),
        CheckConstraint(
            "claimed_at_ms > 0 AND heartbeat_at_ms >= claimed_at_ms AND "
            "lease_until_ms > heartbeat_at_ms AND "
            "(released_at_ms IS NULL OR released_at_ms >= claimed_at_ms)",
            name="research_execution_times",
        ),
        CheckConstraint(
            "(released_at_ms IS NULL AND active_slot = 1 AND active_slot IS NOT NULL) OR "
            "(released_at_ms IS NOT NULL AND active_slot IS NULL)",
            name="research_execution_active_slot",
        ),
    )


class ResearchNoDispatchReceipt(Base):
    __tablename__ = "research_no_dispatch_receipts"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_attempts.id"), primary_key=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    input_hash: Mapped[str] = mapped_column(String(64))
    intent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fence: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(32))
    producer_host: Mapped[str] = mapped_column(String(64))
    producer_pid: Mapped[int] = mapped_column(Integer)
    producer_birth: Mapped[str] = mapped_column(String(128))
    released_at_ms: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(30))
    __table_args__ = (
        CheckConstraint(
            "fence > 0 AND producer_pid > 0 AND released_at_ms > 0",
            name="research_no_dispatch_identity",
        ),
        CheckConstraint("state IN ('cancelled','interrupted')", name="research_no_dispatch_state"),
    )


class ResearchExecutionIntent(Base):
    __tablename__ = "research_execution_intents"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_attempts.id"), primary_key=True
    )
    input_hash: Mapped[str] = mapped_column(String(64))
    intent_hash: Mapped[str] = mapped_column(String(64), unique=True)
    domain_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    staging_id: Mapped[str] = mapped_column(String(32), unique=True)
    fence: Mapped[int] = mapped_column(Integer)
    document_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint("fence > 0", name="research_execution_intent_fence"),)


class ResearchExecutionReceipt(Base):
    __tablename__ = "research_execution_receipts"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_execution_intents.attempt_id"), primary_key=True
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    result_sha256: Mapped[str] = mapped_column(String(64))
    result_bytes: Mapped[int] = mapped_column(BigInteger)
    document_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint(
            "result_bytes > 0 AND result_bytes <= 16777216", name="research_execution_receipt_bytes"
        ),
    )


class ResearchExecutionDispatch(Base):
    __tablename__ = "research_execution_dispatches"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_execution_intents.attempt_id"), primary_key=True
    )

    intent_hash: Mapped[str] = mapped_column(String(64))
    fence: Mapped[int] = mapped_column(Integer)
    registered_at_ms: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint(
            "fence > 0 AND registered_at_ms > 0", name="research_execution_dispatch_identity"
        ),
    )


class ResearchPublicationIntent(Base):
    __tablename__ = "research_publication_intents"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_execution_receipts.attempt_id"), primary_key=True
    )
    plan_hash: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    root_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    document_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)


class ResearchPublicationReceipt(Base):
    __tablename__ = "research_publication_receipts"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_publication_intents.attempt_id"), primary_key=True
    )
    plan_hash: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    root_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_artifacts.id"), unique=True)
    publication_fence: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(30))
    __table_args__ = (
        CheckConstraint("publication_fence > 0", name="research_publication_fence"),
        CheckConstraint("state IN ('completed','failed')", name="research_publication_state"),
    )
