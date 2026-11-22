"""DatasetProfile domain model: persisted data profiling results."""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ProfileStatus(str, enum.Enum):
    PENDING = "pending"
    PROFILING = "profiling"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class DatasetProfile(Base, UUIDMixin, TimestampMixin):
    """A computed data profile for a dataset at a specific content SHA."""

    __tablename__ = "dataset_profiles"

    dataset_id = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ProfileStatus] = mapped_column(
        Enum(ProfileStatus), default=ProfileStatus.PENDING, nullable=False
    )
    profiling_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(50), default="1.0.0", nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sampled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Full profile result

    # Relationships
    dataset = relationship("Dataset", back_populates="profiles")
