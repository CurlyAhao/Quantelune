"""Record a verified never-dispatched stop for bounded original Run retry."""

import sqlalchemy as sa

from alembic import op

revision = "6a08c0de2032"
down_revision = "6a08c0de2031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_no_dispatch_receipts",
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id"), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("intent_hash", sa.String(64), nullable=True),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(32), nullable=False),
        sa.Column("producer_host", sa.String(64), nullable=False),
        sa.Column("producer_pid", sa.Integer(), nullable=False),
        sa.Column("producer_birth", sa.String(128), nullable=False),
        sa.Column("released_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.CheckConstraint(
            "fence > 0 AND producer_pid > 0 AND released_at_ms > 0",
            name="research_no_dispatch_identity",
        ),
        sa.CheckConstraint(
            "state IN ('cancelled','interrupted')", name="research_no_dispatch_state"
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; never-dispatched receipts cannot be discarded.")
