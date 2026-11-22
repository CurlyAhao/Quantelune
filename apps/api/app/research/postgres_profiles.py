"""Pinned actual PostgreSQL catalog profiles, not row-count recognition."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql.base import PGInspector
from sqlalchemy.engine import Connection

from app.research import migration as base
from app.research import postgres_migration as pg

PROFILE = Path(__file__).with_name("profiles") / "postgres-startup-v12.json"
PROFILE_SHA256 = "5da8de734f705d09dff011e76a4b4940590be15e03dc930949016be8e5ddc36a"


@dataclass(frozen=True)
class CatalogProfiles:
    head: str
    domain: dict
    sdk: dict


def schema(connection: Connection) -> dict:
    pg._supported_source(connection)
    inspector = cast(PGInspector, sa.inspect(connection))
    result = {}
    for namespace in pg._schemas(connection):
        tables = {}
        for name in sorted(inspector.get_table_names(schema=namespace)):
            columns = []
            for column in inspector.get_columns(name, schema=namespace):
                columns.append(
                    {
                        k: (str(v) if k == "type" else v)
                        for k, v in column.items()
                        if k in {"name", "type", "nullable", "default", "identity", "computed"}
                    }
                )
            native_columns = (
                connection.execute(
                    sa.text("""
                SELECT a.attname, format_type(a.atttypid,a.atttypmod) AS format_type,
                       tn.nspname AS type_schema,t.typname,t.typtype,
                       cn.nspname AS collation_schema,co.collname,
                       a.attidentity,a.attgenerated,a.attstorage,a.attcompression
                  FROM pg_attribute a JOIN pg_class r ON r.oid=a.attrelid
                  JOIN pg_namespace n ON n.oid=r.relnamespace
                  JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace tn ON tn.oid=t.typnamespace
                  LEFT JOIN pg_collation co ON co.oid=a.attcollation
                  LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace
                 WHERE n.nspname=:schema AND r.relname=:table AND a.attnum>0 AND NOT a.attisdropped
                 ORDER BY a.attnum
            """),
                    {"schema": namespace, "table": name},
                )
                .mappings()
                .all()
            )
            # Names, validation, actions and index expressions remain exact.
            constraints = (
                connection.execute(
                    sa.text("""
                SELECT conname, contype, convalidated, condeferrable, condeferred,
                       pg_get_constraintdef(c.oid, true) AS definition
                  FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
                  JOIN pg_namespace n ON n.oid=t.relnamespace
                 WHERE n.nspname=:schema AND t.relname=:table ORDER BY conname
            """),
                    {"schema": namespace, "table": name},
                )
                .mappings()
                .all()
            )
            indexes = (
                connection.execute(
                    sa.text("""
                SELECT i.relname, x.indisvalid, x.indisready, x.indisreplident,
                       pg_get_indexdef(i.oid) AS definition
                  FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
                  JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace
                 WHERE n.nspname=:schema AND t.relname=:table ORDER BY i.relname
            """),
                    {"schema": namespace, "table": name},
                )
                .mappings()
                .all()
            )
            flags = (
                connection.execute(
                    sa.text("""
                SELECT relkind, relrowsecurity, relforcerowsecurity, relpersistence, relreplident
                  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname=:schema AND c.relname=:table
            """),
                    {"schema": namespace, "table": name},
                )
                .mappings()
                .one()
            )
            parents = (
                connection.execute(
                    sa.text("""
                SELECT pn.nspname AS schema,p.relname,i.inhseqno
                  FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
                  JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_class p ON p.oid=i.inhparent
                  JOIN pg_namespace pn ON pn.oid=p.relnamespace
                 WHERE n.nspname=:schema AND c.relname=:table ORDER BY i.inhseqno
            """),
                    {"schema": namespace, "table": name},
                )
                .mappings()
                .all()
            )
            tables[name] = {
                "columns": columns,
                "native_columns": [dict(c) for c in native_columns],
                "constraints": [_constraint(dict(c)) for c in constraints],
                "indexes": [dict(i) for i in indexes],
                "flags": dict(flags),
                "parents": [dict(p) for p in parents],
            }
        sequences = (
            connection.execute(
                sa.text("""
            SELECT c.relname, format_type(s.seqtypid,NULL) AS type, s.seqstart,
                   s.seqincrement, s.seqmin, s.seqmax, s.seqcache, s.seqcycle
              FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
              JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema
             ORDER BY c.relname
        """),
                {"schema": namespace},
            )
            .mappings()
            .all()
        )
        result[namespace] = {
            "tables": tables,
            "enums": {e["name"]: e["labels"] for e in inspector.get_enums(schema=namespace)},
            "sequences": [dict(s) for s in sequences],
        }
    return result


def profiles(head: str) -> CatalogProfiles:
    path = base._regular_path(PROFILE)
    with path.open("rb") as stream:
        encoded = stream.read(8 * 1024 * 1024 + 1)
    if len(encoded) > 8 * 1024 * 1024 or hashlib.sha256(encoded).hexdigest() != PROFILE_SHA256:
        raise base.MigrationRefusedError("PostgreSQL profile signature mismatch.")
    document = json.loads(encoded)
    if document["head"] != head:
        raise base.MigrationRefusedError(
            "PostgreSQL profile does not cover the current migration head."
        )
    accepted = expand_profiles(document)
    for variants in accepted.values():
        for variant in variants:
            for namespace in variant.values():
                for table in namespace["tables"].values():
                    table["constraints"] = [_constraint(c) for c in table["constraints"]]
    return CatalogProfiles(head, accepted, document["sdk_namespace"])


def expand_profiles(document: dict) -> dict:
    """Exact shared table definitions reduce repeated catalog bytes, never recognition."""
    if "catalog_encoding" not in document:
        if "table_pool" in document:
            raise base.MigrationRefusedError("Invalid PostgreSQL catalog encoding.")
        return document["profiles"]
    if document["catalog_encoding"] != "table-ref-v1":
        raise base.MigrationRefusedError("Unknown PostgreSQL catalog encoding.")
    pool = document.get("table_pool")
    if not isinstance(pool, dict) or not 1 <= len(pool) <= 2048:
        raise base.MigrationRefusedError("Invalid PostgreSQL catalog pool.")
    for digest, table in pool.items():
        if (
            not isinstance(table, dict)
            or hashlib.sha256(_catalog_bytes(table)).hexdigest() != digest
        ):
            raise base.MigrationRefusedError("PostgreSQL table definition signature mismatch.")
    revisions = document["profiles"]
    if not isinstance(revisions, dict) or not 1 <= len(revisions) <= 128:
        raise base.MigrationRefusedError("Invalid PostgreSQL catalog revisions.")
    result, used = {}, set()
    for revision, variants in revisions.items():
        if not isinstance(variants, list) or not 1 <= len(variants) <= 8:
            raise base.MigrationRefusedError("Invalid PostgreSQL catalog variants.")
        expanded = []
        for variant in variants:
            actual = {}
            if not isinstance(variant, dict) or not 1 <= len(variant) <= 16:
                raise base.MigrationRefusedError("Invalid PostgreSQL catalog namespaces.")
            for namespace, value in variant.items():
                if not isinstance(value, dict) or set(value) != {"tables", "enums", "sequences"}:
                    raise base.MigrationRefusedError("Invalid PostgreSQL catalog namespace.")
                tables = value["tables"]
                if not isinstance(tables, dict) or not 1 <= len(tables) <= 512:
                    raise base.MigrationRefusedError("Invalid PostgreSQL catalog tables.")
                resolved = {}
                for name, reference in tables.items():
                    if (
                        not isinstance(reference, dict)
                        or set(reference) != {"table_sha256"}
                        or not isinstance(reference["table_sha256"], str)
                        or reference["table_sha256"] not in pool
                    ):
                        raise base.MigrationRefusedError("Unproven PostgreSQL table reference.")
                    digest = reference["table_sha256"]
                    used.add(digest)
                    resolved[name] = pool[digest]
                actual[namespace] = dict(value, tables=resolved)
            expanded.append(actual)
        result[revision] = expanded
    if used != set(pool):
        raise base.MigrationRefusedError("Unreferenced PostgreSQL catalog definitions.")
    return result


def _catalog_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def compact_profiles(document: dict) -> dict:
    """Developer profile-generation helper; rehydration must be exactly identical."""
    original = expand_profiles(document)
    pool: dict[str, dict] = {}
    compact: dict[str, list[dict]] = {}
    for revision, variants in original.items():
        compact[revision] = []
        for variant in variants:
            packed = {}
            for namespace, value in variant.items():
                refs = {}
                for name, table in value["tables"].items():
                    digest = hashlib.sha256(_catalog_bytes(table)).hexdigest()
                    pool[digest] = table
                    refs[name] = {"table_sha256": digest}
                packed[namespace] = dict(value, tables=refs)
            compact[revision].append(packed)
    result = dict(document, catalog_encoding="table-ref-v1", table_pool=pool, profiles=compact)
    if expand_profiles(result) != original:
        raise base.MigrationRefusedError("Catalog compaction changed an accepted schema.")
    return result


def _constraint(item: dict) -> dict:
    if item["contype"] == "c":
        # Reuse the accepted exact PG varchar-literal-array cast distribution,
        # preserving values/order/operators and all other constraint attributes.
        item["definition"] = pg._check_sql(item["definition"])
    return item


def verify_stage(
    connection: Connection, revision: str, accepted: CatalogProfiles, *, sdk_present: bool
) -> None:
    """Migration keeps the original SDK state; this is never a READY exit."""
    actual = schema(connection)
    expected = accepted.domain.get(revision, [])
    if sdk_present:
        from app.research.harness_postgres_schema import verify_sync

        verify_sync(connection)
        expected = [dict(candidate, statforge_h02=accepted.sdk) for candidate in expected]
    if actual not in expected:
        raise base.MigrationRefusedError(
            "Unknown or changed PostgreSQL startup schema; source preserved."
        )


def verify(connection: Connection, revision: str, accepted: CatalogProfiles) -> None:
    """Default validation is the full current domain and initialized SDK catalog."""
    if revision != accepted.head:
        raise base.MigrationRefusedError("READY requires the actual current PostgreSQL head.")
    verify_stage(connection, revision, accepted, sdk_present=True)


def recognize_unversioned(
    connection: Connection, accepted: CatalogProfiles, *, sdk_present: bool = False
) -> str:
    actual = schema(connection)
    # Only the exact frozen legacy/full-head schema with no version table.
    # No table-count inference and no source stamp. Old backup stays unversioned.
    if sdk_present:
        from app.research.harness_postgres_schema import verify_sync

        verify_sync(connection)
    for baseline in ("aacbcf50997e", "6a07c0de2029", accepted.head):
        for candidate in accepted.domain.get(baseline, []):
            expected = json.loads(json.dumps(candidate))
            expected["public"]["tables"].pop("alembic_version")
            if sdk_present:
                expected["statforge_h02"] = accepted.sdk
            if actual == expected:
                return baseline
    raise base.MigrationRefusedError("Unversioned PostgreSQL schema is not recognized.")
