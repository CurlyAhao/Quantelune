"""Async SQLAlchemy engine and session management (PostgreSQL or SQLite)."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.research.postgres_startup import PostgresLifecycle
from app.research.sqlite_lifecycle import SQLiteLifecycleLock

logger = logging.getLogger("statforge.database")

_engine_kwargs: dict = {"echo": False}
if settings.db_backend == "sqlite":
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update({"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True})
    _engine_kwargs["connect_args"] = settings.postgres_async_connect_args()

engine = create_async_engine(settings.database_url, **_engine_kwargs)

if settings.db_backend == "sqlite":

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

_sqlite_lifecycle: SQLiteLifecycleLock | None = None
_postgres_lifecycle: PostgresLifecycle | None = None


def _guard_postgres_transaction(connection) -> None:
    from app.research import postgres_migration as pg

    if _postgres_lifecycle is None:
        raise RuntimeError("PostgreSQL lifecycle has not been initialized.")
    _postgres_lifecycle.verify()
    if not connection.execute(
        text("SELECT pg_try_advisory_xact_lock_shared(:key)"), {"key": pg._LOCK_KEY}
    ).scalar_one():
        raise RuntimeError("PostgreSQL migration or startup owns the database.")
    _postgres_lifecycle.verify()


if settings.db_backend == "postgresql":
    event.listen(engine.sync_engine, "begin", _guard_postgres_transaction)


async def initialize_database() -> None:
    """Finish verified startup before the first business engine connection."""
    global _sqlite_lifecycle, _postgres_lifecycle
    # Importing models registers every table on Base.metadata.
    import app.models  # noqa: F401

    if settings.db_backend == "sqlite":
        from app.research.sqlite_startup import prepare_sqlite_database

        path = Path(settings.sqlite_path).absolute()
        if engine.url.database is None or Path(engine.url.database).absolute() != path:
            raise RuntimeError("SQLite engine and configured startup path differ.")
        path.parent.mkdir(parents=True, exist_ok=True)
        if _sqlite_lifecycle is not None:
            if _sqlite_lifecycle.database != path.resolve():
                raise RuntimeError("SQLite lifecycle belongs to another configured database.")
            return
        # This is intentionally synchronous: caller cancellation cannot leave
        # an unobserved background publisher mutating the DB during shutdown.
        _sqlite_lifecycle = prepare_sqlite_database(path)
        return
    from app.research.postgres_startup import prepare_postgres_database

    source = settings.postgres_url()
    if engine.url != sa_url_async(source):
        raise RuntimeError("PostgreSQL engine and trusted startup target differ.")
    if _postgres_lifecycle is not None:
        if _postgres_lifecycle.source != source:
            raise RuntimeError("PostgreSQL lifecycle belongs to another configured database.")
        _postgres_lifecycle.verify()
        return
    _postgres_lifecycle = prepare_postgres_database(
        source,
        runtime=Path(settings.postgres_migration_runtime)
        if settings.postgres_migration_runtime
        else None,
        evidence=Path(settings.postgres_migration_evidence)
        if settings.postgres_migration_evidence
        else None,
        restore=settings.postgres_url(restore=True) if settings.postgres_restore_db else None,
    )


def sa_url_async(source):
    return source.set(drivername="postgresql+asyncpg").difference_update_query(
        ["sslmode", "sslrootcert"]
    )


async def shutdown_database() -> None:
    """Close all business handles before releasing the same-database lease."""
    global _sqlite_lifecycle, _postgres_lifecycle
    cleanup = asyncio.create_task(engine.dispose())
    cancelled: asyncio.CancelledError | None = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancelled = exc
    # A failed/unknown close retains the lease; another startup must not open
    # the database while old handles may still exist. Process death releases it.
    cleanup.result()
    if _sqlite_lifecycle is not None:
        _sqlite_lifecycle.release()
        _sqlite_lifecycle = None
    if _postgres_lifecycle is not None:
        _postgres_lifecycle.release()
        _postgres_lifecycle = None
    if cancelled is not None:
        raise cancelled


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    if _postgres_lifecycle is not None:
        _postgres_lifecycle.verify()
    session = async_session_factory()
    failed = False
    try:
        yield session
        await session.commit()
    except BaseException:
        failed = True
        try:
            await session.rollback()
        except BaseException as cleanup_error:
            # Preserve the original business/cancellation outcome; a broken
            # connection must not turn PUBLICATION_UNKNOWN into a false cause.
            logger.error("Database rollback cleanup failed (%s)", type(cleanup_error).__name__)
        raise
    finally:
        try:
            # Match AsyncSession.__aexit__: cleanup survives caller cancellation.
            await asyncio.shield(session.close())
        except BaseException as cleanup_error:
            if not failed:
                raise
            logger.error("Database close cleanup failed (%s)", type(cleanup_error).__name__)
