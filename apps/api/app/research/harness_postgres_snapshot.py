"""One excluded repeatable-read/read-only PostgreSQL checkpoint snapshot."""

import asyncio
import os
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus

from app.config import settings
from app.research import harness_postgres_proof as proof
from app.research import harness_postgres_schema as schema
from app.research import postgres_migration as pg
from app.research.harness_postgres import PostgresBoundSaver
from app.research.harness_serde import SafeSerializer
from app.research.harness_snapshot import ReadOnlySaver
from app.services import research_service as ledger


@asynccontextmanager
async def open_snapshot(factory, binding, *, lease=None):
    if os.name == "nt" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
        raise ledger.error("HARNESS_POSTGRES_EVENT_LOOP_REFUSED")
    proof.require_configuration()
    if lease is None:
        from app import database

        lease = database._postgres_lifecycle
    if lease is None:
        raise ledger.error("HARNESS_POSTGRES_INITIALIZATION_REQUIRED")
    engine = factory.kw.get("bind")
    if engine is None or engine.url != lease.source.set(
        drivername="postgresql+asyncpg"
    ).difference_update_query(["sslmode", "sslrootcert"]):
        raise ledger.error("HARNESS_POSTGRES_SOURCE_REFUSED")
    lease.verify()
    async with await AsyncConnection.connect(**schema.connection_args(lease.source)) as connection:
        await connection.execute("SET search_path TO statforge_h02, pg_catalog")
        await connection.execute("SET statement_timeout='30s'")
        await connection.execute("SET lock_timeout='5s'")
        serde = SafeSerializer(
            owned_secrets=(
                settings.jwt_secret_key,
                os.environ.get("SIDECAR_SESSION_TOKEN", ""),
                settings.ai_api_key.get_secret_value() if settings.ai_api_key else "",
                settings.h02_checkpoint_integrity_key.get_secret_value()
                if settings.h02_checkpoint_integrity_key
                else "",
            )
        )
        delegate = AsyncPostgresSaver(connection, serde=serde)
        # Reuse only the existing bounded/HMAC raw proof. Never its writer
        # _operation, per-read owner authorization, setup, or nested transaction.
        verifier = PostgresBoundSaver(delegate, None, binding, None, lease)
        backend_pid = connection.info.backend_pid
        async with connection.transaction():
            await connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for query, key, code in (
                (
                    "SELECT pg_try_advisory_xact_lock_shared(%s) AS held",
                    pg._LOCK_KEY,
                    "HARNESS_POSTGRES_LEASE_REFUSED",
                ),
                (
                    "SELECT pg_try_advisory_xact_lock(%s) AS held",
                    verifier.thread_lock,
                    "HARNESS_POSTGRES_THREAD_BUSY",
                ),
            ):
                cursor = await connection.execute(query, (key,))
                held = await cursor.fetchone()
                if held is None or not held["held"]:
                    raise ledger.error(code)
            await schema.verify(connection)
            await connection.execute(
                "LOCK TABLE checkpoint_migrations, checkpoints, checkpoint_blobs, "
                "checkpoint_writes IN ACCESS SHARE MODE"
            )
            await schema.verify(connection)

            async def guard(config):
                if (
                    connection.closed
                    or connection.info.backend_pid != backend_pid
                    or connection.info.transaction_status != TransactionStatus.INTRANS
                ):
                    raise ledger.error("HARNESS_POSTGRES_CONNECTION_REFUSED")
                lease.verify()
                cursor = await connection.execute(
                    "SELECT current_setting('transaction_read_only') AS ro, "
                    "current_setting('transaction_isolation') AS isolation"
                )
                state = await cursor.fetchone()
                if state != {"ro": "on", "isolation": "repeatable read"}:
                    raise ledger.error("HARNESS_SNAPSHOT_CHANGED")
                await verifier._raw(config)

            saver = ReadOnlySaver(delegate, binding, guard)
            try:
                await guard({"configurable": {"thread_id": binding.thread_id}})
                yield saver
                await guard({"configurable": {"thread_id": binding.thread_id}})
            finally:
                saver.close()
