"""Back up, restore-verify and transactionally upgrade a trusted PostgreSQL DB.

Only boot code supplies connections/runtime/paths; no Agent tool exposes this.
Failure retains backup evidence and rolls back the original schema transaction.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql.base import PGInspector
from sqlalchemy.engine import URL, Connection

from alembic import command
from app.research import migration as base

_LOCK_KEY = 0x53464F5247453038


def _value(value):
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, UUID):
        return ["uuid", str(value)]
    if isinstance(value, datetime):
        if value.tzinfo:
            value = value.astimezone(UTC)
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, dict):
        return ["object", {k: _value(v) for k, v in sorted(value.items())}]
    if isinstance(value, list):
        return ["array", [_value(v) for v in value]]
    if isinstance(value, memoryview):
        value = value.tobytes()
    return base._value(value)


def _name(connection: Connection, schema: str, table: str) -> str:
    quote = connection.dialect.identifier_preparer.quote
    return quote(schema) + "." + quote(table)


def _rows(connection: Connection, schema: str, table: str, columns: list[str]) -> dict:
    quote = connection.dialect.identifier_preparer.quote
    sql = (
        "SELECT "
        + ",".join(quote(c) for c in columns)
        + " FROM "
        + _name(connection, schema, table)
    )
    count = sum256 = sum512 = 0
    for row in connection.exec_driver_sql(sql):
        encoded = json.dumps([_value(v) for v in row], sort_keys=True, ensure_ascii=True).encode()
        if len(encoded) > 8 * 1024 * 1024:
            raise base.MigrationRefusedError("Migration row exceeds the verified content bound.")
        count += 1
        sum256 = (sum256 + int.from_bytes(hashlib.sha256(encoded).digest())) % (1 << 256)
        sum512 = (sum512 + int.from_bytes(hashlib.sha512(encoded).digest())) % (1 << 512)
    return {"count": count, "sum256": f"{sum256:064x}", "sum512": f"{sum512:0128x}"}


def _schemas(connection: Connection) -> list[str]:
    return [
        s
        for s in sa.inspect(connection).get_schema_names()
        if s != "information_schema" and not s.startswith("pg_")
    ]


def _constraints(connection: Connection, schema: str, table: str) -> dict:
    inspector = sa.inspect(connection)
    primary = inspector.get_pk_constraint(table, schema=schema)["constrained_columns"]
    foreign = [
        {k: v for k, v in item.items() if k != "name"}
        for item in inspector.get_foreign_keys(table, schema=schema)
    ]
    indexes = [
        {k: v for k, v in item.items() if k not in {"name", "duplicates_constraint"}}
        for item in inspector.get_indexes(table, schema=schema)
    ]
    checks = [
        _check_sql(item["sqltext"])
        for item in inspector.get_check_constraints(table, schema=schema)
    ]
    return {
        "primary": primary,
        "foreign": sorted(foreign, key=lambda x: json.dumps(x, sort_keys=True)),
        "indexes": sorted(indexes, key=lambda x: json.dumps(x, sort_keys=True)),
        "checks": sorted(checks),
    }


_ARRAY = re.compile(
    r"ARRAY\[((?:'(?:''|[^'])*'::character varying(?:::text)?)"
    r"(?:,\s*'(?:''|[^'])*'::character varying(?:::text)?)*)\](::text\[\])?"
)
_LITERAL = re.compile(r"('(?:''|[^'])*')::character varying(?P<text>::text)?")


def _check_sql(expression: str) -> str:
    """Normalize only PG's distributed unlimited-varchar→text literal arrays.

    pg_dump can spell ARRAY[varchar literals]::text[] as ARRAY[each::text].
    Values/order/operators remain exact; quoted strings/identifiers are skipped.
    Arbitrary casts, bounded varchar, SQL expressions or constraint weakening
    are never erased or evaluated.
    """
    result = []
    index = 0
    while index < len(expression):
        if expression[index] in {"'", '"'}:
            quote, start = expression[index], index
            index += 1
            while index < len(expression):
                if expression[index] == quote:
                    index += 1
                    if index < len(expression) and expression[index] == quote:
                        index += 1
                        continue
                    break
                index += 1
            result.append(expression[start:index])
            continue
        match = _ARRAY.match(expression, index)
        if match and (
            index == 0 or not (expression[index - 1].isalnum() or expression[index - 1] == "_")
        ):
            literals = list(_LITERAL.finditer(match[1]))
            if match[2] or all(literal["text"] for literal in literals):
                result.append(
                    "ARRAY[" + ", ".join(literal[1] + "::text" for literal in literals) + "]"
                )
                index = match.end()
                continue
        result.append(expression[index])
        index += 1
    return "".join(result)


def inventory(connection: Connection) -> dict:
    """Hash all user schemas without persisting raw rows or connection secrets."""
    inspector = cast(PGInspector, sa.inspect(connection))
    result = {}
    for schema in _schemas(connection):
        tables = {}
        for name in inspector.get_table_names(schema=schema):
            if schema == "public" and name == "alembic_version":
                continue
            columns: list[dict[str, Any]] = [
                {
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": c["nullable"],
                    "default": c.get("default"),
                    "identity": c.get("identity"),
                    "computed": c.get("computed"),
                }
                for c in inspector.get_columns(name, schema=schema)
            ]
            tables[name] = {
                "columns": columns,
                "rows": _rows(connection, schema, name, [c["name"] for c in columns]),
                "constraints": _constraints(connection, schema, name),
            }
        enums = {item["name"]: item["labels"] for item in inspector.get_enums(schema=schema)}
        sequences = {}
        for name in inspector.get_sequence_names(schema=schema):
            row = connection.exec_driver_sql(
                "SELECT last_value,is_called FROM " + _name(connection, schema, name)
            ).one()
            definition = (
                connection.execute(
                    sa.text("""
                SELECT format_type(s.seqtypid,NULL) AS data_type,s.seqstart AS start,
                       s.seqincrement AS increment,s.seqmin AS minimum,s.seqmax AS maximum,
                       s.seqcache AS cache,s.seqcycle AS cycle
                  FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname=:schema AND c.relname=:name
                """),
                    {"schema": schema, "name": name},
                )
                .mappings()
                .one()
            )
            ownership = (
                connection.execute(
                    sa.text("""
                SELECT tn.nspname AS schema,t.relname AS table,a.attname AS column,d.deptype AS kind
                  FROM pg_class s JOIN pg_namespace sn ON sn.oid=s.relnamespace
                  JOIN pg_depend d ON d.objid=s.oid AND d.classid='pg_class'::regclass
                  JOIN pg_class t ON t.oid=d.refobjid
                  JOIN pg_namespace tn ON tn.oid=t.relnamespace
                  JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
                 WHERE sn.nspname=:schema AND s.relname=:name AND d.deptype IN ('a','i')
                 ORDER BY tn.nspname,t.relname,a.attname,d.deptype
                """),
                    {"schema": schema, "name": name},
                )
                .mappings()
                .all()
            )
            sequences[name] = {
                "value": [row[0], row[1]],
                "definition": dict(definition),
                "ownership": [dict(item) for item in ownership],
            }
        result[schema] = {"tables": tables, "enums": enums, "sequences": sequences}
    return result


def _preserved(connection: Connection, before: dict) -> None:
    after = inventory(connection)
    for schema, original in before.items():
        if schema not in after:
            raise base.MigrationRefusedError("Upgrade removed a user schema.")
        current = after[schema]
        for kind in ("enums", "sequences"):
            if any(current[kind].get(k) != v for k, v in original[kind].items()):
                raise base.MigrationRefusedError("Upgrade changed a source enum or sequence.")
        for table, old in original["tables"].items():
            if table not in current["tables"]:
                raise base.MigrationRefusedError("Upgrade removed a source table.")
            new = current["tables"][table]
            lookup = {c["name"]: c for c in new["columns"]}
            for column in old["columns"]:
                actual = lookup.get(column["name"])
                if actual != column:
                    # Frozen 2028 fixes these timestamp defaults on PostgreSQL
                    # as well; no general default/type/nullability waiver.
                    expected = dict(column)
                    if (
                        schema == "public"
                        and table
                        in {
                            "research_charges",
                            "research_approvals",
                            "research_policies",
                            "research_budget_accounts",
                        }
                        and column["name"] in {"created_at", "updated_at"}
                        and column["default"] == "now()"
                    ):
                        expected["default"] = "CURRENT_TIMESTAMP"
                    if actual != expected:
                        raise base.MigrationRefusedError(
                            "Upgrade changed a source column contract."
                        )
            if _rows(connection, schema, table, [c["name"] for c in old["columns"]]) != old["rows"]:
                raise base.MigrationRefusedError("Upgrade changed original row content.")
            constraints = new["constraints"]
            if constraints["primary"] != old["constraints"]["primary"]:
                raise base.MigrationRefusedError("Upgrade changed the source primary key.")
            for kind in ("foreign", "indexes", "checks"):
                if any(c not in constraints[kind] for c in old["constraints"][kind]):
                    raise base.MigrationRefusedError("Upgrade removed a source constraint.")


def _revision(connection: Connection) -> str:
    rows = connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").all()
    if len(rows) != 1:
        raise base.MigrationRefusedError("Unknown or ambiguous PostgreSQL revision.")
    return rows[0][0]


def _observed_revision(connection: Connection) -> str | None:
    if not sa.inspect(connection).has_table("alembic_version", schema="public"):
        return None
    return _revision(connection)


def _environment(url: URL) -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}
    environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    environment.update(
        PGPASSFILE=os.devnull, PGCLIENTENCODING="UTF8", PGAPPNAME="StatForgeMigration"
    )
    if url.password:
        environment["PGPASSWORD"] = str(url.password)
    for parameter in ("sslmode", "sslrootcert", "sslcert", "sslkey"):
        value = url.query.get(parameter)
        if value is not None:
            if not isinstance(value, str):
                raise base.MigrationRefusedError("Invalid trusted PostgreSQL TLS configuration.")
            environment["PG" + parameter.upper()] = value
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        and environment.get("PGSSLMODE") != "verify-full"
    ):
        raise base.MigrationRefusedError("Remote PostgreSQL backup requires verified TLS.")
    return environment


def _arguments(url: URL) -> list[str]:
    _validate_url(url)
    if not url.host or not url.database or not url.username:
        raise base.MigrationRefusedError("Backup requires an explicit trusted PostgreSQL target.")
    return [
        "--host",
        url.host,
        "--port",
        str(url.port or 5432),
        "--username",
        url.username,
        "--dbname",
        url.database,
        "--no-password",
    ]


def _validate_url(url: URL) -> None:
    if not url.drivername.startswith("postgresql") or set(url.query) - {
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
    }:
        raise base.MigrationRefusedError("PostgreSQL connection redirection parameters refused.")
    if (
        not isinstance(url.database, str)
        or not url.database
        or "=" in url.database
        or "\x00" in url.database
        or url.database.startswith(("postgres://", "postgresql://"))
    ):
        raise base.MigrationRefusedError("A literal PostgreSQL database name is required.")
    if not url.host or not url.username or (url.port is not None and not 1 <= url.port <= 65535):
        raise base.MigrationRefusedError("Explicit PostgreSQL endpoint and identity required.")


def _connection_identity(connection: Connection, url: URL) -> tuple[str, str]:
    raw = cast(Any, connection.connection.driver_connection)
    if hasattr(raw, "info") and hasattr(raw.info, "host"):
        host, port, database, user = raw.info.host, raw.info.port, raw.info.dbname, raw.info.user
    elif hasattr(raw, "get_dsn_parameters"):
        parameters = raw.get_dsn_parameters()
        host, port, database, user = (
            parameters.get("host"),
            parameters.get("port"),
            parameters.get("dbname"),
            parameters.get("user"),
        )
    else:
        raise base.MigrationRefusedError("Cannot verify the PostgreSQL driver endpoint.")
    if (host, int(port), database, user) != (
        url.host,
        url.port or 5432,
        url.database,
        url.username,
    ):
        raise base.MigrationRefusedError("Driver connection differs from the trusted CLI target.")
    actual = connection.exec_driver_sql(
        "SELECT current_database(),system_identifier::text FROM pg_control_system()"
    ).one()
    if actual[0] != url.database:
        raise base.MigrationRefusedError("Actual PostgreSQL database identity mismatch.")
    return actual[0], actual[1]


def _tool(runtime: Path, name: str) -> Path:
    runtime = base._regular_path(runtime, directory=True)
    exe = runtime / "bin" / (name + (".exe" if os.name == "nt" else ""))
    return base._regular_path(exe)


def _run(argv: list[str], url: URL, *, timeout: int = 60) -> None:
    try:
        result = subprocess.run(
            argv,
            env=_environment(url),
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise base.MigrationRefusedError(
            "PostgreSQL backup/restore execution failed or timed out."
        ) from None
    if result.returncode:
        # Raw stderr can contain source data/connection parameters; keep it out
        # of event, journal and exception surfaces.
        raise base.MigrationRefusedError("PostgreSQL backup/restore process failed.")


def _upgrade(connection: Connection, target: str) -> None:
    config = base._config()
    config.attributes["connection"] = connection
    command.upgrade(config, target)


def _empty_restore(connection: Connection) -> bool:
    if _schemas(connection) != ["public"]:
        return False
    # A DB with a view/function/domain/extension is not an empty restore target,
    # even when ordinary tables/row counts happen to be zero.
    return not connection.exec_driver_sql("""
        SELECT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public'
            UNION ALL
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
              WHERE n.nspname='public'
            UNION ALL
            SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
              WHERE n.nspname='public'
            UNION ALL
            SELECT 1 FROM pg_extension WHERE extname != 'plpgsql'
        )
        """).scalar_one()


def _supported_source(connection: Connection) -> None:
    """Reject custom executable/foreign/derived objects outside Alpha's schema.

    Alpha persists ordinary tables, enums and sequences. Unknown custom objects
    must not be projected away or executed on restore. New support requires its
    own versioned schema and preservation evidence.
    """
    unsupported = connection.exec_driver_sql("""
        SELECT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname!='information_schema' AND left(n.nspname,3)!='pg_'
                AND c.relkind IN ('v','m','f','c')
            UNION ALL
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
              WHERE n.nspname!='information_schema' AND left(n.nspname,3)!='pg_'
            UNION ALL
            SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
              WHERE n.nspname!='information_schema' AND left(n.nspname,3)!='pg_'
                AND NOT (
                    t.typtype='e'
                    OR (t.typtype='c' AND EXISTS (
                        SELECT 1 FROM pg_class r WHERE r.oid=t.typrelid AND r.relkind IN ('r','p')
                    ))
                    OR (t.typtype='b' AND EXISTS (
                        SELECT 1 FROM pg_type e LEFT JOIN pg_class r ON r.oid=e.typrelid
                         WHERE e.oid=t.typelem AND e.typarray=t.oid
                           AND (e.typtype='e' OR (e.typtype='c' AND r.relkind IN ('r','p')))
                    ))
                )
            UNION ALL
            SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
              JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE NOT t.tgisinternal AND n.nspname!='information_schema'
                AND left(n.nspname,3)!='pg_'
            UNION ALL
            SELECT 1 FROM pg_extension WHERE extname!='plpgsql'
            UNION ALL
            SELECT 1 FROM pg_foreign_server
        )
        """).scalar_one()
    if unsupported:
        raise base.MigrationRefusedError(
            "Unsupported custom PostgreSQL source objects; source preserved."
        )


def upgrade_postgres(
    connection: Connection,
    directory: Path,
    runtime: Path,
    restore_url: URL,
    *,
    target: str = "head",
    verify_source: Callable[[Connection], None] | None = None,
    verify_target: Callable[[Connection], None] | None = None,
    unversioned_baseline: str | None = None,
) -> dict:
    """Verify a real restored backup before mutating the original transaction."""
    if connection.dialect.name != "postgresql" or connection.in_transaction():
        raise base.MigrationRefusedError("A fresh trusted PostgreSQL connection is required.")
    source_url = connection.engine.url
    _validate_url(source_url)
    _validate_url(restore_url)
    source_identity = _connection_identity(connection, source_url)
    connection.rollback()
    if (source_url.host, source_url.port or 5432, source_url.database) == (
        restore_url.host,
        restore_url.port or 5432,
        restore_url.database,
    ):
        raise base.MigrationRefusedError("Restore target must differ from the source database.")
    parent = base._regular_path(directory.parent, directory=True)
    directory = parent / directory.name
    if directory.exists() or directory.is_symlink():
        raise base.MigrationRefusedError("Backup evidence directory already exists.")
    dump, restore = _tool(runtime, "pg_dump"), _tool(runtime, "pg_restore")
    restored_engine = sa.create_engine(restore_url, poolclass=sa.pool.NullPool)
    try:
        with restored_engine.connect() as restored:
            restored_identity = _connection_identity(restored, restore_url)
            if (
                source_identity[1] != restored_identity[1]
                or source_identity[0] == restored_identity[0]
            ):
                raise base.MigrationRefusedError(
                    "Restore cluster/database identity is not independent."
                )
            if not _empty_restore(restored):
                raise base.MigrationRefusedError("Restore target contains user objects.")
        directory.mkdir()
        try:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                if not connection.execute(
                    sa.text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
                ).scalar_one():
                    raise base.MigrationRefusedError("Another PostgreSQL migration owns the lock.")
                _supported_source(connection)
                inspector = sa.inspect(connection)
                names = [
                    _name(connection, schema, table)
                    for schema in _schemas(connection)
                    for table in inspector.get_table_names(schema=schema)
                ]
                if names:
                    connection.exec_driver_sql("LOCK TABLE " + ",".join(names) + " IN SHARE MODE")
                if verify_source is not None:
                    verify_source(connection)
                source_revision = _observed_revision(connection)
                if source_revision is None:
                    if unversioned_baseline is None or verify_source is None:
                        raise base.MigrationRefusedError(
                            "Unversioned PostgreSQL source requires a verified profile."
                        )
                    baseline = unversioned_baseline
                else:
                    if unversioned_baseline is not None:
                        raise base.MigrationRefusedError("Observed source is not unversioned.")
                    baseline = source_revision
                target_revision = base._target(baseline, target)
                before = inventory(connection)
                size = connection.exec_driver_sql(
                    "SELECT pg_database_size(current_database())"
                ).scalar_one()
                if shutil.disk_usage(parent).free < 4 * size + base._RESERVE_BYTES:
                    raise base.MigrationRefusedError("Insufficient space for PostgreSQL backup.")
                snapshot = connection.exec_driver_sql("SELECT pg_export_snapshot()").scalar_one()
                backup = directory / "backup.pgdump"
                _run(
                    [
                        str(dump),
                        *_arguments(source_url),
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                        "--snapshot",
                        snapshot,
                        "--file",
                        str(backup),
                    ],
                    source_url,
                )
                base._sync_file(backup)
                backup_hash = base.file_sha256(backup)
                base._record(
                    directory,
                    "01-backup.json",
                    {
                        "phase": "BACKUP_CREATED",
                        "source_revision": source_revision,
                        "source_identity": {
                            "mode": "unversioned" if source_revision is None else "versioned",
                            "observed_revision": source_revision,
                            "baseline_revision": baseline,
                        },
                        "target_revision": target_revision,
                        "backup_sha256": backup_hash,
                        "backup_bytes": backup.stat().st_size,
                        "inventory": before,
                    },
                )
                _run(
                    [
                        str(restore),
                        *_arguments(restore_url),
                        "--exit-on-error",
                        "--no-owner",
                        "--no-privileges",
                        str(backup),
                    ],
                    restore_url,
                )
                with restored_engine.connect() as restored:
                    if (
                        inventory(restored) != before
                        or _observed_revision(restored) != source_revision
                    ):
                        raise base.MigrationRefusedError(
                            "Restored backup does not match the original."
                        )
                base._record(directory, "02-restored.json", {"phase": "BACKUP_RESTORE_VERIFIED"})
                # Rehearse the fixed migration on the proven copy before source
                # DDL: nextval/setval effects are not reverted by PG rollback.
                with restored_engine.begin() as rehearsed:
                    if source_revision is None:
                        config = base._config()
                        config.attributes["connection"] = rehearsed
                        command.stamp(config, baseline)
                    _upgrade(rehearsed, target_revision)
                    _preserved(rehearsed, before)
                    if _revision(rehearsed) != target_revision:
                        raise base.MigrationRefusedError("Rehearsed migration revision mismatch.")
                    if verify_target is not None:
                        verify_target(rehearsed)
                base._record(directory, "02a-preflight.json", {"phase": "COPY_UPGRADE_VERIFIED"})
                if source_revision is None:
                    config = base._config()
                    config.attributes["connection"] = connection
                    command.stamp(config, baseline)
                _upgrade(connection, target_revision)
                _preserved(connection, before)
                if verify_target is not None:
                    verify_target(connection)
                if (
                    _revision(connection) != target_revision
                    or base.file_sha256(backup) != backup_hash
                ):
                    raise base.MigrationRefusedError("Upgrade or backup identity mismatch.")
                base._record(
                    directory, "03-before-commit.json", {"phase": "UPGRADE_VERIFIED_NOT_COMMITTED"}
                )
            actual_revision = _revision(connection)
            connection.rollback()  # End the verification read, not the committed upgrade.
            if actual_revision != target_revision:
                raise base.MigrationRefusedError("Committed PostgreSQL revision mismatch.")
            result = {
                "phase": "COMMITTED",
                "source_revision": source_revision,
                "source_identity": {
                    "mode": "unversioned" if source_revision is None else "versioned",
                    "observed_revision": source_revision,
                    "baseline_revision": baseline,
                },
                "target_revision": target_revision,
                "backup_sha256": backup_hash,
            }
            base._record(directory, "04-committed.json", result)
            return result
        except BaseException as exc:
            try:
                base._record(
                    directory,
                    "05-failed.json",
                    {"phase": "FAILED_OR_COMMIT_UNKNOWN", "error_type": type(exc).__name__},
                )
            except OSError:
                pass
            raise
    finally:
        restored_engine.dispose()
