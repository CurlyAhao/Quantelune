"""add READY and FROZEN to projectstatus enum

Revision ID: e5e067a07f7d
Revises: 295b93e23c28
Create Date: 2026-07-27 00:28:27.087977

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5e067a07f7d'
down_revision: Union[str, Sequence[str], None] = '295b93e23c28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add READY and FROZEN to the projectstatus enum (cannot add in a transaction)."""
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'READY'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'FROZEN'")


def downgrade() -> None:
    """PostgreSQL does not support removing enum values — no-op downgrade."""
    pass
