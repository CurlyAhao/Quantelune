"""Statistical-analysis persistence models.

New, dedicated tables (``statistical_runs`` / ``stat_results`` /
``stat_findings``) keep the statistical domain decoupled from the generic
``analysis_runs`` scaffold and from data-quality findings. Every run records
full reproducibility metadata (dataset hash, input config, missing-value policy,
software versions).
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class StatisticalRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class StatisticalRun(Base, UUIDMixin, TimestampMixin):
    """One execution of a statistical method against a dataset."""

    __tablename__ = "statistical_runs"

    project_id = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id = mapped_column(
        ForeignKey("dataset_profiles.id", ondelete="SET NULL"), nullable=True
    )
    quality_run_id = mapped_column(
        ForeignKey("quality_runs.id", ondelete="SET NULL"), nullable=True
    )
    method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[StatisticalRunStatus] = mapped_column(
        Enum(StatisticalRunStatus), default=StatisticalRunStatus.PENDING, nullable=False, index=True
    )
    methodset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_value_policy: Mapped[str] = mapped_column(
        String(50), default="listwise_delete", nullable=False
    )
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    project = relationship("Project", back_populates="statistical_runs")
    dataset = relationship("Dataset", back_populates="statistical_runs")
    results = relationship(
        "StatResult",
        back_populates="run",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="StatResult.id",
    )
    findings = relationship(
        "StatFinding",
        back_populates="run",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="StatFinding.id",
    )


class StatResult(Base, UUIDMixin):
    """The structured result of a statistical method within a run."""

    __tablename__ = "stat_results"

    statistical_run_id = mapped_column(
        ForeignKey("statistical_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    result_type: Mapped[str] = mapped_column(String(50), default="test", nullable=False)
    statistic_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    statistic_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    degrees_of_freedom_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    alternative: Mapped[str] = mapped_column(String(20), default="two_sided", nullable=False)
    confidence_level: Mapped[float] = mapped_column(Float, default=0.95, nullable=False)
    confidence_interval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    effect_size_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    descriptive_statistics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumption_checks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    software_versions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    run = relationship("StatisticalRun", back_populates="results")


class StatFinding(Base, UUIDMixin):
    """An assumption / applicability finding persisted within a statistical run."""

    __tablename__ = "stat_findings"

    statistical_run_id = mapped_column(
        ForeignKey("statistical_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    run = relationship("StatisticalRun", back_populates="findings")
