"""Machine-learning persistence models: experiments and their runs.

An experiment defines the task (features/target/split/preprocessing/models);
each run is a reproducible execution storing metrics, leakage checks,
comparison, interpretation and a serialized pipeline artifact in object storage.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class MLRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MLExperiment(Base, UUIDMixin, TimestampMixin):
    """A configured ML task ready to be run (and re-run) reproducibly."""

    __tablename__ = "ml_experiments"

    project_id = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id = mapped_column(
        ForeignKey("dataset_profiles.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_variable: Mapped[str] = mapped_column(String(255), nullable=False)
    feature_variables_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    categorical_variables_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    project = relationship("Project", back_populates="ml_experiments")
    dataset = relationship("Dataset", back_populates="ml_experiments")
    runs = relationship(
        "MLRun",
        back_populates="experiment",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="MLRun.created_at.desc()",
    )


class MLRun(Base, UUIDMixin, TimestampMixin):
    """One reproducible execution of an ML experiment."""

    __tablename__ = "ml_runs"

    experiment_id = mapped_column(
        ForeignKey("ml_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[MLRunStatus] = mapped_column(
        Enum(MLRunStatus), default=MLRunStatus.PENDING, nullable=False, index=True
    )
    random_seed: Mapped[int] = mapped_column(Integer, default=42, nullable=False)
    validation_strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    split_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    comparison_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    leakage_checks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_observations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifact_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    software_versions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    experiment = relationship("MLExperiment", back_populates="runs")
