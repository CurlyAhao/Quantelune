"""Separate actual final SDK checkpoint evidence from business Plan receipt."""

import sqlalchemy as sa

from alembic import op

revision = "6a09c0de2039"
down_revision = "6a09c0de2038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_planner_sdk_finishes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id"), nullable=False),
        sa.Column(
            "thread_id", sa.Uuid(), sa.ForeignKey("research_planner_threads.id"), nullable=False
        ),
        sa.Column(
            "receipt_id", sa.Uuid(), sa.ForeignKey("research_plan_receipts.id"), nullable=False
        ),
        sa.Column("checkpoint_id", sa.String(128), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("attempt_id"),
        sa.UniqueConstraint("thread_id"),
        sa.UniqueConstraint("receipt_id"),
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; actual SDK finish evidence must remain.")
