"""Existing sealed Windows SQLite checkpoints: read-only, zero create or repair."""

import os
import stat
from contextlib import ExitStack, asynccontextmanager

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import settings
from app.research.harness_serde import SafeSerializer
from app.research.harness_snapshot import ReadOnlySaver
from app.research.harness_sqlite import _schema, raw_guard
from app.research.output_handles import directories, lexical, long_path
from app.research.sqlite_lifecycle import SQLiteLifecycleLock
from app.services import research_service as ledger


def _open_readonly(path):
    """Deny existing/new write/delete handles, while admitting our SQLite reader."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ledger.error("HARNESS_FILE_REFUSED")
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
    handle = create(long_path(path), 0x80000000, 1, None, 3, 0x200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ledger.error("HARNESS_FILE_REFUSED")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise
    stream = os.fdopen(descriptor, "rb", buffering=0)
    actual = os.fstat(stream.fileno())
    if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
        stream.close()
        raise ledger.error("HARNESS_FILE_REFUSED")
    return stream


class _Reader(AsyncSqliteSaver):
    async def setup(self):
        # Official read methods call setup; its normal WAL/DDL/commit is forbidden.
        await _schema(self.conn)


@asynccontextmanager
async def open_snapshot(binding):
    # immutable=1 is safe only with kernel write/delete exclusion, not a flock
    # that an ordinary external SQLite writer can ignore on another platform.
    if os.name != "nt":
        raise ledger.error("HARNESS_SQLITE_SNAPSHOT_PLATFORM_REFUSED")
    from pathlib import Path

    root = lexical(Path(settings.app_data_path or Path(settings.sqlite_path).absolute().parent))
    folder = root / "harness-checkpoints" / binding.owner_id.hex
    path = folder / (binding.thread_id + ".sqlite")
    with ExitStack() as stack:
        stack.enter_context(directories(folder))
        stream = stack.enter_context(_open_readonly(path))
        stack.enter_context(SQLiteLifecycleLock(path, existing_only=True))

        def sealed():
            for suffix in ("-wal", "-shm", "-journal"):
                # Even empty or unusual companion entries are retained/refused.
                if os.path.lexists(long_path(path.with_name(path.name + suffix))):
                    raise ledger.error("HARNESS_SNAPSHOT_WAL_DEPENDENCY")

        sealed()
        uri = path.as_uri() + "?mode=ro&immutable=1"
        async with aiosqlite.connect(uri, uri=True) as connection:
            await connection.execute("BEGIN")
            await _schema(connection)
            serde = SafeSerializer(
                owned_secrets=(
                    settings.jwt_secret_key,
                    os.environ.get("SIDECAR_SESSION_TOKEN", ""),
                    settings.ai_api_key.get_secret_value() if settings.ai_api_key else "",
                )
            )
            guard = raw_guard(connection, stream, path, binding, serde)

            async def readonly_guard(config):
                sealed()
                if not connection.in_transaction:
                    raise ledger.error("HARNESS_SNAPSHOT_CHANGED")
                await guard(config)

            saver = ReadOnlySaver(_Reader(connection, serde=serde), binding, readonly_guard)
            try:
                yield saver
                await readonly_guard({"configurable": {"thread_id": binding.thread_id}})
            finally:
                saver.close()
                await connection.rollback()
