"""Preserve research history while fencing new operations after lifecycle changes."""

import sqlalchemy as sa

from alembic import op

revision = "6a09c0de2036"
down_revision = "6a08c0de2035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("research_workspaces", "research_sessions"):
        op.add_column(name, sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(
            name, sa.Column("lifecycle_epoch", sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column(
        "research_sessions",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("research_workspaces") as table:
        table.create_check_constraint(
            "research_workspace_lifecycle", "revision >= 0 AND lifecycle_epoch >= 0"
        )
    with op.batch_alter_table("research_sessions") as table:
        table.create_check_constraint(
            "research_session_lifecycle", "revision >= 0 AND lifecycle_epoch >= 0"
        )


def downgrade() -> None:
    raise RuntimeError(
        "Restore verified backup; lifecycle and retained research history are durable."
    )
