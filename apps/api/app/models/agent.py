"""Agent session persistence models — plans, steps, and session state."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class AgentSession(Base, UUIDMixin, TimestampMixin):
    """One agent conversation session for a project."""

    __tablename__ = "agent_sessions"

    project_id = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), default="Agent session", nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(20), default="advisory", nullable=False)
    data_export_policy: Mapped[str] = mapped_column(
        String(30), default="sanitized_sample", nullable=False
    )
    token_budget: Mapped[int] = mapped_column(Integer, default=100000, nullable=False)
    max_time_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    plans = relationship(
        "AgentSessionPlan", back_populates="session", lazy="selectin", cascade="all, delete-orphan"
    )


class AgentSessionPlan(Base, UUIDMixin, TimestampMixin):
    """One execution plan within an agent session."""

    __tablename__ = "agent_plans"

    session_id = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    session = relationship("AgentSession", back_populates="plans")
    steps = relationship(
        "AgentSessionStep", back_populates="plan", lazy="selectin", cascade="all, delete-orphan"
    )


class AgentSessionStep(Base, UUIDMixin):
    """One tool call within an execution plan."""

    __tablename__ = "agent_steps"

    plan_id = mapped_column(
        ForeignKey("agent_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approval_granted: Mapped[bool] = mapped_column(default=False, nullable=False)

    plan = relationship("AgentSessionPlan", back_populates="steps")
