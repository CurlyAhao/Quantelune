"""Register dispatch before worker side effects; missing output stays unknown."""
import sqlalchemy as sa
from alembic import op

revision = "6a08c0de2028"
down_revision = "6a08c0de2027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_execution_dispatches",
        sa.Column("attempt_id",sa.Uuid(),sa.ForeignKey("research_execution_intents.attempt_id"),primary_key=True),
        sa.Column("intent_hash",sa.String(64),nullable=False),
        sa.Column("fence",sa.Integer(),nullable=False),
        sa.Column("registered_at_ms",sa.BigInteger(),nullable=False),
        sa.CheckConstraint("fence > 0 AND registered_at_ms > 0",name="research_execution_dispatch_identity"))


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; dispatch evidence must not be discarded.")
