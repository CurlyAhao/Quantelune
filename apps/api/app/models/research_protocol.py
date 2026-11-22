"""Immutable research protocols and explicit actor activation receipts."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ResearchProtocolVersion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_protocol_versions"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"), index=True)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_context_versions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    protocol_hash: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("session_id", "revision", name="uq_research_protocol_revision"),
        UniqueConstraint("session_id", "key_hash", name="uq_research_protocol_request"),
        CheckConstraint("revision > 0", name="research_protocol_revision_positive"),
    )


class ResearchProtocolActivation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_protocol_activations"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"), index=True)
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_protocol_versions.id")
    )
    action: Mapped[str] = mapped_column(String(20))
    generation: Mapped[int] = mapped_column(Integer)
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    document_json: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("session_id", "generation", name="uq_research_protocol_generation"),
        UniqueConstraint("session_id", "key_hash", name="uq_research_protocol_activation_request"),
        CheckConstraint("generation > 0", name="research_protocol_generation_positive"),
        CheckConstraint(
            "action IN ('confirm','revoke')", name="research_protocol_activation_action"
        ),
    )
