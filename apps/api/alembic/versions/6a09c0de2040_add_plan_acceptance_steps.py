"""Fixed actor acceptance and per-step real Run/receipt bindings; no native change."""

import sqlalchemy as sa

from alembic import op

revision = "6a09c0de2040"
down_revision = "6a09c0de2039"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "research_plan_acceptances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("research_sessions.id"), nullable=False),
        sa.Column(
            "plan_version_id", sa.Uuid(), sa.ForeignKey("research_plan_versions.id"), nullable=False
        ),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("plan_version_id"),
        sa.UniqueConstraint("session_id", "key_hash", name="uq_research_plan_acceptance_request"),
        sa.CheckConstraint("revision >= 0", name="research_plan_acceptance_revision"),
    )
    op.create_table(
        "research_plan_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "acceptance_id",
            sa.Uuid(),
            sa.ForeignKey("research_plan_acceptances.id"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(40), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("child_run_id", sa.Uuid(), sa.ForeignKey("research_runs.id")),
        sa.Column("result_attempt_id", sa.Uuid(), sa.ForeignKey("research_attempts.id")),
        sa.Column("result_json", sa.Text()),
        sa.Column("result_hash", sa.String(64)),
        *timestamps(),
        sa.UniqueConstraint("child_run_id"),
        sa.UniqueConstraint("acceptance_id", "step_id", name="uq_research_plan_step"),
        sa.CheckConstraint(
            "(result_json IS NULL) = (result_hash IS NULL)", name="research_plan_step_receipt_pair"
        ),
    )


def downgrade():
    raise RuntimeError("Restore verified backup; accepted Plan/child/receipt history must remain.")
