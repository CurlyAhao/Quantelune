"""ReviewFinding domain model."""

import enum

from sqlalchemy import Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class FindingSource(str, enum.Enum):
    RULE_ENGINE = "rule_engine"
    AI_REVIEW = "ai_review"
    USER = "user"


class FindingSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingResolution(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RESOLVED = "resolved"


class ReviewFinding(Base, UUIDMixin, TimestampMixin):
    """A finding from the rule engine or AI review."""

    __tablename__ = "review_findings"

    analysis_run_id = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[FindingSource] = mapped_column(Enum(FindingSource), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[FindingSeverity] = mapped_column(Enum(FindingSeverity), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution: Mapped[FindingResolution] = mapped_column(
        Enum(FindingResolution), default=FindingResolution.PENDING, nullable=False
    )

    # Relationships
    analysis_run = relationship("AnalysisRun", back_populates="review_findings")
