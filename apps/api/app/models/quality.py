"""Data-quality persistence models: quality runs and their findings."""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class QualityRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class QualityRun(Base, UUIDMixin, TimestampMixin):
    """One execution of the quality rule set against a dataset."""

    __tablename__ = "quality_runs"

    dataset_id = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id = mapped_column(
        ForeignKey("dataset_profiles.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[QualityRunStatus] = mapped_column(
        Enum(QualityRunStatus), default=QualityRunStatus.PENDING, nullable=False
    )
    ruleset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    threshold_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    total_rules: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ohlcv_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sampled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    dataset = relationship("Dataset", back_populates="quality_runs")
    findings = relationship(
        "QualityFinding",
        back_populates="quality_run",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="QualityFinding.id",
    )


class QualityFinding(Base, UUIDMixin):
    """A single rule result persisted within a quality run."""

    __tablename__ = "quality_findings"

    quality_run_id = mapped_column(
        ForeignKey("quality_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rule_version: Mapped[str] = mapped_column(String(20), default="1", nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_columns_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    affected_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    threshold_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    quality_run = relationship("QualityRun", back_populates="findings")
