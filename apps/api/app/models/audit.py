"""AuditEvent domain model."""

import uuid

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class AuditEvent(Base, UUIDMixin, TimestampMixin):
    """An immutable audit log entry.

    ``project_id`` is optional so that account-level events (login, logout)
    can be recorded. ``actor_id`` has no hard FK so audit rows survive user
    deletion. Sensitive data (passwords, tokens, cookies, file contents) must
    never be stored here.
    """

    __tablename__ = "audit_events"

    project_id = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    project = relationship("Project", back_populates="audit_events")
