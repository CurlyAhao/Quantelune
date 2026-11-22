"""Mark recovered execution epoch as publication-only, origin proof immutable."""

import sqlalchemy as sa
from alembic import op

revision = "6a08c0de2030"
down_revision = "6a08c0de2029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_execution_leases",
        sa.Column("result_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; result-only epoch cannot be silently discarded.")
