"""Workflow and WorkflowNode domain models."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class WorkflowStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Workflow(Base, UUIDMixin, TimestampMixin):
    """A visual analysis workflow (DAG of nodes)."""

    __tablename__ = "workflows"

    project_id = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus), default=WorkflowStatus.DRAFT, nullable=False
    )
    graph_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    project = relationship("Project", back_populates="workflows")
    nodes = relationship("WorkflowNode", back_populates="workflow", lazy="raise")
    runs = relationship(
        "WorkflowRun",
        back_populates="workflow",
        lazy="raise",
        cascade="all, delete-orphan",
        order_by="WorkflowRun.created_at.desc()",
    )


class WorkflowNode(Base, UUIDMixin, TimestampMixin):
    """A single node in a workflow graph."""

    __tablename__ = "workflow_nodes"

    workflow_id = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_type: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    workflow = relationship("Workflow", back_populates="nodes")


class WorkflowRun(Base, UUIDMixin, TimestampMixin):
    """One execution of a workflow, storing a graph snapshot + node results."""

    __tablename__ = "workflow_runs"

    workflow_id = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[WorkflowRunStatus] = mapped_column(
        Enum(WorkflowRunStatus), default=WorkflowRunStatus.PENDING, nullable=False, index=True
    )
    graph_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_seconds: Mapped[float | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    workflow = relationship("Workflow", back_populates="runs")
