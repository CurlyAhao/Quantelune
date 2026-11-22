"""Independent planner ownership and durable PlanVersion receipt."""

import sqlalchemy as sa

from alembic import op

revision = "6a09c0de2038"
down_revision = "6a09c0de2037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_planning_leases",
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id"), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("worker_id", sa.String(32), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("producer_host", sa.String(64), nullable=False),
        sa.Column("producer_pid", sa.Integer(), nullable=False),
        sa.Column("producer_birth", sa.String(128), nullable=False),
        sa.Column("claimed_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("heartbeat_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("lease_until_ms", sa.BigInteger(), nullable=False),
        sa.Column("active_owner_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("released_at_ms", sa.BigInteger()),
        sa.Column("pause_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.UniqueConstraint("active_owner_id", name="uq_research_planning_owner_slot"),
        sa.CheckConstraint("fence > 0 AND producer_pid > 0", name="research_planning_identity"),
        sa.CheckConstraint(
            "claimed_at_ms > 0 AND heartbeat_at_ms >= claimed_at_ms AND "
            "lease_until_ms > heartbeat_at_ms AND "
            "(released_at_ms IS NULL OR released_at_ms >= claimed_at_ms)",
            name="research_planning_times",
        ),
        sa.CheckConstraint(
            "(released_at_ms IS NULL AND active_owner_id IS NOT NULL "
            # Exact ORM/migration expression; no relational guard is weakened.
            "AND active_owner_id = owner_id) OR "
            "(released_at_ms IS NOT NULL AND active_owner_id IS NULL)",
            name="research_planning_owner_slot",
        ),
    )
    op.create_table(
        "research_planner_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("research_planning_leases.attempt_id"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("attempt_id"),
        sa.UniqueConstraint("thread_id"),
        sa.CheckConstraint("purpose = 'planner'", name="research_planner_purpose"),
    )
    op.create_table(
        "research_plan_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("research_planning_leases.attempt_id"),
            nullable=False,
        ),
        sa.Column(
            "plan_version_id", sa.Uuid(), sa.ForeignKey("research_plan_versions.id"), nullable=False
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("attempt_id"),
        sa.CheckConstraint("fence > 0", name="research_plan_receipt_fence"),
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; planner claims and actual receipts must remain.")
