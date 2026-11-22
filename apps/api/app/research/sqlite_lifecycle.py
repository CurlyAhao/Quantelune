"""Kernel-held same-database startup lock, released only at process shutdown.

The retained lock file is not ownership evidence. Kernel exclusion is authority;
neither its age nor its contents authorize killing an owner or deleting a file.
Legacy database offline verification and publication are separate responsibilities.
"""

import os
import sys
from pathlib import Path
from typing import BinaryIO

from app.research.migration import MigrationRefusedError, _regular_path

_HEADER = b"STATFORGE-SQLITE-LIFECYCLE-v1\n"


class SQLiteLifecycleLock:
    def __init__(self, database: Path, *, existing_only: bool = False):
        self._existing_only = existing_only
        parent = _regular_path(database.absolute().parent, directory=True)
        self.database = parent / database.name
        try:
            info = self.database.lstat()
        except FileNotFoundError:
            pass
        else:
            self.database = _regular_path(self.database)
            parent = self.database.parent
            if info.st_nlink != 1:
                raise MigrationRefusedError("Hard-linked SQLite database refused.")
        # Windows SQLite filenames are case-insensitive; normalize the lock
        # basename without changing the original configured database path.
        name = os.path.normcase(self.database.name)
        self.path = parent / (name + ".statforge-lifecycle.lock")
        self._handle: BinaryIO | None = None

    def acquire(self) -> "SQLiteLifecycleLock":
        if self._handle is not None:
            raise MigrationRefusedError("SQLite lifecycle lock is already held here.")
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        created = False
        if self._existing_only:
            _regular_path(self.path)
            descriptor = os.open(self.path, flags)
        else:
            try:
                descriptor = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                _regular_path(self.path)
                descriptor = os.open(self.path, flags)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            actual = os.fstat(handle.fileno())
            path = _regular_path(self.path).stat()
            if (actual.st_dev, actual.st_ino) != (path.st_dev, path.st_ino) or path.st_nlink != 1:
                raise MigrationRefusedError("SQLite lifecycle file identity changed.")
            handle.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise MigrationRefusedError(
                    "SQLite database already has an active lifecycle."
                ) from exc
            handle.seek(0)
            if created:
                handle.write(_HEADER)
                os.fsync(handle.fileno())
            elif path.st_size != len(_HEADER) or handle.read(len(_HEADER)) != _HEADER:
                raise MigrationRefusedError("Unknown SQLite lifecycle file preserved.")
            self._handle = handle
            return self
        except BaseException:
            handle.close()
            raise

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self) -> "SQLiteLifecycleLock":
        return self.acquire()

    def __exit__(self, *_args) -> None:
        self.release()
