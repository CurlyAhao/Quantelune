"""Immutable source-bound research protocol versions and actor confirmations."""

import sqlalchemy as sa

from alembic import op

revision = "6a11c0de2041"
down_revision = "6a09c0de2040"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "research_protocol_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("research_sessions.id"), nullable=False),
        sa.Column(
            "context_id", sa.Uuid(), sa.ForeignKey("research_context_versions.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("protocol_hash", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("session_id", "revision", name="uq_research_protocol_revision"),
        sa.UniqueConstraint("session_id", "key_hash", name="uq_research_protocol_request"),
        sa.CheckConstraint("revision > 0", name="research_protocol_revision_positive"),
    )
    op.create_index(
        "ix_research_protocol_versions_owner_id", "research_protocol_versions", ["owner_id"]
    )
    op.create_index(
        "ix_research_protocol_versions_session_id", "research_protocol_versions", ["session_id"]
    )
    op.create_table(
        "research_protocol_activations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("research_sessions.id"), nullable=False),
        sa.Column(
            "protocol_version_id",
            sa.Uuid(),
            sa.ForeignKey("research_protocol_versions.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("session_id", "generation", name="uq_research_protocol_generation"),
        sa.UniqueConstraint(
            "session_id", "key_hash", name="uq_research_protocol_activation_request"
        ),
        sa.CheckConstraint("generation > 0", name="research_protocol_generation_positive"),
        sa.CheckConstraint(
            "action IN ('confirm','revoke')", name="research_protocol_activation_action"
        ),
    )
    op.create_index(
        "ix_research_protocol_activations_owner_id", "research_protocol_activations", ["owner_id"]
    )
    op.create_index(
        "ix_research_protocol_activations_session_id",
        "research_protocol_activations",
        ["session_id"],
    )


def downgrade():
    raise RuntimeError("Restore a verified backup; protocol/confirmation history must remain.")
