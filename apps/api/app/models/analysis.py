"""AnalysisRun, StatisticalResult, and ModelRun domain models."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisRun(Base, UUIDMixin, TimestampMixin):
    """A single execution of a workflow."""

    __tablename__ = "analysis_runs"

    workflow_id = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_version: Mapped[int] = mapped_column(nullable=False)
    dataset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.PENDING, nullable=False
    )
    random_seed: Mapped[int | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    environment_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    statistical_results = relationship(
        "StatisticalResult", back_populates="analysis_run", lazy="selectin"
    )
    model_runs = relationship("ModelRun", back_populates="analysis_run", lazy="selectin")
    review_findings = relationship("ReviewFinding", back_populates="analysis_run", lazy="selectin")


class StatisticalResult(Base, UUIDMixin, TimestampMixin):
    """Result of a statistical test or analysis."""

    __tablename__ = "statistical_results"

    analysis_run_id = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    statistic: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    effect_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_interval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumption_checks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    analysis_run = relationship("AnalysisRun", back_populates="statistical_results")


class ModelRun(Base, UUIDMixin, TimestampMixin):
    """A trained ML model and its evaluation metrics."""

    __tablename__ = "model_runs"

    analysis_run_id = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_type: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature_names_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Relationships
    analysis_run = relationship("AnalysisRun", back_populates="model_runs")
