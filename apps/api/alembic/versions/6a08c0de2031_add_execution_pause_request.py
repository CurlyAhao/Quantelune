"""Durable pause request, acknowledged only at a computation boundary."""

import sqlalchemy as sa

from alembic import op

revision = "6a08c0de2031"
down_revision = "6a08c0de2030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_execution_leases",
        sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; pending control requests cannot be discarded.")
