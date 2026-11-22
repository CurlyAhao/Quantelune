"""Normalize declaration order, retaining every schema expression and object.

Only SQLAlchemy's separate table-constraint lines are reordered. Quoted text,
comments, column order, expression text and all other DDL stay byte-exact.
"""

import copy
import hashlib
import json


def _ordered_constraints(sql: str) -> str:
    lines = sql.splitlines(keepends=True)
    quote = None
    eligible = []
    endings = {}
    for index, line in enumerate(lines):
        if quote is None and line.startswith(
            ("\tCONSTRAINT ", "\tFOREIGN KEY(", "\tPRIMARY KEY (", "\tUNIQUE (")
        ):
            if line.endswith(", \n"):
                eligible.append(index)
                endings[index] = ", \n"
            elif line.endswith("\n") and index + 1 < len(lines) and lines[index + 1].strip() == ")":
                eligible.append(index)
                endings[index] = "\n"
        position = 0
        while position < len(line):
            character = line[position]
            if quote is not None:
                if character == quote:
                    if position + 1 < len(line) and line[position + 1] == quote:
                        position += 2
                        continue
                    quote = None
            elif line[position : position + 2] in ("--", "/*"):
                # No comment processing or expression rewriting is allowed.
                return sql
            elif character in "'\"`":
                quote = character
            elif character == "[":
                quote = "]"
            position += 1
        if quote is not None and index in eligible:
            return sql
    if quote is not None:
        return sql
    ordered = sorted(lines[index][: -len(endings[index])] for index in eligible)
    for index, value in zip(eligible, ordered, strict=True):
        lines[index] = value + endings[index]
    return "".join(lines)


def normalize_schema(schema: dict) -> dict:
    result = copy.deepcopy(schema)
    renamed = {}
    for table, definition in result["tables"].items():
        grouped: dict[int, list] = {}
        for row in definition["foreign_keys"]:
            grouped.setdefault(row[0], []).append(row[1:])
        definition["foreign_keys"] = sorted(
            [sorted(rows, key=lambda row: row[0]) for rows in grouped.values()],
            key=lambda item: json.dumps(item, sort_keys=True),
        )
        for index in definition["indexes"]:
            if index["origin"] in ("pk", "u") and index["name"].startswith(
                "sqlite_autoindex_" + table + "_"
            ):
                old = index["name"]
                payload = {key: value for key, value in index.items() if key != "name"}
                name = (
                    "sqlite_autoindex:"
                    + table
                    + ":"
                    + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                )
                renamed[old] = name
                index["name"] = name
        definition["indexes"].sort(key=lambda item: json.dumps(item, sort_keys=True))
    for row in result["objects"]:
        if row[0] == "table" and isinstance(row[3], str):
            row[3] = _ordered_constraints(row[3])
        if row[0] == "index" and row[1] in renamed and row[3] is None:
            row[1] = renamed[row[1]]
    result["objects"].sort(key=lambda row: (row[0], row[1]))
    return result


def version_component(schema: dict) -> dict:
    return {
        "objects": [row for row in schema["objects"] if row[2] == "alembic_version"],
        "table": schema["tables"].get("alembic_version"),
        "flags": [row for row in schema["table_flags"] if row[0] == "alembic_version"],
    }


def known_plain_version(schema: dict) -> bool:
    """The frozen legacy test/source form, without weakening other version DDL."""
    return version_component(schema) == {
        "objects": [
            [
                "table",
                "alembic_version",
                "alembic_version",
                "CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL)",
            ]
        ],
        "table": {
            "columns": [[0, "version_num", "VARCHAR(32)", 1, None, 0, 0]],
            "foreign_keys": [],
            "indexes": [],
        },
        "flags": [["alembic_version", "table", 1, 0, 0]],
    }
