"""Immutable source snapshots and Plan versions, separate from scientific computation."""

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

from app.models.base import Base, TimestampMixin, UUIDMixin


class ResearchContextVersion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_context_versions"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"))
    source_version: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("session_id", "source_version", name="uq_research_context_version"),
    )


class ResearchPlanVersion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_plan_versions"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"))
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_context_versions.id"))
    draft_id: Mapped[uuid.UUID] = mapped_column(index=True)
    revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("draft_id", "revision", name="uq_research_plan_version"),
        UniqueConstraint("draft_id", "idempotency_key", name="uq_research_plan_request"),
        CheckConstraint("revision > 0", name="research_plan_revision_positive"),
    )


class ResearchPlanningLease(Base):
    """An owner-bounded planner claim; never reserves the native global slot."""

    __tablename__ = "research_planning_leases"
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_attempts.id"), primary_key=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    worker_id: Mapped[str] = mapped_column(String(32))
    fence: Mapped[int] = mapped_column(Integer)
    producer_host: Mapped[str] = mapped_column(String(64))
    producer_pid: Mapped[int] = mapped_column(Integer)
    producer_birth: Mapped[str] = mapped_column(String(128))
    claimed_at_ms: Mapped[int] = mapped_column(BigInteger)
    heartbeat_at_ms: Mapped[int] = mapped_column(BigInteger)
    lease_until_ms: Mapped[int] = mapped_column(BigInteger)
    active_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    released_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    __table_args__ = (
        UniqueConstraint("active_owner_id", name="uq_research_planning_owner_slot"),
        CheckConstraint("fence > 0 AND producer_pid > 0", name="research_planning_identity"),
        CheckConstraint(
            "claimed_at_ms > 0 AND heartbeat_at_ms >= claimed_at_ms AND "
            "lease_until_ms > heartbeat_at_ms AND "
            "(released_at_ms IS NULL OR released_at_ms >= claimed_at_ms)",
            name="research_planning_times",
        ),
        CheckConstraint(
            "(released_at_ms IS NULL AND active_owner_id IS NOT NULL "
            # Preserve the exact schema expression while keeping source lines bounded.
            "AND active_owner_id = owner_id) OR "
            "(released_at_ms IS NOT NULL AND active_owner_id IS NULL)",
            name="research_planning_owner_slot",
        ),
    )


class ResearchPlannerThread(Base, UUIDMixin):
    """New namespace keeps the original Harness purpose constraint unchanged."""

    __tablename__ = "research_planner_threads"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_planning_leases.attempt_id"), unique=True
    )
    thread_id: Mapped[str] = mapped_column(String(32), unique=True)
    purpose: Mapped[str] = mapped_column(String(30))
    input_hash: Mapped[str] = mapped_column(String(64))
    profile_hash: Mapped[str] = mapped_column(String(64))
    binding_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (CheckConstraint("purpose = 'planner'", name="research_planner_purpose"),)


class ResearchPlanReceipt(Base, UUIDMixin, TimestampMixin):
    """Actual immutable PlanVersion output, independent of SDK END or text."""

    __tablename__ = "research_plan_receipts"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_planning_leases.attempt_id"), unique=True
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_plan_versions.id"))
    input_hash: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[str] = mapped_column(String(64))
    fence: Mapped[int] = mapped_column(Integer)
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (CheckConstraint("fence > 0", name="research_plan_receipt_fence"),)


class ResearchPlannerSDKFinish(Base, UUIDMixin, TimestampMixin):
    """A fixed final SDK checkpoint, independent of the business Plan receipt."""

    __tablename__ = "research_planner_sdk_finishes"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_attempts.id"), unique=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_planner_threads.id"), unique=True
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_plan_receipts.id"), unique=True
    )
    checkpoint_id: Mapped[str] = mapped_column(String(128))
    state_hash: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))


class ResearchPlanAcceptance(Base, UUIDMixin, TimestampMixin):
    """An actual actor's fixed-version execution consent, never planner output."""

    __tablename__ = "research_plan_acceptances"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"))
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_plan_versions.id"), unique=True
    )
    key_hash: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    __table_args__ = (
        UniqueConstraint("session_id", "key_hash", name="uq_research_plan_acceptance_request"),
        CheckConstraint("revision >= 0", name="research_plan_acceptance_revision"),
    )


class ResearchPlanStep(Base, UUIDMixin, TimestampMixin):
    """Fixed step definition plus exact real child/receipt references; no guessed state."""

    __tablename__ = "research_plan_steps"
    acceptance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_plan_acceptances.id"))
    step_id: Mapped[str] = mapped_column(String(40))
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    child_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_runs.id"), unique=True
    )
    result_attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_attempts.id"))
    result_json: Mapped[str | None] = mapped_column(Text)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("acceptance_id", "step_id", name="uq_research_plan_step"),
        CheckConstraint(
            "(result_json IS NULL) = (result_hash IS NULL)", name="research_plan_step_receipt_pair"
        ),
    )
