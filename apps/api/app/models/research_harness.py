"""Server-only SDK thread binding and filtered real advisory receipts."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class ResearchHarnessThread(Base, UUIDMixin):
    __tablename__ = "research_harness_threads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_attempts.id"))
    purpose: Mapped[str] = mapped_column(String(30))
    thread_id: Mapped[str] = mapped_column(String(32), unique=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    profile_hash: Mapped[str] = mapped_column(String(64))
    binding_hash: Mapped[str] = mapped_column(String(64))
    finish_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finish_checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    __table_args__ = (
        UniqueConstraint("attempt_id", "purpose", name="uq_research_harness_attempt_purpose"),
        CheckConstraint("purpose IN ('business','assistant')", name="research_harness_purpose"),
        CheckConstraint(
            "(finish_hash IS NULL AND finish_checkpoint_id IS NULL) OR "
            "(finish_hash IS NOT NULL AND finish_checkpoint_id IS NOT NULL)",
            name="research_harness_finish_pair",
        ),
    )


class ResearchAdvisoryReceipt(Base):
    __tablename__ = "research_advisory_receipts"

    charge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_charges.id"), primary_key=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_attempts.id"))
    input_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    document_hash: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[str] = mapped_column(Text)
    approved_revision: Mapped[int] = mapped_column(Integer)
    resumed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "approved_revision >= 0 AND (resumed_revision IS NULL OR "
            "resumed_revision = approved_revision + 1)",
            name="research_advisory_epoch",
        ),
    )
