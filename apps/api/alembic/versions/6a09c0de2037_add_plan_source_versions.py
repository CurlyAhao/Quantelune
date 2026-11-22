"""Store independently anchored immutable Plan and Context versions."""

import sqlalchemy as sa

from alembic import op

revision = "6a09c0de2037"
down_revision = "6a09c0de2036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    def common():
        return [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "session_id", sa.Uuid(), sa.ForeignKey("research_sessions.id"), nullable=False
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        ]

    op.create_table(
        "research_context_versions",
        *common(),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("session_id", "source_version", name="uq_research_context_version"),
    )
    op.create_table(
        "research_plan_versions",
        *common(),
        sa.Column(
            "context_id", sa.Uuid(), sa.ForeignKey("research_context_versions.id"), nullable=False
        ),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("draft_id", "revision", name="uq_research_plan_version"),
        sa.UniqueConstraint("draft_id", "idempotency_key", name="uq_research_plan_request"),
        sa.CheckConstraint("revision > 0", name="research_plan_revision_positive"),
    )
    op.create_index("ix_research_plan_versions_draft_id", "research_plan_versions", ["draft_id"])


def downgrade() -> None:
    raise RuntimeError(
        "Restore verified backup; original context and immutable Plan versions must remain."
    )
