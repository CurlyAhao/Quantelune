"""Formal security ledger; metadata contains no credentials or raw provider payload."""

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ResearchPolicy(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_policies"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_workspaces.id"), unique=True, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    document_json: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))


class ResearchBudgetAccount(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_budget_accounts"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    scope_kind: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[uuid.UUID] = mapped_column(index=True)
    cost_limit: Mapped[int] = mapped_column(BigInteger)
    attempt_limit: Mapped[int] = mapped_column(Integer)
    token_limit: Mapped[int] = mapped_column(BigInteger)
    time_limit_ms: Mapped[int] = mapped_column(BigInteger)
    reserved_cost: Mapped[int] = mapped_column(BigInteger, default=0)
    consumed_cost: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    consumed_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    consumed_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        UniqueConstraint("owner_id", "scope_kind", "scope_id", name="uq_research_budget_scope"),
        CheckConstraint(
            "scope_kind IN ('owner','project','workspace','run')", name="research_budget_scope"
        ),
        CheckConstraint(
            "cost_limit > 0 AND attempt_limit > 0 AND token_limit > 0 AND time_limit_ms > 0 AND "
            "reserved_cost >= 0 AND consumed_cost >= 0 AND attempts >= 0 AND "
            "reserved_tokens >= 0 AND consumed_tokens >= 0 AND "
            "reserved_ms >= 0 AND consumed_ms >= 0",
            name="research_budget_nonnegative",
        ),
        CheckConstraint(
            "reserved_cost + consumed_cost <= cost_limit AND attempts <= attempt_limit AND "
            "reserved_tokens + consumed_tokens <= token_limit AND "
            "reserved_ms + consumed_ms <= time_limit_ms",
            name="research_budget_hard_limits",
        ),
    )


class ResearchApproval(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_approvals"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_policies.id"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"), index=True)
    proposal_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[str] = mapped_column(Text)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    consumed_charge_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    __table_args__ = (
        UniqueConstraint("run_id", "proposal_key", name="uq_research_approval_proposal"),
        CheckConstraint("expires_at_ms > 0", name="research_approval_expiry"),
        CheckConstraint(
            "state IN ('pending','approved','denied','revoked','consumed')",
            name="research_approval_state",
        ),
        CheckConstraint(
            "(state = 'consumed' AND consumed_charge_id IS NOT NULL) OR "
            "(state != 'consumed' AND consumed_charge_id IS NULL)",
            name="research_approval_consumed",
        ),
    )


class ResearchCharge(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "research_charges"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_runs.id"), index=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_approvals.id"), unique=True, index=True
    )
    call_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    account_ids_json: Mapped[str] = mapped_column(Text)
    specification_json: Mapped[str] = mapped_column(Text)
    reserved_cost: Mapped[int] = mapped_column(BigInteger)
    input_token_limit: Mapped[int] = mapped_column(BigInteger)
    output_token_limit: Mapped[int] = mapped_column(BigInteger)
    time_limit_ms: Mapped[int] = mapped_column(BigInteger)
    used_cost: Mapped[int | None] = mapped_column(BigInteger)
    used_tokens: Mapped[int | None] = mapped_column(BigInteger)
    used_ms: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    settlement_hash: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("run_id", "call_key", name="uq_research_charge_call"),
        CheckConstraint(
            "state IN ('reserved','dispatched','unknown','settled','released')",
            name="research_charge_state",
        ),
        CheckConstraint(
            "reserved_cost > 0 AND input_token_limit > 0 AND output_token_limit > 0 AND "
            "time_limit_ms > 0",
            name="research_charge_reservation",
        ),
        CheckConstraint(
            "(state IN ('settled','released') AND used_cost IS NOT NULL AND "
            "used_tokens IS NOT NULL AND "
            "used_ms IS NOT NULL AND settlement_hash IS NOT NULL) OR "
            "(state NOT IN ('settled','released') AND used_cost IS NULL AND "
            "used_tokens IS NULL AND used_ms IS NULL)",
            name="research_charge_usage_proof",
        ),
        CheckConstraint(
            "(used_cost IS NULL OR (used_cost >= 0 AND used_cost <= reserved_cost)) AND "
            "(used_tokens IS NULL OR (used_tokens >= 0 AND "
            "used_tokens <= input_token_limit + output_token_limit)) AND "
            "(used_ms IS NULL OR (used_ms >= 0 AND used_ms <= time_limit_ms))",
            name="research_charge_usage_limits",
        ),
    )
