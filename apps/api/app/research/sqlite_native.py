"""Internal Windows offline file leases and same-volume publication."""

import ctypes
import hashlib
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

from app.ml.bounded_writer import filesystem_path
from app.research.migration import MigrationRefusedError, _native_path, _regular_path


class NativeTripletGuard:
    """Hold actual DB/WAL/SHM handles; never kill another owner or bypass sharing."""

    def __init__(self, source: Path):
        self.source = _regular_path(source)
        self.files: dict[str, BinaryIO] = {}
        self._stack = ExitStack()

    def __enter__(self) -> "NativeTripletGuard":
        if sys.platform != "win32":
            raise MigrationRefusedError(
                "Native SQLite offline proof is unavailable on this platform."
            )
        import msvcrt
        from ctypes import wintypes

        create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
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
        try:
            for suffix in ("", "-wal", "-shm", "-journal"):
                path = self.source.with_name(self.source.name + suffix)
                try:
                    _native_path(path).lstat()
                except FileNotFoundError:
                    if suffix:
                        continue
                    raise
                info = _native_path(_regular_path(path)).stat()
                if suffix == "-journal":
                    raise MigrationRefusedError(
                        "SQLite journal requires explicit recovery before publication."
                    )
                if info.st_nlink != 1:
                    raise MigrationRefusedError("Hard-linked SQLite generation refused.")
                # READ|WRITE, SHARE_DELETE only, OPEN_EXISTING, OPEN_REPARSE_POINT.
                handle = create(str(filesystem_path(path)), 0xC0000000, 4, None, 3, 0x200080, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise MigrationRefusedError(
                        "SQLite files remain open or cannot be proved offline."
                    )
                try:
                    descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                except BaseException:
                    ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(handle))
                    raise
                file = self._stack.enter_context(os.fdopen(descriptor, "r+b", buffering=0))
                actual = os.fstat(file.fileno())
                checked = _native_path(_regular_path(path)).stat()
                if (actual.st_dev, actual.st_ino) != (checked.st_dev, checked.st_ino):
                    raise MigrationRefusedError("Native SQLite file identity changed.")
                self.files[suffix] = file
            return self
        except BaseException:
            self._stack.close()
            raise

    def copy_to(self, directory: Path, basename: str = "source.sqlite3") -> dict:
        """Read held handles, not a second pathname open prohibited by our lease."""
        result = {}
        for suffix, file in self.files.items():
            file.seek(0)
            path = directory / (basename + suffix)
            digest = hashlib.sha256()
            with _native_path(path).open("xb") as output:
                while block := file.read(1024 * 1024):
                    output.write(block)
                    digest.update(block)
                output.flush()
                os.fsync(output.fileno())
            info = os.fstat(file.fileno())
            result[suffix] = {
                "sha256": digest.hexdigest(),
                "bytes": info.st_size,
                "device": info.st_dev,
                "file_id": info.st_ino,
            }
        return result

    def __exit__(self, *_args) -> None:
        self._stack.close()


def publish_file(candidate: Path, source: Path, *, replace: bool) -> None:
    """No copy-across-volume fallback; atomic rename on the actual filesystem."""
    if filesystem_path(candidate).stat().st_dev != source.parent.stat().st_dev:
        raise MigrationRefusedError("SQLite publication must remain on one volume.")
    if sys.platform == "win32":
        from ctypes import wintypes

        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", wintypes.WCHAR * 1),
            ]

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
        rename = kernel.SetFileInformationByHandle
        rename.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        rename.restype = wintypes.BOOL
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        target = str(filesystem_path(source)).encode("utf-16-le")
        buffer = ctypes.create_string_buffer(ctypes.sizeof(RenameInfo) + len(target) + 2)
        information = RenameInfo.from_buffer(buffer)
        # FileRenameInfoEx; only REPLACE_IF_EXISTS|POSIX_SEMANTICS. No readonly
        # override, privilege escalation, cross-volume copy or released-guard retry.
        information.Flags = 3 if replace else 0
        information.RootDirectory = None
        information.FileNameLength = len(target)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, target, len(target))
        handle = create(str(filesystem_path(candidate)), 0x10000, 7, None, 3, 0x200080, None)
        if handle == ctypes.c_void_p(-1).value:
            raise MigrationRefusedError("SQLite publication candidate is unavailable.")
        try:
            if not rename(handle, 22, buffer, len(buffer)):
                raise MigrationRefusedError(
                    "SQLite publication failed; original material is retained."
                )
        finally:
            close(handle)
    elif sys.platform.startswith("linux") and not replace:
        # RENAME_NOREPLACE protects a newly-created DB from an external race.
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise MigrationRefusedError("Atomic fresh SQLite publication is unavailable.")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(candidate), -100, os.fsencode(source), 1):
            raise MigrationRefusedError("Fresh SQLite target already exists or publication failed.")
    else:
        raise MigrationRefusedError("Verified SQLite publication is unavailable on this platform.")
