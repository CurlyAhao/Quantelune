"""Hold lexical directory/file identities without resolving untrusted links."""

import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from app.research.migration import MigrationRefusedError


def lexical(path: Path) -> Path:
    path = path.absolute()
    if any(part in {".", ".."} for part in path.parts):
        raise MigrationRefusedError("Unknown output path component.")
    return path


def long_path(path: Path) -> str:
    value = str(lexical(path))
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return value


def _same(actual, expected, *, directory=False):
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino) or getattr(
        actual, "st_file_attributes", 0
    ) & 0x400:
        raise MigrationRefusedError("Output handle identity or link boundary changed.")
    if not (stat.S_ISDIR(actual.st_mode) if directory else stat.S_ISREG(actual.st_mode)):
        raise MigrationRefusedError("Unknown output object type.")
    if not directory and actual.st_nlink != 1:
        raise MigrationRefusedError("Hard linked output retained and refused.")


def _win_open(path: Path, *, directory=False, write=False, create=False, delete=False) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    opener = kernel.CreateFileW
    opener.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    opener.restype = wintypes.HANDLE
    access = (
        0x80000000
        if directory
        else ((0xC0000000 | (0x10000 if create else 0)) if write else 0x80000000)
    )
    if delete:
        access |= 0x10000
    share = 3 if directory else 0  # Directories cannot be renamed/deleted; file has one held owner.
    handle = opener(
        long_path(path),
        access,
        share,
        None,
        1 if create else 3,
        0x200000 | (0x2000000 if directory else 0),
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise MigrationRefusedError("Output handle unavailable; no sharing or privilege bypass.")
    try:
        return msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if write else os.O_RDONLY))
    except BaseException:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise


@contextmanager
def directories(path: Path):
    path = lexical(path)
    with ExitStack() as stack:
        previous = None
        for current in reversed((path, *path.parents)):
            expected = current.lstat()
            if stat.S_ISLNK(expected.st_mode) or getattr(expected, "st_file_attributes", 0) & 0x400:
                raise MigrationRefusedError("Lexical output directory link refused.")
            if os.name == "nt":
                descriptor = _win_open(current, directory=True)
            else:
                descriptor = os.open(
                    str(current) if previous is None else current.name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=previous,
                )
            stack.callback(os.close, descriptor)
            _same(os.fstat(descriptor), expected, directory=True)
            previous = descriptor
        yield previous


@contextmanager
def opened(path: Path, *, write=False, create=False, delete=False):
    path = lexical(path)
    with directories(path.parent) as parent:
        expected = None if create else path.lstat()
        if expected is not None and (
            stat.S_ISLNK(expected.st_mode) or getattr(expected, "st_file_attributes", 0) & 0x400
        ):
            raise MigrationRefusedError("Lexical output file link refused.")
        if os.name == "nt":
            descriptor = _win_open(path, write=write, create=create, delete=delete)
        else:
            descriptor = os.open(
                path.name,
                (os.O_RDWR if write else os.O_RDONLY)
                | getattr(os, "O_NOFOLLOW", 0)
                | (os.O_CREAT | os.O_EXCL if create else 0),
                0o600,
                dir_fd=parent,
            )
        try:
            actual = os.fstat(descriptor)
            _same(actual, expected or path.lstat())
            stream = os.fdopen(descriptor, "r+b" if write else "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            yield stream


def unlink_opened(stream, path: Path, parent: int) -> None:
    """Remove the verified held ordinary file; never resolve a different target."""
    path = lexical(path)
    actual = os.fstat(stream.fileno())
    _same(path.lstat(), actual)
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        dispose = kernel.SetFileInformationByHandle
        dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        dispose.restype = wintypes.BOOL
        disposition = wintypes.BOOL(True)
        if not dispose(
            msvcrt.get_osfhandle(stream.fileno()),
            4,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise MigrationRefusedError("Owned output handle deletion refused.")
    else:
        _same(os.stat(path.name, dir_fd=parent, follow_symlinks=False), actual)
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)


def rename_opened(stream, target: Path, parent: int) -> None:
    """NOREPLACE rename through the held file identity, never a resolved source."""
    import ctypes

    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes

        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", wintypes.WCHAR * 1),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        rename = kernel.SetFileInformationByHandle
        rename.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        rename.restype = wintypes.BOOL
        name = long_path(target).encode("utf-16-le")
        buffer = ctypes.create_string_buffer(ctypes.sizeof(RenameInfo) + len(name) + 2)
        information = RenameInfo.from_buffer(buffer)
        information.Flags = 0
        information.RootDirectory = None
        information.FileNameLength = len(name)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, name, len(name))
        if not rename(msvcrt.get_osfhandle(stream.fileno()), 22, buffer, len(buffer)):
            raise MigrationRefusedError("Owned output rename refused; existing files retained.")
    else:
        library = ctypes.CDLL(None, use_errno=True)
        rename_linux = getattr(library, "renameat2", None)
        if rename_linux is None:
            raise MigrationRefusedError("Owned output atomic rename unavailable.")
        rename_linux.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_linux.restype = ctypes.c_int
        pending = target.name + ".pending"
        if rename_linux(parent, os.fsencode(pending), parent, os.fsencode(target.name), 1):
            raise MigrationRefusedError("Owned output target exists; retained without overwrite.")
    os.fsync(stream.fileno())
