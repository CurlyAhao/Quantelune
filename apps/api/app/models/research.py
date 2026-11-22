"""Formal business ledger. Old domain and preview records keep their identities."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.research.contracts import ZERO_HASH, State

_STATES = ",".join(f"'{state.value}'" for state in State)


class ResearchWorkspace(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_workspaces"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lifecycle_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    event_head_hash: Mapped[str] = mapped_column(String(64), default=ZERO_HASH)
    __table_args__ = (
        CheckConstraint("event_sequence >= 0", name="research_sequence_nonnegative"),
        CheckConstraint(
            "revision >= 0 AND lifecycle_epoch >= 0", name="research_workspace_lifecycle"
        ),
    )


class ResearchSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_sessions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_workspaces.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lifecycle_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    __table_args__ = (
        CheckConstraint(
            "revision >= 0 AND lifecycle_epoch >= 0", name="research_session_lifecycle"
        ),
    )


class ResearchAttachment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_attachments"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[uuid.UUID] = mapped_column(index=True)
    source_project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    target_sha256: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("session_id", "target_kind", "target_id", name="uq_research_attachment"),
        CheckConstraint(
            "target_kind IN ('project','dataset_version','artifact')",
            name="research_attachment_kind",
        ),
        CheckConstraint(
            "(target_kind = 'project' AND target_sha256 IS NULL) OR "
            "(target_kind != 'project' AND target_sha256 IS NOT NULL)",
            name="research_attachment_version_required",
        ),
    )


class ResearchRun(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_runs"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    input_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default=State.QUEUED.value, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    domain_kind: Mapped[str | None] = mapped_column(String(30))
    domain_run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    completion_hash: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_research_run_idempotency"),
        CheckConstraint(f"state IN ({_STATES})", name="research_run_state"),
        CheckConstraint("revision >= 0 AND attempt_count >= 0", name="research_run_counters"),
        CheckConstraint(
            "state != 'completed' OR (completion_hash IS NOT NULL AND domain_run_id IS NOT NULL "
            "AND domain_kind IS NOT NULL)",
            name="research_completed_proof",
        ),
    )


class ResearchAttempt(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_attempts"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(30), default=State.QUEUED.value)
    error_code: Mapped[str | None] = mapped_column(String(100))
    retry_key: Mapped[str | None] = mapped_column(String(200))
    base_revision: Mapped[int | None] = mapped_column(Integer)
    domain_kind: Mapped[str | None] = mapped_column(String(30))
    domain_run_id: Mapped[uuid.UUID | None] = mapped_column()
    completion_hash: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("run_id", "number", name="uq_research_attempt_number"),
        UniqueConstraint("run_id", "retry_key", name="uq_research_attempt_retry_key"),
        CheckConstraint(f"state IN ({_STATES})", name="research_attempt_state"),
        CheckConstraint("number > 0", name="research_attempt_positive"),
    )


class ResearchArtifact(Base, UUIDMixin):
    __tablename__ = "research_artifacts"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    media_type: Mapped[str] = mapped_column(String(127))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    logical_hash: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(1024), unique=True)
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("byte_size > 0", name="research_artifact_nonempty"),)


class ResearchArtifactEdge(Base, UUIDMixin):
    __tablename__ = "research_artifact_edges"

    parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_artifacts.id"), index=True)
    child_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_artifacts.id"), index=True)
    relation: Mapped[str] = mapped_column(String(50))
    __table_args__ = (
        UniqueConstraint("parent_id", "child_id", "relation", name="uq_research_artifact_edge"),
        CheckConstraint("parent_id != child_id", name="research_artifact_no_self_edge"),
    )


class ResearchMessage(Base, UUIDMixin):
    __tablename__ = "research_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_sessions.id"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_runs.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    content_json: Mapped[str] = mapped_column(Text)
    logical_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','tool','system')", name="research_message_role"
        ),
    )


class ResearchEvent(Base, UUIDMixin):
    __tablename__ = "research_events"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_workspaces.id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    entity_kind: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    payload_json: Mapped[str] = mapped_column(Text)
    logical_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str] = mapped_column(String(64))
    chain_hash: Mapped[str] = mapped_column(String(64))
    hash_version: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[str] = mapped_column(String(40))
    __table_args__ = (
        UniqueConstraint("workspace_id", "sequence", name="uq_research_event_sequence"),
        CheckConstraint("sequence > 0", name="research_event_positive"),
    )
