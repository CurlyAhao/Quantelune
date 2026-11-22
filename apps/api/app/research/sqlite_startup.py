"""Internal startup migration: kernel lease, offline evidence, durable publication.

No product Agent receives filesystem paths or SQL. Business engine connection
must happen only after this coordinator returns, and after its lease is retained.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import sqlalchemy as sa
from alembic.script import ScriptDirectory

from app.research import migration as m
from app.research.migration_recovery import _receipt, inspect_sqlite_stage
from app.research.sqlite_lifecycle import SQLiteLifecycleLock
from app.research.sqlite_native import NativeTripletGuard, publish_file
from app.research.sqlite_profiles import schema_identity
from app.research.sqlite_schema_match import (
    known_plain_version,
    normalize_schema,
    version_component,
)
from app.research.sqlite_scratch import startup_scratch
from app.research.sqlite_startup_journal import validate_journal
from app.startup_diagnostics import note

_PROFILES = Path(__file__).parent / "profiles/startup-v9.json"
_PROFILE_SHA = "68048f70d67806fd435d30a7efa5b7c3deb2ddc1920a15f10a3f55d0335308c2"


def _profiles() -> dict:
    path = m._regular_path(_PROFILES)
    if m._native_path(path).stat().st_size > 8 * 1024 * 1024:
        raise m.MigrationRefusedError("Startup profiles exceed the size limit.")
    raw = m._native_path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != _PROFILE_SHA:
        raise m.MigrationRefusedError("Startup schema profile signature changed.")
    result = json.loads(raw)
    if result["head"] != ScriptDirectory.from_config(m._config()).get_current_head():
        raise m.MigrationRefusedError("Startup profiles do not cover the actual migration head.")
    return result


def _without_version(schema: dict) -> dict:
    return {
        "objects": [row for row in schema["objects"] if row[2] != "alembic_version"],
        "tables": {
            name: table for name, table in schema["tables"].items() if name != "alembic_version"
        },
        "table_flags": [row for row in schema["table_flags"] if row[0] != "alembic_version"],
    }


def _recognized(connection: sqlite3.Connection, profiles: dict) -> dict:
    m._check(connection)
    identity = m._source_identity(connection)
    if identity["kind"] == "versioned":
        schema = schema_identity(connection)
        normalized = normalize_schema(schema)
        actual = _without_version(normalized)
        expected = profiles["profiles"].get(identity["revision"], [])
        if not any(
            actual == _without_version(normalize_schema(item))
            and (
                known_plain_version(schema)
                or version_component(normalized) == version_component(normalize_schema(item))
            )
            for item in expected
        ):
            raise m.MigrationRefusedError("Unrecognized versioned SQLite schema.")
    return identity


def _copy(source: Path, destination: Path) -> None:
    m._regular_path(source)
    with (
        m._native_path(source).open("rb") as original,
        m._native_path(destination).open("xb") as output,
    ):
        shutil.copyfileobj(original, output, 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def _clone_raw(raw: Path, parent: Path) -> Path:
    clone = parent / ("snapshot-" + uuid.uuid4().hex)
    m._native_path(clone).mkdir()
    for suffix in ("", "-wal", "-shm"):
        source = raw / ("source.sqlite3" + suffix)
        if m._native_path(source).exists():
            _copy(source, clone / source.name)
    return clone / "source.sqlite3"


def _native_snapshot(source: Path, parent: Path) -> Path:
    note("native_snapshot_enter")
    snapshot = parent / ("check-" + uuid.uuid4().hex)
    m._native_path(snapshot).mkdir()
    with NativeTripletGuard(source) as native:
        native.copy_to(snapshot)
    note("native_snapshot_ready")
    return snapshot / "source.sqlite3"


def _same_original(connection: sqlite3.Connection, receipt: dict, profiles: dict) -> None:
    identity = _recognized(connection, profiles)
    current = {
        name: {"columns": columns, "content": m._rows_hash(connection, name, columns)}
        for name, columns in m._tables(connection)
    }
    original = {
        name: {"columns": item["columns"], "content": item["content"]}
        for name, item in receipt["inventory"].items()
    }
    # No second connection is opened while the real SQLite EXCLUSIVE lease
    # is held. Full source DDL includes the CHECK/default/relationship evidence.
    if (
        identity != receipt["source_identity"]
        or schema_identity(connection) != receipt["schema_identity"]
        or current != original
    ):
        raise m.MigrationRefusedError("SQLite source changed after the verified backup.")


def _checkpoint(source: Path, receipt: dict, profiles: dict) -> None:
    # Native guard is released for this handoff. SQLite's exclusive locking
    # mode and a fresh full comparison prevent a racing old writer being lost.
    with closing(m._connect(source, readonly=False)) as connection:
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        connection.execute("BEGIN EXCLUSIVE")
        _same_original(connection, receipt, profiles)
        connection.commit()
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result is None or result[0] != 0:
            raise m.MigrationRefusedError("SQLite checkpoint is busy or unverified.")
        if connection.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise m.MigrationRefusedError("SQLite source is still in WAL mode.")
    m._sync_file(source)


def _fresh(path: Path, head: str, profiles: dict) -> None:
    import app.models  # noqa: F401
    from app.models.base import Base

    engine = sa.create_engine(sa.URL.create("sqlite", database=str(m._native_path(path))))
    note("fresh_create")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    note("fresh_created")
    note("fresh_stamp")
    m._stamp_candidate(path, head)
    note("fresh_stamped")
    m._sync_file(path)
    note("fresh_synced")
    with closing(m._connect(path)) as connection:
        if _recognized(connection, profiles)["revision"] != head:
            raise m.MigrationRefusedError("Fresh SQLite schema does not match the actual head.")
    note("fresh_recognized")


def _root(source: Path, head: str) -> Path:
    parent = source.parent / (os.path.normcase(source.name) + ".statforge-startup")
    if not m._native_path(parent).exists():
        m._native_path(parent).mkdir()
    m._regular_path(parent, directory=True)
    root = parent / head
    if not m._native_path(root).exists():
        m._native_path(root).mkdir()
    m._regular_path(root, directory=True)
    return root


def _check_published(source: Path, root: Path, profiles: dict, workspace: Path) -> None:
    note("published_check")
    # Application data may legitimately change after a completed publication.
    # Revalidate current integrity and complete schema, never replay old rows.
    if os.name == "nt":
        current = _native_snapshot(source, workspace)
    else:
        current = source
    with closing(m._connect(current)) as connection:
        if _recognized(connection, profiles)["revision"] != profiles["head"]:
            raise m.MigrationRefusedError("Published SQLite source is not at the current head.")
    note("published_recognized")


def _existing(
    source: Path, root: Path, profiles: dict, boundary: Callable[[str], None], workspace: Path
) -> None:
    head = profiles["head"]
    original_path = root / "01-original.json"
    if not m._native_path(original_path).exists():
        size = m._native_path(source).stat().st_size
        for suffix in ("-wal", "-shm"):
            path = source.with_name(source.name + suffix)
            if m._native_path(path).exists():
                size += m._native_path(m._regular_path(path)).stat().st_size
        if shutil.disk_usage(m._native_path(root)).free < 10 * size + 64 * 1024 * 1024:
            raise m.MigrationRefusedError("Insufficient space for retained SQLite generations.")
        raw = root / ("raw-" + uuid.uuid4().hex)
        m._native_path(raw).mkdir()
        with NativeTripletGuard(source) as native:
            raw_files = native.copy_to(raw)
            clone = _clone_raw(raw, workspace)
            with closing(m._connect(clone)) as connection:
                identity = _recognized(connection, profiles)
                inventory = m._inventory(connection)
                schema = schema_identity(connection)
            m._record(
                root,
                "01-original.json",
                {
                    "phase": "ORIGINAL_RETAINED",
                    "source_basename": source.name,
                    "source_identity": identity,
                    "inventory": inventory,
                    "schema_identity": schema,
                    "raw_files": raw_files,
                    "raw_directory": raw.name,
                    "target_revision": head,
                },
            )
    original = _receipt(root, "01-original.json")
    if original.get("source_basename") != source.name or original.get("target_revision") != head:
        raise m.MigrationRefusedError("SQLite journal belongs to a different source or target.")
    raw_name = original.get("raw_directory", "")
    if (
        not isinstance(raw_name, str)
        or not raw_name.startswith("raw-")
        or "/" in raw_name
        or "\\" in raw_name
    ):
        raise m.MigrationRefusedError("Invalid retained SQLite generation identity.")
    raw = m._regular_path(root / raw_name, directory=True)
    for suffix, evidence in original["raw_files"].items():
        if suffix not in ("", "-wal", "-shm"):
            raise m.MigrationRefusedError("Unknown original SQLite generation component.")
        file = m._regular_path(raw / ("source.sqlite3" + suffix))
        if (
            m._native_path(file).stat().st_size != evidence["bytes"]
            or m.file_sha256(file) != evidence["sha256"]
        ):
            raise m.MigrationRefusedError("Original SQLite physical evidence changed.")
    if original["source_identity"]["revision"] == head:
        current = _native_snapshot(source, workspace)
        with closing(m._connect(current)) as connection:
            _same_original(connection, original, profiles)
        m._record(
            root,
            "04-published.json",
            {
                "phase": "PUBLISHED",
                "target_revision": head,
                "candidate_sha256": original["raw_files"][""]["sha256"],
                "source_basename": source.name,
                "mode": "already_current",
                "already_current": True,
            },
        )
        return
    if not m._native_path(root / "02-candidate.json").exists():
        clone = _clone_raw(raw, workspace)
        with closing(m._connect(clone)) as connection:
            _same_original(connection, original, profiles)
        stage_name = "stage-" + uuid.uuid4().hex
        built = m.stage_sqlite_upgrade(clone, root / stage_name)
        with closing(m._connect(built.candidate)) as connection:
            _recognized(connection, profiles)
        m._record(
            root,
            "02-candidate.json",
            {
                "phase": "CANDIDATE_VERIFIED",
                "stage": stage_name,
                "candidate_sha256": built.candidate_sha256,
                "target_revision": head,
            },
        )
    candidate_receipt = _receipt(root, "02-candidate.json")
    stage_name = candidate_receipt.get("stage", "")
    if (
        not isinstance(stage_name, str)
        or not stage_name.startswith("stage-")
        or "/" in stage_name
        or "\\" in stage_name
    ):
        raise m.MigrationRefusedError("Invalid publication stage identity.")
    stage = inspect_sqlite_stage(root / stage_name)
    if (
        stage.phase != "CANDIDATE_VERIFIED"
        or stage.candidate_sha256 != candidate_receipt.get("candidate_sha256")
        or stage.target_revision != head
    ):
        raise m.MigrationRefusedError("Publication candidate is no longer verified.")
    assert stage.candidate is not None
    # A death immediately after replace can precede its final receipt. Only
    # the exact new bytes prove that narrow re-entry; unknown new writes refuse.
    current = _native_snapshot(source, workspace)
    with closing(m._connect(current)) as connection:
        identity = _recognized(connection, profiles)
    if identity["revision"] == head:
        if m.file_sha256(current) != stage.candidate_sha256:
            raise m.MigrationRefusedError(
                "SQLite publication outcome is unknown; current bytes retained."
            )
        m._record(
            root,
            "04-published.json",
            {
                "phase": "PUBLISHED",
                "target_revision": head,
                "candidate_sha256": stage.candidate_sha256,
                "recovered_after_replace": True,
                "source_basename": source.name,
                "mode": "upgraded",
            },
        )
        return
    boundary("before_checkpoint")
    _checkpoint(source, original, profiles)
    with NativeTripletGuard(source) as native:
        checked = workspace / ("check-" + uuid.uuid4().hex)
        m._native_path(checked).mkdir()
        prepared = native.copy_to(checked)
        with closing(m._connect(checked / "source.sqlite3")) as connection:
            _same_original(connection, original, profiles)
        if "-wal" in native.files:
            raise m.MigrationRefusedError("SQLite WAL remains after checkpoint.")
        if "-shm" in native.files:
            # SQLite EXCLUSIVE mode can leave its old, unused shared-memory
            # cache after switching to DELETE. Only retire the byte-identical
            # original cache AFTER full committed content has reached main.
            native.files[""].seek(0)
            header = native.files[""].read(100)
            retained = original["raw_files"].get("-shm")
            if (
                header[18:20] != b"\x01\x01"
                or retained is None
                or any(
                    prepared["-shm"][key] != retained[key]
                    for key in ("bytes", "sha256", "device", "file_id")
                )
            ):
                raise m.MigrationRefusedError("Unknown SQLite shared-memory cache preserved.")
            retired = root / ("retired-" + uuid.uuid4().hex)
            m._native_path(retired).mkdir()
            m._record(
                retired,
                "01-intent.json",
                {
                    "phase": "KNOWN_INACTIVE_CACHE",
                    "evidence": prepared["-shm"],
                    "main_sha256": prepared[""]["sha256"],
                    "main_delete_mode": True,
                },
            )
            publish_file(
                source.with_name(source.name + "-shm"),
                retired / "source.sqlite3-shm",
                replace=False,
            )
            actual_cache = m._native_path(m._regular_path(retired / "source.sqlite3-shm")).stat()
            if (actual_cache.st_dev, actual_cache.st_ino, actual_cache.st_size) != (
                prepared["-shm"]["device"],
                prepared["-shm"]["file_id"],
                prepared["-shm"]["bytes"],
            ):
                raise m.MigrationRefusedError("Retained SQLite cache identity changed.")
            native.files["-shm"].seek(0)
            cache_digest = hashlib.sha256()
            while block := native.files["-shm"].read(1024 * 1024):
                cache_digest.update(block)
            cache_hash = cache_digest.hexdigest()
            if cache_hash != prepared["-shm"]["sha256"]:
                raise m.MigrationRefusedError("Retained SQLite cache bytes changed.")
            m._record(
                retired,
                "02-retained.json",
                {
                    "phase": "INACTIVE_CACHE_RETAINED",
                    "sha256": cache_hash,
                },
            )
            del native.files["-shm"]
        if set(native.files) != {""}:
            raise m.MigrationRefusedError("SQLite generation sidecars remain after checkpoint.")
        if not m._native_path(root / "03-prepared.json").exists():
            m._record(
                root,
                "03-prepared.json",
                {
                    "phase": "SOURCE_PREPARED",
                    "physical_files": prepared,
                    "logical_source_unchanged": True,
                    "original_raw_preserved": True,
                },
            )
        publish_copy = root / ("publish-" + uuid.uuid4().hex + ".sqlite3")
        _copy(stage.candidate, publish_copy)
        if m.file_sha256(publish_copy) != stage.candidate_sha256:
            raise m.MigrationRefusedError("SQLite publication copy changed.")
        boundary("before_replace")
        publish_file(publish_copy, source, replace=True)
        boundary("after_replace")
        with NativeTripletGuard(source) as published:
            if set(published.files) != {""}:
                raise m.MigrationRefusedError("Unexpected new SQLite generation sidecars.")
            verify = workspace / ("check-" + uuid.uuid4().hex)
            m._native_path(verify).mkdir()
            actual = published.copy_to(verify)
            if actual[""]["sha256"] != stage.candidate_sha256:
                raise m.MigrationRefusedError(
                    "SQLite publication outcome is unknown; current bytes retained."
                )
            with closing(m._connect(verify / "source.sqlite3")) as connection:
                _recognized(connection, profiles)
                m._preserved(connection, original["inventory"])
        m._record(
            root,
            "04-published.json",
            {
                "phase": "PUBLISHED",
                "target_revision": head,
                "candidate_sha256": stage.candidate_sha256,
                "recovered_after_replace": False,
                "source_basename": source.name,
                "mode": "upgraded",
            },
        )


def prepare_sqlite_database(
    source: Path, *, boundary: Callable[[str], None] = lambda _phase: None
) -> SQLiteLifecycleLock:
    """Return a held lifecycle lease only after actual DB readiness is verified."""
    note("sqlite_prepare")
    source = source.absolute()
    m._regular_path(source.parent, directory=True)
    lease = SQLiteLifecycleLock(source).acquire()
    source = lease.database
    root = None
    try:
        profiles = _profiles()
        root = _root(source, profiles["head"])
        with startup_scratch(root, source.name, profiles["head"]) as workspace:
            validate_journal(root, source.name, profiles["head"], workspace)
            if m._native_path(root / "04-published.json").exists():
                _check_published(source, root, profiles, workspace)
            elif (
                m._native_path(root / "01-fresh.json").exists() and m._native_path(source).exists()
            ):
                fresh_receipt = _receipt(root, "01-fresh.json")
                if (
                    fresh_receipt.get("source_basename") != source.name
                    or fresh_receipt.get("target_revision") != profiles["head"]
                    or m.file_sha256(source) != fresh_receipt.get("candidate_sha256")
                ):
                    raise m.MigrationRefusedError("Fresh SQLite publication outcome is unknown.")
                _check_published(source, root, profiles, workspace)
                m._record(
                    root,
                    "04-published.json",
                    {
                        "phase": "PUBLISHED",
                        "target_revision": profiles["head"],
                        "candidate_sha256": m.file_sha256(source),
                        "fresh_recovered": True,
                        "source_basename": source.name,
                        "mode": "fresh",
                    },
                )
            elif not m._native_path(source).exists():
                if m._native_path(root / "01-original.json").exists():
                    raise m.MigrationRefusedError(
                        "Original SQLite source is missing; restore must be explicit."
                    )
                if m._native_path(root / "01-fresh.json").exists():
                    fresh = root / _receipt(root, "01-fresh.json")["candidate"]
                    with closing(m._connect(fresh)) as connection:
                        _recognized(connection, profiles)
                else:
                    fresh = root / ("fresh-" + uuid.uuid4().hex + ".sqlite3")
                    _fresh(fresh, profiles["head"], profiles)
                    m._record(
                        root,
                        "01-fresh.json",
                        {
                            "phase": "FRESH_PREPARED",
                            "target_revision": profiles["head"],
                            "source_basename": source.name,
                            "candidate_sha256": m.file_sha256(fresh),
                            "candidate": fresh.name,
                        },
                    )
                    note("fresh_receipt")
                note("publish_enter")
                publish_copy = root / ("publish-" + uuid.uuid4().hex + ".sqlite3")
                _copy(fresh, publish_copy)
                publish_file(publish_copy, source, replace=False)
                note("publish_renamed")
                _check_published(source, root, profiles, workspace)
                m._record(
                    root,
                    "04-published.json",
                    {
                        "phase": "PUBLISHED",
                        "target_revision": profiles["head"],
                        "candidate_sha256": m.file_sha256(source),
                        "fresh": True,
                        "source_basename": source.name,
                        "mode": "fresh",
                    },
                )
            else:
                _existing(source, root, profiles, boundary, workspace)
        note("sqlite_ready")
        return lease
    except BaseException as exc:
        if root is not None:
            try:
                m._record(
                    root,
                    "failure-" + uuid.uuid4().hex + ".json",
                    {
                        "phase": "FAILED_OR_UNKNOWN",
                        "error_type": type(exc).__name__,
                        "automatic_rollback": False,
                    },
                )
            except OSError:
                pass
        lease.release()
        raise
