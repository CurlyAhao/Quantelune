"""Durable publication ownership and actual business commit receipt."""

import sqlalchemy as sa
from alembic import op

revision = "6a08c0de2029"
down_revision = "6a08c0de2028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_publication_intents",
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("research_execution_receipts.attempt_id"),
            primary_key=True,
        ),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("root_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "research_publication_receipts",
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("research_publication_intents.attempt_id"),
            primary_key=True,
        ),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "root_id",
            sa.Uuid(),
            sa.ForeignKey("research_artifacts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("publication_fence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.CheckConstraint("publication_fence > 0", name="research_publication_fence"),
        sa.CheckConstraint("state IN ('completed','failed')", name="research_publication_state"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Restore verified backup; business publication evidence must not be discarded."
    )
