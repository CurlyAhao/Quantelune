"""Immutable execution intentions and real produced result receipts."""
import sqlalchemy as sa
from alembic import op

revision = "6a08c0de2027"
down_revision = "6a08c0de2026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_execution_intents",
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id"), primary_key=True),
        sa.Column("input_hash",sa.String(64),nullable=False),
        sa.Column("intent_hash",sa.String(64),nullable=False,unique=True),
        sa.Column("domain_id",sa.Uuid(),nullable=False,unique=True),
        sa.Column("staging_id",sa.String(32),nullable=False,unique=True),
        sa.Column("fence",sa.Integer(),nullable=False),
        sa.Column("document_json",sa.Text(),nullable=False),
        sa.CheckConstraint("fence > 0",name="research_execution_intent_fence"))
    op.create_table("research_execution_receipts",
        sa.Column("attempt_id",sa.Uuid(),sa.ForeignKey("research_execution_intents.attempt_id"),primary_key=True),
        sa.Column("manifest_sha256",sa.String(64),nullable=False),
        sa.Column("result_sha256",sa.String(64),nullable=False),
        sa.Column("result_bytes",sa.BigInteger(),nullable=False),
        sa.Column("document_json",sa.Text(),nullable=False),
        sa.CheckConstraint("result_bytes > 0 AND result_bytes <= 16777216",name="research_execution_receipt_bytes"))


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; produced evidence must not be discarded.")
