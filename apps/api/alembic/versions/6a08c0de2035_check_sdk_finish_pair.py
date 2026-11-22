"""Reject partial SDK finish markers without rewriting prior append migrations."""

from alembic import op

revision = "6a08c0de2035"
down_revision = "6a08c0de2034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_harness_threads") as table:
        table.create_check_constraint(
            "research_harness_finish_pair",
            "(finish_hash IS NULL AND finish_checkpoint_id IS NULL) OR "
            "(finish_hash IS NOT NULL AND finish_checkpoint_id IS NOT NULL)",
        )


def downgrade() -> None:
    raise RuntimeError("Restore verified backup; partial finish markers cannot be accepted.")
