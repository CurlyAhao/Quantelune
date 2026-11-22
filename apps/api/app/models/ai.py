"""AI invocation audit log — records every structured AI call.

Never stores the raw API key content, the full user message content, or password/
token material. Input/output are hashed for reproducibility without leaking data.
"""

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class AIInvocation(Base, UUIDMixin, TimestampMixin):
    """One call to an AI provider (structured generate)."""

    __tablename__ = "ai_invocations"

    project_id = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    token_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    project = relationship("Project", back_populates="ai_invocations")
