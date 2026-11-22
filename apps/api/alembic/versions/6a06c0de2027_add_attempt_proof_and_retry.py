"""Retain per-attempt domain proof and explicit retry identity."""

from alembic import op
import sqlalchemy as sa

revision = "6a06c0de2027"
down_revision = "6a06c0de2026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_attempts") as batch:
        batch.add_column(sa.Column("retry_key", sa.String(200), nullable=True))
        batch.add_column(sa.Column("base_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("domain_kind", sa.String(30), nullable=True))
        batch.add_column(sa.Column("domain_run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("completion_hash", sa.String(64), nullable=True))
        batch.create_unique_constraint(
            "uq_research_attempt_retry_key", ["run_id", "retry_key"]
        )
    # Existing single-attempt formal records retain their current proof; old
    # domain tables are untouched. The calling environment owns backup/restore.
    op.execute(
        sa.text(
            "UPDATE research_attempts SET "
            "domain_kind=(SELECT domain_kind FROM research_runs WHERE id=research_attempts.run_id), "
            "domain_run_id=(SELECT domain_run_id FROM research_runs WHERE id=research_attempts.run_id), "
            "completion_hash=(SELECT completion_hash FROM research_runs WHERE id=research_attempts.run_id) "
            "WHERE number=(SELECT attempt_count FROM research_runs WHERE id=research_attempts.run_id)"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("research_attempts") as batch:
        batch.drop_constraint("uq_research_attempt_retry_key", type_="unique")
        for name in [
            "completion_hash",
            "domain_run_id",
            "domain_kind",
            "base_revision",
            "retry_key",
        ]:
            batch.drop_column(name)
