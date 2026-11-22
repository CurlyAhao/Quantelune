"""Typed startup receipt graph and retained evidence, independent of live rows."""

import os
import re
import shutil
import uuid
from contextlib import closing
from pathlib import Path
from typing import TypeGuard

from app.research import migration as m
from app.research.migration_recovery import _receipt, inspect_sqlite_stage
from app.research.sqlite_profiles import schema_identity


def _hash(value) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _name(value: object, prefix: str, suffix: str = "") -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and re.fullmatch(prefix + "[0-9a-f]{32}" + re.escape(suffix), value) is not None
    )


def _original(root: Path, document: dict, workspace: Path) -> None:
    name = document.get("raw_directory")
    files = document.get("raw_files")
    if (
        not _name(name, "raw-")
        or not isinstance(files, dict)
        or "" not in files
        or any(key not in ("", "-wal", "-shm") for key in files)
    ):
        raise m.MigrationRefusedError("Invalid retained original SQLite evidence.")
    directory = m._regular_path(root / name, directory=True)
    for suffix, item in files.items():
        if (
            not isinstance(item, dict)
            or not _hash(item.get("sha256"))
            or any(
                type(item.get(key)) is not int or item[key] < 0
                for key in ("bytes", "device", "file_id")
            )
        ):
            raise m.MigrationRefusedError("Malformed original SQLite component receipt.")
        path = m._regular_path(directory / ("source.sqlite3" + suffix))
        if (
            m._native_path(path).stat().st_nlink != 1
            or m._native_path(path).stat().st_size != item["bytes"]
            or m.file_sha256(path) != item["sha256"]
        ):
            raise m.MigrationRefusedError("Retained original SQLite bytes changed.")
    if (
        not isinstance(document.get("source_identity"), dict)
        or not isinstance(document.get("inventory"), dict)
        or not isinstance(document.get("schema_identity"), dict)
    ):
        raise m.MigrationRefusedError("Original SQLite logical evidence is missing.")
    # Inspect a new writable clone, never the immutable physical archive;
    # SQLite may create or update a cloned WAL index while reading it.
    clone = workspace / ("verify-original-" + uuid.uuid4().hex)
    m._native_path(clone).mkdir()
    for suffix in files:
        with (
            m._native_path(directory / ("source.sqlite3" + suffix)).open("rb") as source,
            m._native_path(clone / ("source.sqlite3" + suffix)).open("xb") as target,
        ):
            shutil.copyfileobj(source, target, 1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
    with closing(m._connect(clone / "source.sqlite3")) as connection:
        m._check(connection)
        if (
            m._source_identity(connection) != document["source_identity"]
            or m._inventory(connection) != document["inventory"]
            or schema_identity(connection) != document["schema_identity"]
        ):
            raise m.MigrationRefusedError("Retained original SQLite logical evidence changed.")


def _candidate(root: Path, document: dict, original: dict, head: str) -> None:
    name = document.get("stage")
    if not _name(name, "stage-") or not _hash(document.get("candidate_sha256")):
        raise m.MigrationRefusedError("Invalid startup candidate identity.")
    stage = inspect_sqlite_stage(root / name)
    if (
        stage.phase != "CANDIDATE_VERIFIED"
        or stage.candidate_sha256 != document["candidate_sha256"]
        or stage.target_revision != head
        or stage.source_identity != original["source_identity"]
    ):
        raise m.MigrationRefusedError("Retained startup candidate binding changed.")
    with closing(m._connect(stage.backup)) as connection:
        if (
            m._inventory(connection) != original["inventory"]
            or schema_identity(connection) != original["schema_identity"]
        ):
            raise m.MigrationRefusedError("Startup backup no longer matches its original input.")


def validate_journal(root: Path, basename: str, head: str, workspace: Path) -> dict[str, dict]:
    phases = {
        "01-original.json": "ORIGINAL_RETAINED",
        "01-fresh.json": "FRESH_PREPARED",
        "02-candidate.json": "CANDIDATE_VERIFIED",
        "03-prepared.json": "SOURCE_PREPARED",
        "04-published.json": "PUBLISHED",
    }
    records: dict[str, dict] = {}
    for name, phase in phases.items():
        try:
            m._native_path(root / name).lstat()
        except FileNotFoundError:
            continue
        document = _receipt(root, name)
        if document.get("phase") != phase:
            raise m.MigrationRefusedError("Unknown startup publication phase.")
        if name != "03-prepared.json" and document.get("target_revision") != head:
            raise m.MigrationRefusedError("Startup receipt target binding changed.")
        if (
            name in ("01-original.json", "01-fresh.json", "04-published.json")
            and document.get("source_basename") != basename
        ):
            raise m.MigrationRefusedError("Startup receipt source binding changed.")
        records[name] = document
    original, fresh = records.get("01-original.json"), records.get("01-fresh.json")
    candidate, prepared = records.get("02-candidate.json"), records.get("03-prepared.json")
    published = records.get("04-published.json")
    if original is not None and fresh is not None:
        raise m.MigrationRefusedError("Conflicting fresh and existing SQLite publication.")
    if candidate is not None and original is None or prepared is not None and candidate is None:
        raise m.MigrationRefusedError("Startup receipt dependency is missing.")
    if original is not None:
        _original(root, original, workspace)
    if fresh is not None:
        fresh_name = fresh.get("candidate")
        if not _name(fresh_name, "fresh-", ".sqlite3") or not _hash(fresh.get("candidate_sha256")):
            raise m.MigrationRefusedError("Malformed fresh SQLite candidate receipt.")
        path = m._regular_path(root / fresh_name)
        if (
            m._native_path(path).stat().st_nlink != 1
            or m.file_sha256(path) != fresh["candidate_sha256"]
        ):
            raise m.MigrationRefusedError("Retained fresh SQLite candidate changed.")
        with closing(m._connect(path)) as connection:
            m._check(connection)
            if m._revision(connection) != head:
                raise m.MigrationRefusedError("Retained fresh SQLite revision changed.")
    if candidate is not None:
        if original is None:
            raise m.MigrationRefusedError("Startup original dependency is missing.")
        _candidate(root, candidate, original, head)
    if prepared is not None and (
        prepared.get("logical_source_unchanged") is not True
        or prepared.get("original_raw_preserved") is not True
        or not isinstance(prepared.get("physical_files"), dict)
    ):
        raise m.MigrationRefusedError("Malformed prepared SQLite receipt.")
    if published is not None:
        mode = published.get("mode")
        if not _hash(published.get("candidate_sha256")):
            raise m.MigrationRefusedError("Malformed published SQLite receipt.")
        if mode == "fresh":
            if (
                fresh is None
                or original is not None
                or candidate is not None
                or prepared is not None
                or published["candidate_sha256"] != fresh["candidate_sha256"]
            ):
                raise m.MigrationRefusedError("Published fresh SQLite dependency changed.")
        elif mode == "already_current":
            if (
                original is None
                or candidate is not None
                or prepared is not None
                or original["source_identity"].get("revision") != head
                or published["candidate_sha256"] != original["raw_files"][""]["sha256"]
            ):
                raise m.MigrationRefusedError("Current SQLite publication dependency changed.")
        elif mode == "upgraded":
            if (
                original is None
                or candidate is None
                or prepared is None
                or published["candidate_sha256"] != candidate["candidate_sha256"]
            ):
                raise m.MigrationRefusedError("Published SQLite upgrade dependency changed.")
        else:
            raise m.MigrationRefusedError("Unknown SQLite publication mode.")
    return records
