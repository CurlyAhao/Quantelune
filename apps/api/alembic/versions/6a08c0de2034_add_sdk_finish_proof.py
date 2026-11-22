"""Separate real business publication from final SDK checkpoint proof."""

import sqlalchemy as sa

from alembic import op

revision = "6a08c0de2034"
down_revision = "6a08c0de2033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_harness_threads", sa.Column("finish_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "research_harness_threads", sa.Column("finish_checkpoint_id", sa.String(128), nullable=True)
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; SDK completion proof cannot be discarded.")
