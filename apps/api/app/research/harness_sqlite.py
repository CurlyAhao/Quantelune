"""Fixed private SQLite checkpoint files, kernel exclusion and raw bounded reads."""

import json
import os
import re
import stat
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import settings
from app.research.harness_binding import authorize
from app.research.harness_saver import BoundSaver
from app.research.harness_serde import MAX_BYTES, SafeSerializer
from app.research.output_handles import directories, lexical, long_path
from app.research.sqlite_lifecycle import SQLiteLifecycleLock
from app.services import research_service as ledger

_COLUMNS = {
    "checkpoints": [
        ("thread_id", "TEXT", 1, None, 1),
        ("checkpoint_ns", "TEXT", 1, "''", 2),
        ("checkpoint_id", "TEXT", 1, None, 3),
        ("parent_checkpoint_id", "TEXT", 0, None, 0),
        ("type", "TEXT", 0, None, 0),
        ("checkpoint", "BLOB", 0, None, 0),
        ("metadata", "BLOB", 0, None, 0),
    ],
    "writes": [
        ("thread_id", "TEXT", 1, None, 1),
        ("checkpoint_ns", "TEXT", 1, "''", 2),
        ("checkpoint_id", "TEXT", 1, None, 3),
        ("task_id", "TEXT", 1, None, 4),
        ("idx", "INTEGER", 1, None, 5),
        ("channel", "TEXT", 1, None, 0),
        ("type", "TEXT", 0, None, 0),
        ("value", "BLOB", 0, None, 0),
    ],
}


async def _schema(connection):
    async with connection.execute("SELECT type,name FROM sqlite_schema") as cursor:
        objects = set(await cursor.fetchall())
    expected = {
        ("table", "checkpoints"),
        ("table", "writes"),
        ("index", "sqlite_autoindex_checkpoints_1"),
        ("index", "sqlite_autoindex_writes_1"),
    }
    if objects != expected:
        raise ledger.error("HARNESS_SCHEMA_REFUSED")
    for table, columns in _COLUMNS.items():
        async with connection.execute(f"PRAGMA table_xinfo({table})") as cursor:
            actual = await cursor.fetchall()
        if [(row[1], row[2], row[3], row[4], row[5]) for row in actual] != columns or any(
            row[6] != 0 for row in actual
        ):
            raise ledger.error("HARNESS_SCHEMA_REFUSED")
        async with connection.execute(f"PRAGMA foreign_key_list({table})") as cursor:
            if await cursor.fetchall():
                raise ledger.error("HARNESS_SCHEMA_REFUSED")
        fields = []
        for name, dtype, required, default, _pk in columns:
            fields.append(
                name
                + " "
                + dtype
                + (" NOT NULL" if required else "")
                + (" DEFAULT " + default if default is not None else "")
            )
        primary = [name for name, _dtype, _required, _default, pk in columns if pk]
        fields.append("PRIMARY KEY (" + ", ".join(primary) + ")")
        expected_sql = "CREATE TABLE " + table + " (" + ", ".join(fields) + ")"
        async with connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)
        ) as cursor:
            sql = (await cursor.fetchone())[0]
        if re.sub(r"\s+", "", sql).lower() != re.sub(r"\s+", "", expected_sql).lower():
            raise ledger.error("HARNESS_SCHEMA_REFUSED")


def _open_file(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ledger.error("HARNESS_FILE_REFUSED")
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create.restype = wintypes.HANDLE
        handle = create(long_path(path), 0xC0000000, 3, None, 3, 0x200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ledger.error("HARNESS_FILE_REFUSED")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    else:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    file = os.fdopen(descriptor, "r+b", buffering=0)
    actual = os.fstat(file.fileno())
    if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
        file.close()
        raise ledger.error("HARNESS_FILE_REFUSED")
    return file


@asynccontextmanager
async def open_saver(factory, binding, token):
    await authorize(factory, binding, token)
    root = lexical(Path(settings.app_data_path or Path(settings.sqlite_path).absolute().parent))
    with ExitStack() as stack:
        stack.enter_context(directories(root))
        folder = root
        for name in ("harness-checkpoints", binding.owner_id.hex):
            folder = folder / name
            folder.mkdir(mode=0o700, exist_ok=True)
            stack.enter_context(directories(folder))
        path = folder / (binding.thread_id + ".sqlite")
        with SQLiteLifecycleLock(path):
            created = False
            try:
                descriptor = os.open(long_path(path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
                created = True
            except FileExistsError:
                pass
            db_file = stack.enter_context(_open_file(path))
            companions = ExitStack()
            try:
                for suffix in ("-wal", "-shm"):
                    sibling = path.with_name(path.name + suffix)
                    try:
                        descriptor = os.open(
                            long_path(sibling), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600
                        )
                        os.close(descriptor)
                    except FileExistsError:
                        pass
                    companions.enter_context(_open_file(sibling))
                async with aiosqlite.connect(long_path(path)) as connection:
                    await connection.execute("PRAGMA busy_timeout=5000")
                    if not created:
                        await _schema(connection)
                    serde = SafeSerializer(
                        owned_secrets=(
                            settings.jwt_secret_key,
                            os.environ.get("SIDECAR_SESSION_TOKEN", ""),
                            settings.ai_api_key.get_secret_value() if settings.ai_api_key else "",
                        )
                    )
                    delegate = AsyncSqliteSaver(connection, serde=serde)
                    await delegate.setup()
                    await _schema(connection)

                    guarded = raw_guard(connection, db_file, path, binding, serde)

                    try:
                        yield BoundSaver(delegate, factory, binding, token, guarded)
                    finally:
                        # SQLite removes WAL/SHM on last close. Release just these
                        # owned handles before connection close; parent/DB/lifecycle
                        # exclusion stays held through the terminal close.
                        companions.close()
            finally:
                companions.close()


def raw_guard(connection, db_file, path, binding, serde):
    async def guard(config, planned=None):
        current = os.fstat(db_file.fileno())
        actual = path.lstat()
        if (current.st_dev, current.st_ino) != (
            actual.st_dev,
            actual.st_ino,
        ) or actual.st_nlink != 1:
            raise ledger.error("HARNESS_FILE_REFUSED")
        thread = binding.thread_id
        async with connection.execute(
            "SELECT count(*), "
            "coalesce(sum(coalesce(length(checkpoint),0)+"
            "coalesce(length(metadata),0)),0), "
            "coalesce(max(length(checkpoint)),0), "
            "coalesce(max(length(metadata)),0) "
            "FROM checkpoints WHERE thread_id=?",
            (thread,),
        ) as cursor:
            aggregate = await cursor.fetchone()
            if aggregate is None:
                raise ledger.error("HARNESS_SCHEMA_REFUSED")
            count, total, blob, metadata = aggregate
        if planned is not None and planned["kind"] == "checkpoint":
            async with connection.execute(
                "SELECT coalesce(length(checkpoint),0)+"
                "coalesce(length(metadata),0) FROM checkpoints "
                "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (
                    thread,
                    config["configurable"].get("checkpoint_ns", ""),
                    planned["id"],
                ),
            ) as cursor:
                prior = await cursor.fetchone()
            count += 0 if prior is not None else 1
            total += planned["bytes"] - (prior[0] if prior else 0)
        if count > 4096 or total > 64 * 1024 * 1024 or max(blob, metadata) > MAX_BYTES:
            raise ledger.error("HARNESS_CONTENT_LIMIT")
        async with connection.execute(
            "SELECT count(*), coalesce(sum(length(value)),0), "
            "coalesce(max(length(value)),0) FROM writes WHERE thread_id=?",
            (thread,),
        ) as cursor:
            aggregate = await cursor.fetchone()
            if aggregate is None:
                raise ledger.error("HARNESS_SCHEMA_REFUSED")
            count, total, blob = aggregate
        if planned is not None and planned["kind"] == "writes":
            known: dict[int, tuple[int | None] | None] = {}
            for index, size in planned["rows"]:
                if index not in known:
                    async with connection.execute(
                        "SELECT length(value) FROM writes WHERE thread_id=? "
                        "AND checkpoint_ns=? AND checkpoint_id=? "
                        "AND task_id=? AND idx=?",
                        (
                            thread,
                            config["configurable"].get("checkpoint_ns", ""),
                            config["configurable"]["checkpoint_id"],
                            planned["task_id"],
                            index,
                        ),
                    ) as cursor:
                        existing_row = await cursor.fetchone()
                        known[index] = (existing_row[0],) if existing_row is not None else None
                write_prior = known[index]
                if write_prior is None:
                    count += 1
                    total += size
                    known[index] = (size,)
                elif planned["replace"]:
                    total += size - (write_prior[0] or 0)
                    known[index] = (size,)
        if count > 20_000 or total > 64 * 1024 * 1024 or blob > MAX_BYTES:
            raise ledger.error("HARNESS_CONTENT_LIMIT")
        async with connection.execute(
            "SELECT metadata FROM checkpoints WHERE thread_id=?", (thread,)
        ) as cursor:
            for (content,) in await cursor.fetchall():
                serde.check(json.loads(content) if content is not None else {})

    return guard
