"""Bind SDK threads to business attempts and retain filtered real replies."""

import sqlalchemy as sa

from alembic import op

revision = "6a08c0de2033"
down_revision = "6a08c0de2032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_harness_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id"), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("thread_id", sa.String(32), nullable=False, unique=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("attempt_id", "purpose", name="uq_research_harness_attempt_purpose"),
        sa.CheckConstraint("purpose IN ('business','assistant')", name="research_harness_purpose"),
    )
    op.create_table(
        "research_advisory_receipts",
        sa.Column("charge_id", sa.Uuid(), sa.ForeignKey("research_charges.id"), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id"), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("approved_revision", sa.Integer(), nullable=False),
        sa.Column("resumed_revision", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "approved_revision >= 0 AND (resumed_revision IS NULL OR "
            "resumed_revision = approved_revision + 1)",
            name="research_advisory_epoch",
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; SDK bindings/replies cannot be discarded.")
