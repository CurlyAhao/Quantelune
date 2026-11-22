"""Exact, packaged SQLite schema identities; never infer a version from rows."""

import hashlib
import json
import sqlite3
from pathlib import Path

from app.research.migration import MigrationRefusedError, _quote, _regular_path

_PROFILE_PATH = Path(__file__).parent / "profiles" / "legacy-v106.json"
# Generated from the frozen, unchanged non-research model definitions.
_PROFILE_SHA256 = "69f2c5dd8fae871d7aaa4c301fef66ca466ce01164eab1db98627ceaac760dcc"


def schema_identity(connection: sqlite3.Connection) -> dict:
    """Include every persistent object and complete column/default/index DDL.

    SQL is deliberately compared exactly. An equivalent but unrecognized schema
    is refused rather than guessed. Root pages, data and journal mode are not
    schema identity. Hidden/generated columns, WITHOUT ROWID/STRICT, triggers,
    views and internal schema objects cannot disappear during recognition.
    """
    objects = [
        list(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name"
        )
    ]
    tables = {}
    for kind, name, _, _ in objects:
        if kind == "table":
            tables[name] = {
                "columns": [
                    list(row)
                    for row in connection.execute(f"PRAGMA main.table_xinfo({_quote(name)})")
                ],
                "foreign_keys": [
                    list(row)
                    for row in connection.execute(f"PRAGMA main.foreign_key_list({_quote(name)})")
                ],
                "indexes": [
                    {
                        "name": row[1],
                        "unique": row[2],
                        "origin": row[3],
                        "partial": row[4],
                        "keys": [
                            list(key)
                            for key in connection.execute(
                                f"PRAGMA main.index_xinfo({_quote(row[1])})"
                            )
                        ],
                    }
                    for row in sorted(
                        connection.execute(f"PRAGMA main.index_list({_quote(name)})"),
                        key=lambda row: row[1],
                    )
                ],
            }
    flags = sorted(
        [list(row[1:]) for row in connection.execute("PRAGMA main.table_list")],
        key=lambda row: row[0],
    )
    return {"objects": objects, "tables": tables, "table_flags": flags}


def recognize_legacy(connection: sqlite3.Connection) -> dict:
    path = _regular_path(_PROFILE_PATH)
    if path.stat().st_size > 1024 * 1024:
        raise MigrationRefusedError("Packaged SQLite profile exceeds its limit.")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _PROFILE_SHA256:
        raise MigrationRefusedError("Packaged SQLite profile signature changed.")
    profile = json.loads(raw)
    if schema_identity(connection) != profile["schema"]:
        raise MigrationRefusedError("Unrecognized unversioned SQLite schema.")
    return {
        "kind": "unversioned",
        "revision": None,
        "baseline_revision": profile["baseline_revision"],
        "profile": profile["id"],
        "profile_sha256": _PROFILE_SHA256,
    }
