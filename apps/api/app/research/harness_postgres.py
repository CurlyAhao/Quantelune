"""Pinned official PostgreSQL saver I/O, fenced and bounded in its own transaction."""

import asyncio
import hashlib
import json
import os
from collections import ChainMap
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import get_serializable_checkpoint_metadata
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus

from app.config import settings
from app.research import harness_postgres_proof as proof
from app.research import harness_postgres_schema as schema
from app.research import postgres_migration as pg
from app.research.harness_binding import authorize
from app.research.harness_saver import BoundSaver
from app.research.harness_serde import MAX_BYTES, SafeSerializer
from app.services import research_service as ledger

MAX_TOTAL = 64 * 1024 * 1024
MAX_REFERENCES = 20_000


def _blob_hash(content):
    return hashlib.sha256(content).hexdigest()


def _json_size(value):
    # PostgreSQL emits JSONB with separators containing spaces. Measure the
    # actual server representation in prediction, not a guessed byte formula.
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class _PlannerWriteGuard:
    """One inner guard in an already authorized, uncommitted PG write."""

    def __init__(self, saver, config, transaction):
        self.saver, self.task = saver, asyncio.current_task()
        self.connection, self.transaction = saver.connection, transaction
        self.pid = saver.backend_pid
        self.factory, self.binding, self.token = saver.factory, saver.binding, saver.token
        self.config = self._projection(saver, config)
        self.state = "READY"

    @staticmethod
    def _projection(saver, config):
        values = config["configurable"]
        metadata = config.get("metadata")
        return saver.guard_serde.dumps_typed(
            dict(
                configurable={
                    key: values[key]
                    for key in ("thread_id", "checkpoint_ns", "checkpoint_id", "checkpoint_map")
                    if key in values
                },
                metadata=dict(metadata) if type(metadata) is ChainMap else metadata,
            )
        )

    def check(self, saver, config, state):
        if (
            saver is not self.saver
            or saver._planner_write_guard is not self
            or self.state != state
            or asyncio.current_task() is not self.task
            or saver._operation_transaction is not self.transaction
            or saver.connection is not self.connection
            or saver.backend_pid != self.pid
            or self.connection.closed
            or self.connection.info.backend_pid != self.pid
            or self.connection.info.transaction_status != TransactionStatus.INTRANS
            or saver.factory is not self.factory
            or saver.binding is not self.binding
            or saver.token is not self.token
            or not saver.operation_lock.locked()
            or self._projection(saver, config) != self.config
        ):
            raise ledger.error("HARNESS_POSTGRES_OPERATION_REFUSED")

    def __reduce__(self):
        raise TypeError("A private PG write guard cannot be serialized.")


class PostgresBoundSaver(BoundSaver):
    def __init__(self, delegate, factory, binding, token, lease):
        super().__init__(delegate, factory, binding, token, self._guard_raw)
        self.connection, self.lease = delegate.conn, lease
        self.operation_lock = asyncio.Lock()  # Different from delegate.lock.
        self._planner_write_guard = None
        self._operation_transaction = None
        self.backend_pid = self.connection.info.backend_pid
        self.thread_lock = int.from_bytes(
            hashlib.sha256(("statforge-h02:" + binding.thread_id).encode()).digest()[:8],
            "big",
            signed=True,
        )

    async def _authorize_operation(self):
        from app.research.reviewer_contracts import ReviewerToken

        # PG checkpoint bookkeeping uses the same current owner/fence/lease
        # proof as BoundSaver. Model/tool calls separately prove live sources.
        if type(self.token) is ReviewerToken:
            await authorize(self.factory, self.binding, self.token, checkpoint=True)
        else:
            await authorize(self.factory, self.binding, self.token)

    @asynccontextmanager
    async def _operation(self, config, planned=None):
        self._config(config)
        async with self.operation_lock:
            if (
                self.connection.closed
                or self.connection.info.backend_pid != self.backend_pid
                or self.connection.info.transaction_status != TransactionStatus.IDLE
            ):
                raise ledger.error("HARNESS_POSTGRES_CONNECTION_REFUSED")
            self.lease.verify()
            async with self.connection.transaction() as transaction:
                cursor = await self.connection.execute(
                    "SELECT pg_try_advisory_xact_lock_shared(%s) AS held", (pg._LOCK_KEY,)
                )
                if not (await cursor.fetchone())["held"]:
                    raise ledger.error("HARNESS_POSTGRES_LEASE_REFUSED")
                cursor = await self.connection.execute(
                    "SELECT pg_try_advisory_xact_lock(%s) AS held", (self.thread_lock,)
                )
                if not (await cursor.fetchone())["held"]:
                    raise ledger.error("HARNESS_POSTGRES_THREAD_BUSY")
                self.lease.verify()
                await self._authorize_operation()
                await schema.verify(self.connection)
                # The same short transaction also excludes an external writer
                # changing bytes between the length guard and SDK decoding.
                await self.connection.execute(
                    "LOCK TABLE checkpoint_migrations, checkpoints, checkpoint_blobs, "
                    "checkpoint_writes IN SHARE ROW EXCLUSIVE MODE"
                )
                await schema.verify(self.connection)
                await self._raw(config, planned)
                from app.research.planning_lease import PlanningToken

                self._operation_transaction = transaction
                try:
                    if (
                        type(self.token) is PlanningToken
                        and self.binding.purpose == "planner"
                        and type(planned) is dict
                        and planned.get("kind") in {"checkpoint", "writes"}
                    ):
                        self._planner_write_guard = _PlannerWriteGuard(self, config, transaction)
                    yield
                finally:
                    # Never carry this one-use guard into exit authorization,
                    # another operation, a failure or cancellation.
                    self._planner_write_guard = None
                    self._operation_transaction = None
                await self._raw(config)  # Same transaction; any excess rolls back.
                await self._authorize_operation()
                self.lease.verify()

    async def _guard(self, config):
        marker = self._planner_write_guard
        if marker is None:
            return await super()._guard(config)
        self._config(config)
        marker.check(self, config, "READY")
        marker.state = "CHECKING"  # No await before claiming the one inner guard.
        await self.raw_guard(config)
        marker.check(self, config, "CHECKING")
        marker.state = "CONSUMED"

    async def _guard_raw(self, config, _generic_plan=None):
        # BoundSaver's generic SQLite plan cannot account for PG JSONB/blobs.
        # The exact PG prediction already ran in _operation before the SDK.
        await self._raw(config)

    async def _raw(self, config, planned=None):
        thread = self.binding.thread_id
        cursor = await self.connection.execute(
            """SELECT 'checkpoint' AS kind,count(*) AS count,
              coalesce(sum(octet_length(checkpoint::text)+octet_length(metadata::text)),0) AS total,
              coalesce(max(greatest(octet_length(checkpoint::text),
                octet_length(metadata::text))),0) AS cell
              FROM checkpoints WHERE thread_id=%s UNION ALL
              SELECT 'blob',count(*),coalesce(sum(octet_length(blob)),0),
              coalesce(max(octet_length(blob)),0) FROM checkpoint_blobs WHERE thread_id=%s UNION ALL
              SELECT 'write',count(*),coalesce(sum(octet_length(blob)),0),
              coalesce(max(octet_length(blob)),0) FROM checkpoint_writes WHERE thread_id=%s""",
            (thread, thread, thread),
        )
        rows = {row["kind"]: row for row in await cursor.fetchall()}
        if (
            rows["checkpoint"]["count"] > 4096
            or max(rows["blob"]["count"], rows["write"]["count"]) > 20_000
            or sum(row["total"] for row in rows.values()) > MAX_TOTAL
            or max(row["cell"] for row in rows.values()) > MAX_BYTES
        ):
            raise ledger.error("HARNESS_CONTENT_LIMIT")
        cursor = await self.connection.execute(
            """SELECT EXISTS(
              SELECT 1 FROM checkpoints WHERE thread_id=%s AND
                (octet_length(checkpoint_ns)>512 OR octet_length(checkpoint_id)>128 OR
                 octet_length(parent_checkpoint_id)>128 OR octet_length(type)>128)
              UNION ALL SELECT 1 FROM checkpoint_blobs WHERE thread_id=%s AND
                (octet_length(checkpoint_ns)>512 OR octet_length(channel)>512 OR
                 octet_length(version)>128 OR octet_length(type)>128)
              UNION ALL SELECT 1 FROM checkpoint_writes WHERE thread_id=%s AND
                (octet_length(checkpoint_ns)>512 OR octet_length(checkpoint_id)>128 OR
                 octet_length(task_id)>128 OR octet_length(task_path)>1024 OR
                 octet_length(channel)>512 OR octet_length(type)>128)) AS excess""",
            (thread, thread, thread),
        )
        if (await cursor.fetchone())["excess"]:
            raise ledger.error("HARNESS_CONTENT_LIMIT")
        cursor = await self.connection.execute(
            """SELECT coalesce(sum(CASE WHEN
              jsonb_typeof(metadata->'statforge_h02_proof'->'references')='array' THEN
              jsonb_array_length(metadata->'statforge_h02_proof'->'references') ELSE 0 END),0) AS n
              FROM checkpoints WHERE thread_id=%s""",
            (thread,),
        )
        if (await cursor.fetchone())["n"] > MAX_REFERENCES:
            raise ledger.error("HARNESS_CONTENT_LIMIT")
        # JSONB primitive channel values and metadata bypass official serde.
        # Read only after the raw length guard above, then check them explicitly.
        cursor = await self.connection.execute(
            "SELECT checkpoint_ns,checkpoint_id,parent_checkpoint_id,checkpoint,metadata "
            "FROM checkpoints WHERE thread_id=%s",
            (thread,),
        )
        required: dict[tuple[str, str, str], str] = {}
        reachable, forbidden, snapshots = set(), set(), set()
        for row in await cursor.fetchall():
            self.guard_serde.check(row["checkpoint"])
            self.guard_serde.check(row["metadata"])
            route = {
                key: row[key] for key in ("checkpoint_ns", "checkpoint_id", "parent_checkpoint_id")
            }
            local = {
                (row["checkpoint_ns"], channel, str(version))
                for channel, version in row["checkpoint"]["channel_versions"].items()
            }
            reachable.update(local)
            declared = set()
            for item in proof.verify(self.binding, row["checkpoint"], row["metadata"], route):
                key = (row["checkpoint_ns"], item["channel"], item["version"])
                if (
                    key not in local
                    or (
                        item["channel"] in row["checkpoint"]["channel_values"]
                        and row["checkpoint"]["channel_values"][item["channel"]] is not True
                    )
                    or key in required
                    and required[key] != item["sha256"]
                ):
                    raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
                required[key] = item["sha256"]
                declared.add(key)
                if item["channel"] in row["checkpoint"]["channel_values"]:
                    snapshots.add(key)
            forbidden.update(local - declared)
        # One bounded bulk read and one hash per unique required blob, regardless
        # of how many checkpoints refer to the same immutable channel/version.
        cursor = await self.connection.execute(
            "SELECT checkpoint_ns,channel,version,type,blob FROM checkpoint_blobs "
            "WHERE thread_id=%s",
            (thread,),
        )
        found = set()
        for blob in await cursor.fetchall():
            key = (blob["checkpoint_ns"], blob["channel"], blob["version"])
            if key in forbidden and (blob["type"] != "empty" or blob["blob"] is not None):
                raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
            if key in required:
                if (
                    blob["type"] != "statforge-json-v1"
                    or blob["blob"] is None
                    or _blob_hash(blob["blob"]) != required[key]
                ):
                    raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
                if (
                    key in snapshots
                    and type(self.guard_serde.loads_typed((blob["type"], blob["blob"])))
                    is not _DeltaSnapshot
                ):
                    raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
                found.add(key)
            elif key in reachable and (blob["type"] != "empty" or blob["blob"] is not None):
                # The official loader merges every reachable blob over inline
                # values, including consumed channels: unsolicited blobs refuse.
                raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
        if found != set(required):
            raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
        if planned is not None:
            await self._predict(config, rows, planned)

    async def _predict(self, config, rows, plan):
        values = config["configurable"]
        args = (self.binding.thread_id, values.get("checkpoint_ns", ""))
        delta, added = 0, {"checkpoint": 0, "blob": 0, "write": 0}
        if plan["kind"] == "checkpoint":
            cursor = await self.connection.execute(
                "SELECT octet_length(%s::jsonb::text)+octet_length(%s::jsonb::text) AS size",
                (plan["checkpoint"], plan["metadata"]),
            )
            size = (await cursor.fetchone())["size"]
            if size > MAX_BYTES:  # Conservative combined bound, before the write.
                raise ledger.error("HARNESS_CONTENT_LIMIT")
            cursor = await self.connection.execute(
                "SELECT octet_length(checkpoint::text)+octet_length(metadata::text) AS size "
                "FROM checkpoints WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s",
                (*args, plan["id"]),
            )
            prior = await cursor.fetchone()
            delta += size - (prior["size"] if prior else 0)
            added["checkpoint"] += int(prior is None)
            for channel, version, blob in plan["blobs"]:
                cursor = await self.connection.execute(
                    "SELECT type,blob FROM checkpoint_blobs WHERE thread_id=%s "
                    "AND checkpoint_ns=%s "
                    "AND channel=%s AND version=%s",
                    (*args, channel, str(version)),
                )
                prior_blob = await cursor.fetchone()
                if prior_blob is None:
                    delta += len(blob)
                    added["blob"] += 1
                elif (prior_blob["type"], prior_blob["blob"]) != ("statforge-json-v1", blob):
                    raise ledger.error("HARNESS_BLOB_VERSION_CHANGED")
        else:
            known = {}
            for index, size in plan["rows"]:
                if index not in known:
                    cursor = await self.connection.execute(
                        "SELECT octet_length(blob) AS size FROM checkpoint_writes "
                        "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s "
                        "AND task_id=%s AND idx=%s",
                        (*args, values["checkpoint_id"], plan["task_id"], index),
                    )
                    prior = await cursor.fetchone()
                    known[index] = prior["size"] if prior else None
                old = known[index]
                if old is None:
                    delta += size
                    added["write"] += 1
                    known[index] = size
                elif plan["replace"]:
                    delta += size - old
                    known[index] = size
        if (
            sum(row["total"] for row in rows.values()) + delta > MAX_TOTAL
            or rows["checkpoint"]["count"] + added["checkpoint"] > 4096
            or rows["blob"]["count"] + added["blob"] > 20_000
            or rows["write"]["count"] + added["write"] > 20_000
        ):
            raise ledger.error("HARNESS_CONTENT_LIMIT")

    async def aget_tuple(self, config):
        async with self._operation(config):
            return await super().aget_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        async with self._operation(config):
            items = [
                item
                async for item in super().alist(config, filter=filter, before=before, limit=limit)
            ]
        for item in items:
            await authorize(self.factory, self.binding, self.token)
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        from app.research import reviewer_sdk_tasks, reviewer_writer
        from app.research.reviewer_contracts import ReviewerToken

        if type(self.token) is ReviewerToken:
            self._config(config)
            config, checkpoint, metadata, new_versions = reviewer_writer.freeze(
                self.guard_serde, config, checkpoint, metadata, new_versions
            )
            async with reviewer_sdk_tasks.callback(self.token, self.binding.binding_hash):
                stamp = reviewer_writer.capture(self.token, self.binding, config, checkpoint)
                return await self._aput_postgres(
                    config, checkpoint, metadata, new_versions, stamp=stamp
                )
        return await self._aput_postgres(config, checkpoint, metadata, new_versions)

    def _metadata(self, config, metadata):
        return get_serializable_checkpoint_metadata(config, metadata)

    async def _aput_postgres(self, config, checkpoint, metadata, new_versions, *, stamp=None):
        self._config(config)
        config = dict(
            config,
            configurable=dict(
                config["configurable"],
                checkpoint_ns=config["configurable"].get("checkpoint_ns", ""),
            ),
        )
        # The SDK may mutate shared messages while authorization awaits. The
        # signed references, quota plan and official write must use one entry
        # snapshot, including concrete whitelisted delta/message containers.
        checkpoint = self.guard_serde.loads_typed(self.guard_serde.dumps_typed(checkpoint))
        new_versions = self.guard_serde.loads_typed(self.guard_serde.dumps_typed(new_versions))
        if type(metadata) is ChainMap:
            metadata = dict(metadata)
        metadata = self.guard_serde.loads_typed(self.guard_serde.dumps_typed(metadata))
        projected = self._metadata(config, metadata)
        if stamp is not None:
            from app.research import reviewer_writer

            projected[reviewer_writer.FIELD] = stamp.model_dump(mode="json")
            self._writer(config, checkpoint, projected, stamp)
        self.guard_serde.check(projected)
        copy = dict(checkpoint, channel_values=dict(checkpoint["channel_values"]))
        blobs, references = [], []
        for channel, value in list(copy["channel_values"].items()):
            if value is None or isinstance(value, (str, int, float, bool)):
                continue
            if type(value) is _DeltaSnapshot:
                copy["channel_values"][channel] = True
            else:
                del copy["channel_values"][channel]
            content = self.guard_serde.dumps_typed(value)[1]
            references.append(
                dict(
                    channel=channel,
                    version=str(checkpoint["channel_versions"][channel]),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
            if channel in new_versions:
                blobs.append((channel, new_versions[channel], content))
        route = dict(
            checkpoint_ns=config["configurable"].get("checkpoint_ns", ""),
            checkpoint_id=checkpoint["id"],
            parent_checkpoint_id=config["configurable"].get("checkpoint_id"),
        )
        projected = proof.attach(self.binding, copy, projected, references, route)
        metadata = projected
        plan = dict(
            kind="checkpoint",
            id=checkpoint["id"],
            checkpoint=_json_size(copy),
            metadata=_json_size(projected),
            blobs=blobs,
        )
        async with self._operation(config, plan):
            # The exact server-prepared metadata already contains the stamp
            # covered by proof.attach. Never re-enter public aput/reinject it.
            return await super()._aput(config, checkpoint, metadata, new_versions, stamp=stamp)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        from collections import deque

        from app.research import reviewer_sdk_tasks, reviewer_writer
        from app.research.reviewer_contracts import ReviewerToken

        if type(self.token) is ReviewerToken:
            self._config(config)
            if type(writes) not in {list, tuple, deque} or len(writes) > 20_000:
                raise ledger.error("HARNESS_WRITES_REFUSED")
            config, _, _, frozen = reviewer_writer.freeze(
                self.guard_serde, config, {}, {}, (list(writes), task_id, task_path)
            )
            writes, task_id, task_path = frozen
            async with reviewer_sdk_tasks.callback(self.token, self.binding.binding_hash):
                self._writes_live()
                return await self._aput_writes_postgres(config, writes, task_id, task_path)
        return await self._aput_writes_postgres(config, writes, task_id, task_path)

    async def _aput_writes_postgres(self, config, writes, task_id, task_path=""):
        from collections import deque

        from langgraph.checkpoint.base import WRITES_IDX_MAP

        self._config(config)
        config = dict(
            config,
            configurable=dict(
                config["configurable"],
                checkpoint_ns=config["configurable"].get("checkpoint_ns", ""),
            ),
        )

        if type(writes) not in {list, tuple, deque} or len(writes) > 20_000:
            raise ledger.error("HARNESS_WRITES_REFUSED")
        writes = list(writes)
        self.guard_serde.check((task_id, task_path, writes))
        plan = dict(
            kind="writes",
            task_id=task_id,
            replace=all(channel in WRITES_IDX_MAP for channel, _ in writes),
            rows=[
                (WRITES_IDX_MAP.get(channel, index), len(self.guard_serde.dumps_typed(value)[1]))
                for index, (channel, value) in enumerate(writes)
            ],
        )
        async with self._operation(config, plan):
            return await super()._aput_writes(config, writes, task_id, task_path)

    async def aget_delta_channel_history(self, *, config, channels):
        async with self._operation(config):
            return await super().aget_delta_channel_history(config=config, channels=channels)


@asynccontextmanager
async def open_saver(factory, binding, token, *, lease=None):
    """No setup, memory, SQLite, pool or reconnect fallback in the runtime path."""
    if os.name == "nt" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
        # Actual Windows server entry must choose its loop before creating one.
        # Never silently move a connection/ledger between independent loops.
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
    await authorize(factory, binding, token)
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
        saver = PostgresBoundSaver(delegate, factory, binding, token, lease)
        async with saver._operation({"configurable": {"thread_id": binding.thread_id}}):
            pass
        yield saver
