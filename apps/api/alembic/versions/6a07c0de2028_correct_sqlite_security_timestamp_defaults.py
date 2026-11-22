"""Correct frozen security defaults without rewriting its migration or data.

SQLite table rebuilds require the migration connection to have FK enforcement
disabled before its transaction. The backup/restore runner must verify all
foreign keys before commit and restore enforcement; no application/user DB is
modified by merely importing this file.
"""
import sqlalchemy as sa
from alembic import op

revision = "6a07c0de2028"
down_revision = "6a07c0de2027"
branch_labels = None
depends_on = None

TABLES = ("research_charges", "research_approvals", "research_policies", "research_budget_accounts")


def upgrade() -> None:
    connection = op.get_bind()
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
            raise RuntimeError("SQLite defaults repair requires a backed-up migration connection with FK off before transaction.")
        if list(connection.exec_driver_sql("PRAGMA foreign_key_check")):
            raise RuntimeError("Existing SQLite foreign key inconsistency; repair aborted.")
    for name in TABLES:
        with op.batch_alter_table(name, recreate="always" if sqlite else "auto") as batch:
            for column in ("created_at", "updated_at"):
                batch.alter_column(column, existing_type=sa.DateTime(timezone=True),
                                   existing_nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))
    if sqlite and list(connection.exec_driver_sql("PRAGMA foreign_key_check")):
        raise RuntimeError("SQLite foreign key inconsistency after defaults repair; restore backup.")


def downgrade() -> None:
    # Correct UTC timestamps remain valid under the previous schema. Restoring
    # an unusable now() default would introduce a defect, not rollback data.
    pass
