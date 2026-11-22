"""Verified PostgreSQL boot before business connections; lifetime kernel lease."""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, Connection, Engine

from alembic import command
from app.models.base import Base
from app.research import harness_postgres_schema as sdk
from app.research import migration as base
from app.research import postgres_migration as pg
from app.research import postgres_profiles as profile
from app.research.harness_postgres_proof import require_configuration


@dataclass
class PostgresLifecycle:
    connection: Connection
    engine: Engine
    source: URL
    backend_pid: int = 0

    def make_ready(self) -> None:
        # Downgrade without a gap: the same session acquires shared first.
        # Every business transaction also holds shared. If this session dies,
        # an in-flight transaction still excludes a new exclusive boot/migrate.
        self.connection.execute(
            sa.text("SELECT pg_advisory_lock_shared(:key)"), {"key": pg._LOCK_KEY}
        )
        if not self.connection.execute(
            sa.text("SELECT pg_advisory_unlock(:key)"), {"key": pg._LOCK_KEY}
        ).scalar_one():
            raise base.MigrationRefusedError("PostgreSQL startup lock ownership was lost.")
        self.connection.commit()
        self.verify()

    def release(self) -> None:
        # Closing this dedicated connection releases its session advisory lock.
        self.connection.close()
        self.engine.dispose()

    def verify(self) -> None:
        if self.connection.closed or self.connection.invalidated:
            raise base.MigrationRefusedError("PostgreSQL lifecycle connection was lost.")
        try:
            # A lost connection must never reconnect and silently reacquire a
            # lock while another backend can already own the database.
            actual = self.connection.execute(
                sa.text("""
                SELECT pg_backend_pid(), EXISTS (
                  SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
                    AND classid=:high AND objid=:low AND objsubid=1 AND granted AND mode='ShareLock'
                )
            """),
                {"high": pg._LOCK_KEY >> 32, "low": pg._LOCK_KEY & 0xFFFFFFFF},
            ).one()
            self.connection.rollback()
            if actual != (self.backend_pid, True):
                raise base.MigrationRefusedError("PostgreSQL lifecycle lock ownership was lost.")
        except sa.exc.SQLAlchemyError:
            raise base.MigrationRefusedError("PostgreSQL lifecycle connection was lost.") from None


def prepare_postgres_database(
    source: URL,
    *,
    runtime: Path | None = None,
    evidence: Path | None = None,
    restore: URL | None = None,
) -> PostgresLifecycle:
    """Never create/stamp an existing schema or select a restore target implicitly."""
    require_configuration()  # Refuse missing durable configuration before source mutation.
    pg._validate_url(source)
    pg._environment(source)  # Enforce verified TLS for non-loopback endpoints.
    engine = sa.create_engine(
        source, poolclass=sa.pool.NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        connection = engine.connect()
    except sa.exc.SQLAlchemyError:
        engine.dispose()
        raise base.MigrationRefusedError("PostgreSQL trusted startup connection failed.") from None
    lease = PostgresLifecycle(connection, engine, source)
    try:
        # Session lock on this exact source connection also conflicts with the
        # old migration xact lock. It survives migration commit and HTTP death.
        connection.exec_driver_sql("SET statement_timeout='30s'")
        connection.exec_driver_sql("SET lock_timeout='5s'")
        if not connection.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": pg._LOCK_KEY}
        ).scalar_one():
            raise base.MigrationRefusedError("Another PostgreSQL lifecycle owns the lock.")
        pg._connection_identity(connection, source)
        lease.backend_pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        connection.commit()
        head = ScriptDirectory.from_config(base._config()).get_current_head()
        if head is None:
            raise base.MigrationRefusedError("A single known PostgreSQL head is required.")
        accepted = profile.profiles(head)
        unversioned_baseline = None
        fresh, sdk_present = False, False
        with connection.begin():
            if pg._empty_restore(connection):
                import app.models  # noqa: F401

                Base.metadata.create_all(connection)
                config = base._config()
                config.attributes["connection"] = connection
                command.stamp(config, head)
                profile.verify_stage(connection, head, accepted, sdk_present=False)
                revision = head
                fresh = True
            else:
                sdk_present = sdk.NAMESPACE in pg._schemas(connection)
                if not sa.inspect(connection).has_table("alembic_version", schema="public"):
                    unversioned_baseline = profile.recognize_unversioned(
                        connection, accepted, sdk_present=sdk_present
                    )
                    revision = unversioned_baseline
                else:
                    revision = pg._revision(connection)
                    profile.verify_stage(connection, revision, accepted, sdk_present=sdk_present)
        # Already-head source data still needs a verified whole-DB backup before
        # its first SDK schema addition. An actually fresh DB has no old content.
        directory = None
        if revision != head or unversioned_baseline is not None or (not fresh and not sdk_present):
            if runtime is None or evidence is None or restore is None:
                raise base.MigrationRefusedError(
                    "Explicit PostgreSQL backup and restore configuration required."
                )
            evidence = base._regular_path(evidence, directory=True)
            directory = evidence / ("startup-" + uuid4().hex)
            pg.upgrade_postgres(
                connection,
                directory,
                runtime,
                restore,
                verify_source=lambda c: _verify_observed(
                    c, revision, accepted, unversioned_baseline, sdk_present
                ),
                verify_target=lambda c: profile.verify_stage(
                    c, head, accepted, sdk_present=sdk_present
                ),
                unversioned_baseline=unversioned_baseline,
            )
        with connection.begin():
            if pg._revision(connection) != head:
                raise base.MigrationRefusedError("PostgreSQL startup revision mismatch.")
            profile.verify_stage(connection, head, accepted, sdk_present=sdk_present)
        # Official concurrent indexes must run after transaction commit, while
        # this same keeper still holds its exclusive session lifecycle lock.
        try:
            sdk.initialize(lease)
            with connection.begin():
                profile.verify(connection, head, accepted)
        except BaseException as exc:
            if directory is not None:
                base._record(
                    directory,
                    "06-sdk-failed.json",
                    {
                        "phase": "SDK_NOT_READY_SOURCE_AND_BACKUP_RETAINED",
                        "error_type": type(exc).__name__,
                    },
                )
            raise
        if directory is not None:
            base._record(
                directory,
                "06-sdk-ready.json",
                {
                    "phase": "FULL_CATALOG_VERIFIED_BEFORE_READY",
                    "head": head,
                },
            )
        lease.make_ready()
        return lease
    except sa.exc.SQLAlchemyError:
        lease.release()
        raise base.MigrationRefusedError(
            "PostgreSQL startup permission or database operation failed; source preserved."
        ) from None
    except BaseException:
        lease.release()
        raise


def _verify_observed(
    connection: Connection,
    revision: str,
    accepted: profile.CatalogProfiles,
    baseline: str | None,
    sdk_present: bool,
) -> None:
    if baseline is None:
        profile.verify_stage(connection, revision, accepted, sdk_present=sdk_present)
    elif (
        pg._observed_revision(connection) is not None
        or profile.recognize_unversioned(connection, accepted, sdk_present=sdk_present) != baseline
    ):
        raise base.MigrationRefusedError("Unversioned PostgreSQL source identity changed.")
