"""One owned transient workspace, bounded across successful and killed startups."""

import re
from contextlib import contextmanager
from pathlib import Path

from app.research import migration as m
from app.research.migration_recovery import _receipt

_DIRECTORY = re.compile(r"(?:snapshot|check|verify-original)-[0-9a-f]{32}")
_FILES = {"source.sqlite3", "source.sqlite3-wal", "source.sqlite3-shm", "source.sqlite3-journal"}


def _clear(directory: Path) -> None:
    if not m._native_path(directory).exists():
        return
    absolute = m._regular_path(directory, directory=True)
    children = [directory / item.name for item in m._native_path(directory).iterdir()]
    files = []
    # Validate the complete exact target set before any removal. Never recurse
    # into links, unfamiliar trees or evidence directories outside this scope.
    for child in children:
        if _DIRECTORY.fullmatch(child.name) is None or not m._native_path(child).is_dir():
            raise m.MigrationRefusedError("Unknown transient SQLite material preserved.")
        checked = m._regular_path(child, directory=True)
        if checked.parent != absolute:
            raise m.MigrationRefusedError("Transient SQLite directory identity changed.")
        for entry in m._native_path(child).iterdir():
            file = child / entry.name
            checked_file = m._regular_path(file)
            if (
                file.name not in _FILES
                or checked_file.parent != checked
                or m._native_path(file).stat().st_nlink != 1
            ):
                raise m.MigrationRefusedError("Unknown transient SQLite file preserved.")
            files.append(checked_file)
    for file in files:
        m._native_path(file).unlink()
    for child in children:
        m._native_path(child).rmdir()  # No recursion; new unknown contents cause refusal.


@contextmanager
def startup_scratch(root: Path, basename: str, head: str):
    expected = {
        "format": 1,
        "purpose": "SQLITE_STARTUP_TRANSIENT",
        "directory": "transient-work",
        "source_basename": basename,
        "target_revision": head,
    }
    marker = root / "00-transient-owner.json"
    try:
        m._native_path(marker).lstat()
    except FileNotFoundError:
        if m._native_path(root / "transient-work").exists():
            raise m.MigrationRefusedError("Unowned transient SQLite workspace preserved.")
        m._record(root, marker.name, expected)
    else:
        if _receipt(root, marker.name) != expected:
            raise m.MigrationRefusedError("Transient SQLite workspace binding changed.")
    directory = root / "transient-work"
    _clear(directory)  # A kernel lease guarantees the previous process is gone.
    if not m._native_path(directory).exists():
        m._native_path(directory).mkdir()
    m._regular_path(directory, directory=True)
    try:
        yield directory
    finally:
        _clear(directory)  # Call only after the user's connections/handles close.
