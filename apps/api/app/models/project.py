"""Project domain model."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    READY = "ready"
    FROZEN = "frozen"
    ARCHIVED = "archived"


class TaskType(str, enum.Enum):
    CLASSIFICATION_BINARY = "classification_binary"
    CLASSIFICATION_MULTICLASS = "classification_multiclass"
    REGRESSION = "regression"
    CLUSTERING = "clustering"
    ANOMALY_DETECTION = "anomaly_detection"
    STATISTICAL_ANALYSIS = "statistical_analysis"


class Project(Base, UUIDMixin, TimestampMixin):
    """A StatForge analysis project."""

    __tablename__ = "projects"

    @property
    def workspace_path(self) -> str | None:
        from app.services.local_workspaces import project_root

        root = project_root(self.id)
        return str(root) if root else None

    owner_id = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_type: Mapped[TaskType | None] = mapped_column(Enum(TaskType), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.DRAFT, nullable=False
    )
    current_revision: Mapped[int] = mapped_column(default=1, nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    owner = relationship("User", back_populates="projects")
    datasets = relationship("Dataset", back_populates="project", lazy="selectin")
    # Workflow graphs/history require explicit owner + input-dataset authorization.
    workflows = relationship("Workflow", back_populates="project", lazy="raise")
    audit_events = relationship("AuditEvent", back_populates="project", lazy="selectin")
    statistical_runs = relationship(
        "StatisticalRun",
        back_populates="project",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    ai_invocations = relationship(
        "AIInvocation",
        back_populates="project",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    ml_experiments = relationship(
        "MLExperiment",
        back_populates="project",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
