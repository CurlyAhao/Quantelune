"""Add producer identities; legacy unknown intentions stay protected."""
import sqlalchemy as sa
from alembic import op

revision = "6a07c0de2029"
down_revision = "6a07c0de2028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_credential_operations", sa.Column("producer_instance", sa.String(80), nullable=True))
    op.add_column("research_credential_operations", sa.Column("producer_pid", sa.Integer(), nullable=True))
    op.add_column("research_credential_operations", sa.Column("producer_birth", sa.String(64), nullable=True))
    op.add_column("research_credential_operations", sa.Column("lease_until_ms", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Restore the pre-upgrade verified backup; discarding producer evidence is not a safe downgrade.")
