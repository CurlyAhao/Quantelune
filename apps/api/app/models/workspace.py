"""Finite preview records; no replacement for domain Runs or an Artifact graph."""

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class WorkspaceRecord(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "workspace_preview_records"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(default=1)
    payload_json: Mapped[str] = mapped_column(Text)


class WorkspaceRevision(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "workspace_preview_revisions"
    __table_args__ = (UniqueConstraint("record_id", "revision"),)
    record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace_preview_records.id"))
    revision: Mapped[int]
    payload_json: Mapped[str] = mapped_column(Text)


class WorkspaceExecution(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "workspace_preview_executions"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace_preview_records.id"))
    plan_revision: Mapped[int]
    request_id: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    results_json: Mapped[str] = mapped_column(Text, default="[]")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
