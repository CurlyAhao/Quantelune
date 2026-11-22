"""Add the formal research ledger; old domain rows are untouched."""

import sqlalchemy as sa

from alembic import op

revision = "6a06c0de2026"
down_revision = "aacbcf50997e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Frozen additive schema; never import mutable application models.
    op.create_table(
        "research_workspaces",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("event_head_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("event_sequence >= 0", name="research_sequence_nonnegative"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_workspaces_owner_id"), "research_workspaces", ["owner_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_workspaces_project_id"),
        "research_workspaces",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "research_events",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("entity_kind", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("logical_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("chain_hash", sa.String(length=64), nullable=False),
        sa.Column("hash_version", sa.String(length=50), nullable=False),
        sa.Column("occurred_at", sa.String(length=40), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="research_event_positive"),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["research_workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "sequence", name="uq_research_event_sequence"),
    )
    op.create_index(
        op.f("ix_research_events_entity_id"), "research_events", ["entity_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_events_workspace_id"), "research_events", ["workspace_id"], unique=False
    )
    op.create_table(
        "research_sessions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["research_workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_sessions_workspace_id"),
        "research_sessions",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "research_attachments",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("source_project_id", sa.Uuid(), nullable=False),
        sa.Column("target_sha256", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(target_kind = 'project' AND target_sha256 IS NULL) OR (target_kind != 'project' AND target_sha256 IS NOT NULL)",
            name="research_attachment_version_required",
        ),
        sa.CheckConstraint(
            "target_kind IN ('project','dataset_version','artifact')",
            name="research_attachment_kind",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["research_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "target_kind", "target_id", name="uq_research_attachment"
        ),
    )
    op.create_index(
        op.f("ix_research_attachments_session_id"),
        "research_attachments",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_attachments_source_project_id"),
        "research_attachments",
        ["source_project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_attachments_target_id"),
        "research_attachments",
        ["target_id"],
        unique=False,
    )
    op.create_table(
        "research_runs",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("domain_kind", sa.String(length=30), nullable=True),
        sa.Column("domain_run_id", sa.Uuid(), nullable=True),
        sa.Column("completion_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state != 'completed' OR (completion_hash IS NOT NULL AND domain_run_id IS NOT NULL AND domain_kind IS NOT NULL)",
            name="research_completed_proof",
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','waiting_approval','paused','cancel_requested','completed','failed','cancelled','interrupted')",
            name="research_run_state",
        ),
        sa.CheckConstraint("revision >= 0 AND attempt_count >= 0", name="research_run_counters"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["research_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_research_run_idempotency"),
    )
    op.create_index(
        op.f("ix_research_runs_domain_run_id"), "research_runs", ["domain_run_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_runs_session_id"), "research_runs", ["session_id"], unique=False
    )
    op.create_index(op.f("ix_research_runs_state"), "research_runs", ["state"], unique=False)
    op.create_table(
        "research_artifacts",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("media_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("logical_hash", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="research_artifact_nonempty"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        op.f("ix_research_artifacts_project_id"), "research_artifacts", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_artifacts_run_id"), "research_artifacts", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_artifacts_sha256"), "research_artifacts", ["sha256"], unique=False
    )
    op.create_table(
        "research_attempts",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','waiting_approval','paused','cancel_requested','completed','failed','cancelled','interrupted')",
            name="research_attempt_state",
        ),
        sa.CheckConstraint("number > 0", name="research_attempt_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "number", name="uq_research_attempt_number"),
    )
    op.create_index(
        op.f("ix_research_attempts_run_id"), "research_attempts", ["run_id"], unique=False
    )
    op.create_table(
        "research_messages",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("logical_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "role IN ('user','assistant','tool','system')", name="research_message_role"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["research_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_messages_run_id"), "research_messages", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_messages_session_id"), "research_messages", ["session_id"], unique=False
    )
    op.create_table(
        "research_artifact_edges",
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=50), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("parent_id != child_id", name="research_artifact_no_self_edge"),
        sa.ForeignKeyConstraint(
            ["child_id"],
            ["research_artifacts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["research_artifacts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "child_id", "relation", name="uq_research_artifact_edge"),
    )
    op.create_index(
        op.f("ix_research_artifact_edges_child_id"),
        "research_artifact_edges",
        ["child_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_artifact_edges_parent_id"),
        "research_artifact_edges",
        ["parent_id"],
        unique=False,
    )


def downgrade() -> None:
    # Frozen additive schema; never import mutable application models.
    op.drop_index(
        op.f("ix_research_artifact_edges_parent_id"), table_name="research_artifact_edges"
    )
    op.drop_index(op.f("ix_research_artifact_edges_child_id"), table_name="research_artifact_edges")
    op.drop_table("research_artifact_edges")
    op.drop_index(op.f("ix_research_messages_session_id"), table_name="research_messages")
    op.drop_index(op.f("ix_research_messages_run_id"), table_name="research_messages")
    op.drop_table("research_messages")
    op.drop_index(op.f("ix_research_attempts_run_id"), table_name="research_attempts")
    op.drop_table("research_attempts")
    op.drop_index(op.f("ix_research_artifacts_sha256"), table_name="research_artifacts")
    op.drop_index(op.f("ix_research_artifacts_run_id"), table_name="research_artifacts")
    op.drop_index(op.f("ix_research_artifacts_project_id"), table_name="research_artifacts")
    op.drop_table("research_artifacts")
    op.drop_index(op.f("ix_research_runs_state"), table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_session_id"), table_name="research_runs")
    op.drop_index(op.f("ix_research_runs_domain_run_id"), table_name="research_runs")
    op.drop_table("research_runs")
    op.drop_index(op.f("ix_research_attachments_target_id"), table_name="research_attachments")
    op.drop_index(
        op.f("ix_research_attachments_source_project_id"), table_name="research_attachments"
    )
    op.drop_index(op.f("ix_research_attachments_session_id"), table_name="research_attachments")
    op.drop_table("research_attachments")
    op.drop_index(op.f("ix_research_sessions_workspace_id"), table_name="research_sessions")
    op.drop_table("research_sessions")
    op.drop_index(op.f("ix_research_events_workspace_id"), table_name="research_events")
    op.drop_index(op.f("ix_research_events_entity_id"), table_name="research_events")
    op.drop_table("research_events")
    op.drop_index(op.f("ix_research_workspaces_project_id"), table_name="research_workspaces")
    op.drop_index(op.f("ix_research_workspaces_owner_id"), table_name="research_workspaces")
    op.drop_table("research_workspaces")
