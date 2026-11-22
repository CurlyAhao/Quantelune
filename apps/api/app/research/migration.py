"""Stage a verified SQLite upgrade without mutating the live database.

Publication and application startup coordination are separate responsibilities.
An interrupted or failed stage remains inspectable alongside its immutable backup.
No connection URL, row content, credential, or application secret is journaled.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command

_API_ROOT = Path(__file__).resolve().parents[2]
_RESERVE_BYTES = 16 * 1024 * 1024


class MigrationRefusedError(RuntimeError):
    """The source or stage cannot be safely verified."""


def _native_path(path: Path) -> Path:
    """Extend Win32 spelling without resolving away an unchecked reparse point."""
    absolute = str(path.absolute())
    if os.name == "nt" and not absolute.startswith("\\\\?\\"):
        absolute = (
            "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
        )
    return Path(absolute)


@dataclass(frozen=True)
class SQLiteStage:
    directory: Path
    backup: Path
    candidate: Path
    source_revision: str | None
    target_revision: str
    backup_sha256: str
    candidate_sha256: str
    source_identity: dict


def _regular_path(path: Path, *, directory: bool = False) -> Path:
    """Reject links/reparse points before resolving the complete existing path."""
    path = path.absolute()
    for part in (path, *path.parents):
        native = _native_path(part)
        info = native.lstat()
        if native.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise MigrationRefusedError("Reparse or symbolic-link migration path refused.")
    if not (_native_path(path).is_dir() if directory else _native_path(path).is_file()):
        raise MigrationRefusedError("Migration path has the wrong file type.")
    return path.resolve()


def _connect(path: Path, *, readonly: bool = True) -> sqlite3.Connection:
    # Path.as_uri() treats the extended Win32 prefix as a URI authority. Quote
    # the native filename instead, retaining SQLite's actual ro/rw enforcement.
    filename = (
        "file:" + quote(str(_native_path(path)), safe="")
        if os.name == "nt" and len(str(path.absolute())) >= 240
        else path.as_uri()
    )
    uri = filename + ("?mode=ro" if readonly else "?mode=rw")
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _check(connection: sqlite3.Connection) -> None:
    if list(connection.execute("PRAGMA integrity_check")) != [("ok",)]:
        raise MigrationRefusedError("SQLite integrity verification failed.")
    if list(connection.execute("PRAGMA foreign_key_check")):
        raise MigrationRefusedError("SQLite foreign-key verification failed.")


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _tables(connection: sqlite3.Connection) -> Iterator[tuple[str, list[str]]]:
    names = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND lower(substr(name,1,7)) != 'sqlite_' AND name != 'alembic_version' ORDER BY name"
    ).fetchall()
    for (name,) in names:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(name)})")]
        yield name, columns


def _value(value: Any) -> tuple[str, Any]:
    if value is None:
        return "null", None
    if isinstance(value, bytes):
        return "bytes", value.hex()
    if isinstance(value, float):
        return "float", value.hex()
    if isinstance(value, int):
        return "int", str(value)
    if isinstance(value, str):
        return "text", value
    raise MigrationRefusedError("Unexpected SQLite value type.")


def _rows_hash(connection: sqlite3.Connection, table: str, columns: list[str]) -> dict:
    # Streaming, order-independent content evidence; duplicates contribute to
    # count and both independent cryptographic sums. No raw row is persisted.
    count = total256 = total512 = 0
    projection = ",".join(_quote(column) for column in columns)
    for row in connection.execute(f"SELECT {projection} FROM {_quote(table)}"):
        encoded = json.dumps([_value(value) for value in row], ensure_ascii=True).encode()
        count += 1
        total256 = (total256 + int.from_bytes(hashlib.sha256(encoded).digest())) % (1 << 256)
        total512 = (total512 + int.from_bytes(hashlib.sha512(encoded).digest())) % (1 << 512)
    return {"rows": count, "sum_sha256": f"{total256:064x}", "sum_sha512": f"{total512:0128x}"}


def _inventory(connection: sqlite3.Connection) -> dict:
    return {
        name: {
            "columns": columns,
            "content": _rows_hash(connection, name, columns),
            "constraints": _constraints(connection, name),
        }
        for name, columns in _tables(connection)
    }


def _constraints(connection: sqlite3.Connection, table: str) -> dict:
    """Retain old type/nullability/PK/FK/uniqueness, not only today's valid rows."""
    columns = {
        row[1]: {"type": row[2].upper(), "not_null": row[3], "primary_position": row[5]}
        for row in connection.execute(f"PRAGMA table_info({_quote(table)})")
    }
    grouped: dict[int, list] = {}
    for row in connection.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
        grouped.setdefault(row[0], []).append(row)
    foreign = []
    for rows in grouped.values():
        rows.sort(key=lambda row: row[1])
        foreign.append(
            {
                "target": rows[0][2],
                "from": [row[3] for row in rows],
                "to": [row[4] for row in rows],
                "update": rows[0][5],
                "delete": rows[0][6],
                "match": rows[0][7],
            }
        )
    indexes = []
    for row in connection.execute(f"PRAGMA index_list({_quote(table)})"):
        keys = [
            {"column": key[2], "descending": key[3], "collation": key[4]}
            for key in connection.execute(f"PRAGMA index_xinfo({_quote(row[1])})")
            if key[5]
        ]
        definition = None
        if row[4] or any(key["column"] is None for key in keys):
            # An expression/partial index's SQL is required to prove its scope.
            definition = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='index' AND name=?", (row[1],)
            ).fetchone()[0]
        indexes.append({"keys": keys, "unique": row[2], "partial": row[4], "sql": definition})
    main_file = Path(connection.execute("PRAGMA database_list").fetchone()[2])
    # Public SQLAlchemy reflection parses SQLite CHECK clauses; do not invent
    # a SQL parser or let reflection connect to ambient application settings.
    engine = sa.create_engine(
        "sqlite://", creator=lambda: _connect(main_file), poolclass=sa.pool.NullPool
    )
    try:
        with engine.connect() as reflected:
            checks = sorted(
                item["sqltext"].strip()
                for item in sa.inspect(reflected).get_check_constraints(table)
            )
    finally:
        engine.dispose()
    return {
        "columns": columns,
        "foreign": sorted(foreign, key=lambda item: json.dumps(item, sort_keys=True)),
        "indexes": sorted(indexes, key=lambda item: json.dumps(item, sort_keys=True)),
        "checks": checks,
    }


def _preserved(connection: sqlite3.Connection, original: dict) -> None:
    current = dict(_tables(connection))
    for table, evidence in original.items():
        columns = evidence["columns"]
        if table not in current or not set(columns).issubset(current[table]):
            raise MigrationRefusedError("Upgrade removed a source table or column.")
        if _rows_hash(connection, table, columns) != evidence["content"]:
            raise MigrationRefusedError("Upgrade changed existing row content.")
        old_constraints = evidence["constraints"]
        new_constraints = _constraints(connection, table)
        if any(
            new_constraints["columns"][column] != old_constraints["columns"][column]
            for column in columns
        ):
            raise MigrationRefusedError(
                "Upgrade changed a source type, nullability or primary key."
            )
        for kind in ("foreign", "indexes", "checks"):
            if any(item not in new_constraints[kind] for item in old_constraints[kind]):
                raise MigrationRefusedError("Upgrade removed a source relational constraint.")


def _revision(connection: sqlite3.Connection) -> str:
    try:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.DatabaseError as exc:
        raise MigrationRefusedError(
            "Unversioned source requires a recognized legacy schema."
        ) from exc
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise MigrationRefusedError("Ambiguous source migration revision.")
    return rows[0][0]


def _source_identity(connection: sqlite3.Connection) -> dict:
    present = connection.execute(
        "SELECT type FROM main.sqlite_schema WHERE name='alembic_version'"
    ).fetchone()
    if present:
        if present != ("table",) or [
            row[1] for row in connection.execute("PRAGMA main.table_xinfo(alembic_version)")
        ] != ["version_num"]:
            raise MigrationRefusedError("Invalid migration version table.")
        revision = _revision(connection)
        return {"kind": "versioned", "revision": revision, "baseline_revision": revision}
    from app.research.sqlite_profiles import recognize_legacy

    return recognize_legacy(connection)


def _saved_identity(connection: sqlite3.Connection, receipt: dict) -> dict:
    actual = _source_identity(connection)
    # Old accepted versioned receipts remain readable; absent identity never
    # authorizes an unversioned backup or supplies a fabricated old version.
    recorded = receipt.get("source_identity")
    if recorded is None and actual["kind"] == "versioned":
        recorded = {
            "kind": "versioned",
            "revision": receipt.get("source_revision"),
            "baseline_revision": receipt.get("source_revision"),
        }
    if actual != recorded or actual["revision"] != receipt.get("source_revision"):
        raise MigrationRefusedError("Backup revision or schema identity changed.")
    return actual


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(_API_ROOT / "alembic"))
    return config


def _target(source: str, requested: str) -> str:
    script = ScriptDirectory.from_config(_config())
    try:
        target = script.get_current_head() if requested == "head" else requested
        if target is None or script.get_revision(source) is None:
            raise MigrationRefusedError("Unknown source migration revision.")
        chain = {item.revision for item in script.iterate_revisions(target, "base")}
        if source not in chain:
            raise MigrationRefusedError("Target is not a descendant of the source revision.")
        return target
    except MigrationRefusedError:
        raise
    except Exception as exc:
        raise MigrationRefusedError("Unsupported migration lineage.") from exc


def file_sha256(path: Path) -> str:
    with _native_path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sync_file(path: Path) -> None:
    with _native_path(path).open("r+b") as handle:
        os.fsync(handle.fileno())


def _record(directory: Path, name: str, document: dict) -> None:
    with _native_path(directory / name).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _upgrade(candidate: Path, target: str) -> None:
    # Use the injected connection, never environment/.env database settings.
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(_native_path(candidate))))
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("PRAGMA journal_mode=DELETE")
            connection.commit()
            config = _config()
            config.attributes["connection"] = connection
            command.upgrade(config, target)
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
            if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
                raise MigrationRefusedError("Candidate foreign-key enforcement unavailable.")
    finally:
        engine.dispose()


def _stamp_candidate(candidate: Path, baseline: str) -> None:
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(_native_path(candidate))))
    try:
        with engine.begin() as connection:
            config = _config()
            config.attributes["connection"] = connection
            command.stamp(config, baseline)
    finally:
        engine.dispose()


def stage_sqlite_upgrade(source: Path, directory: Path, *, target: str = "head") -> SQLiteStage:
    """Create one new stage; never publish, overwrite, stamp, or delete the source.

    The source snapshot uses SQLite's backup API, including committed WAL rows.
    Existing output directories are rejected rather than implicitly resumed.
    Re-entry/publication/restore require their own verified coordination contract.
    """
    source = _regular_path(source)
    parent = _regular_path(directory.parent, directory=True)
    directory = parent / directory.name
    if _native_path(directory).exists() or _native_path(directory).is_symlink():
        raise MigrationRefusedError("Stage directory already exists; evidence is preserved.")
    required = _native_path(source).stat().st_size
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(source.name + suffix)
        if _native_path(sidecar).exists():
            _regular_path(sidecar)
            required += _native_path(sidecar).stat().st_size
    if shutil.disk_usage(_native_path(parent)).free < 4 * required + _RESERVE_BYTES:
        raise MigrationRefusedError("Insufficient space for verified backup and upgrade stage.")
    backup, candidate = directory / "backup.sqlite3", directory / "candidate.sqlite3"
    _native_path(directory).mkdir()
    try:
        with (
            closing(_connect(source)) as original,
            closing(sqlite3.connect(_native_path(backup))) as saved,
        ):
            original.backup(saved, pages=256, sleep=0.01)
        _sync_file(backup)
        with closing(_connect(backup)) as saved:
            _check(saved)
            identity = _source_identity(saved)
            source_revision = identity["revision"]
            target_revision = _target(identity["baseline_revision"], target)
            inventory = _inventory(saved)
        backup_hash = file_sha256(backup)
        _record(
            directory,
            "01-backup.json",
            {
                "phase": "BACKUP_VERIFIED",
                "source_revision": source_revision,
                "source_identity": identity,
                "target_revision": target_revision,
                "backup_sha256": backup_hash,
                "backup_bytes": _native_path(backup).stat().st_size,
                "inventory": inventory,
            },
        )
        with (
            closing(_connect(backup)) as saved,
            closing(sqlite3.connect(_native_path(candidate))) as staged,
        ):
            saved.backup(staged, pages=256, sleep=0.01)
        _record(directory, "02-upgrading.json", {"phase": "UPGRADING"})
        if identity["kind"] == "unversioned":
            _stamp_candidate(candidate, identity["baseline_revision"])
        _upgrade(candidate, target_revision)
        _sync_file(candidate)
        with closing(_connect(candidate)) as staged:
            _check(staged)
            _preserved(staged, inventory)
            if _revision(staged) != target_revision:
                raise MigrationRefusedError(
                    "Candidate revision does not match the requested target."
                )
        if file_sha256(backup) != backup_hash:
            raise MigrationRefusedError("Verified backup changed during the upgrade.")
        candidate_hash = file_sha256(candidate)
        _record(
            directory,
            "03-verified.json",
            {
                "phase": "CANDIDATE_VERIFIED",
                "candidate_sha256": candidate_hash,
                "backup_sha256": backup_hash,
                "target_revision": target_revision,
            },
        )
        return SQLiteStage(
            directory,
            backup,
            candidate,
            source_revision,
            target_revision,
            backup_hash,
            candidate_hash,
            identity,
        )
    except BaseException as exc:
        # The files and original error survive. A full disk must not replace
        # the original cause with a secondary inability to write a receipt.
        try:
            _record(
                directory,
                "04-failed.json",
                {
                    "phase": "FAILED",
                    "error_type": type(exc).__name__,
                    "source_modified": False,
                },
            )
        except OSError:
            pass
        raise
