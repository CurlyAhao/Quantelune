"""Durable server execution fencing; Run/Attempt remain sole business state."""
import sqlalchemy as sa
from alembic import op

revision = "6a08c0de2026"
down_revision = "6a07c0de2029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_execution_leases",
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id"), primary_key=True),
        sa.Column("worker_id", sa.String(32), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("producer_host", sa.String(64), nullable=False),
        sa.Column("producer_pid", sa.Integer(), nullable=False),
        sa.Column("producer_birth", sa.String(128), nullable=False),
        sa.Column("claimed_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("heartbeat_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("lease_until_ms", sa.BigInteger(), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("released_at_ms", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("active_slot", name="uq_research_execution_global_slot"),
        sa.CheckConstraint("fence > 0 AND producer_pid > 0", name="research_execution_identity"),
        sa.CheckConstraint("claimed_at_ms > 0 AND heartbeat_at_ms >= claimed_at_ms AND lease_until_ms > heartbeat_at_ms AND (released_at_ms IS NULL OR released_at_ms >= claimed_at_ms)", name="research_execution_times"),
        sa.CheckConstraint("(released_at_ms IS NULL AND active_slot = 1 AND active_slot IS NOT NULL) OR (released_at_ms IS NOT NULL AND active_slot IS NULL)", name="research_execution_active_slot"),
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; execution ownership evidence must not be discarded.")
