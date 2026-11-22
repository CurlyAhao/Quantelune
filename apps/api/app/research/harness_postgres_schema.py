"""Exact official saver namespace; creation requires the real exclusive boot lease."""

import hashlib
import json
from pathlib import Path

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from app.research import postgres_migration as pg
from app.research.harness_postgres_proof import require_configuration
from app.services import research_service as ledger

NAMESPACE = "statforge_h02"
PROFILE = Path(__file__).with_name("profiles") / "h02-postgres-sdk-v1.json"
PROFILE_SHA256 = "92f4c3dc8831727aa1d15d68ecd49ac129fb979bb06a75d0247454219ef37d38"

# No owner, ACL, trigger, policy, type, relation or constraint is silently ignored.
# Owner is compared with the actual connection role, never a supplied actor ID.
CATALOG_SQL = """
SELECT jsonb_build_object(
  'namespace', (SELECT jsonb_build_object('name',nspname,
     'own_role',nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user),
     'acl',nspacl::text) FROM pg_namespace WHERE nspname=%(ns)s),
  'relations', (SELECT coalesce(jsonb_agg(jsonb_build_object(
     'name',c.relname,'kind',c.relkind,'persistence',c.relpersistence,
     'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
     'own_role',c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user),
     'acl',c.relacl::text) ORDER BY c.relname),'[]'::jsonb)
     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%(ns)s),
  'columns', (SELECT coalesce(jsonb_agg(jsonb_build_object(
     'table',c.relname,'position',a.attnum,'name',a.attname,
     'type',format_type(a.atttypid,a.atttypmod),'not_null',a.attnotnull,
     'default',pg_get_expr(d.adbin,d.adrelid),'identity',a.attidentity,
     'generated',a.attgenerated,'acl',a.attacl::text) ORDER BY c.relname,a.attnum),'[]'::jsonb)
     FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace
     LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
     WHERE n.nspname=%(ns)s AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped),
  'constraints', (SELECT coalesce(jsonb_agg(jsonb_build_object(
     'table',c.relname,'name',k.conname,'type',k.contype,'valid',k.convalidated,
     'deferred',k.condeferred,'deferrable',k.condeferrable,
     'definition',pg_get_constraintdef(k.oid,true)) ORDER BY c.relname,k.conname),'[]'::jsonb)
     FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%(ns)s),
  'indexes', (SELECT coalesce(jsonb_agg(jsonb_build_object(
     'name',c.relname,'valid',x.indisvalid,'ready',x.indisready,
     'definition',pg_get_indexdef(c.oid)) ORDER BY c.relname),'[]'::jsonb)
     FROM pg_index x JOIN pg_class c ON c.oid=x.indexrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%(ns)s),
  'types', (SELECT coalesce(jsonb_agg(jsonb_build_object(
     'name',t.typname,'kind',t.typtype,'relation',c.relname,
     'own_role',t.typowner=(SELECT oid FROM pg_roles WHERE rolname=current_user),
     'acl',t.typacl::text) ORDER BY t.typname),'[]'::jsonb)
     FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
     LEFT JOIN pg_class c ON c.oid=t.typrelid WHERE n.nspname=%(ns)s),
  'functions', (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname=%(ns)s),
  'triggers', (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%(ns)s),
  'rules', (SELECT count(*) FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%(ns)s),
  'policies', (SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%(ns)s),
  'inheritance', (SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%(ns)s)
)
"""


def expected():
    raw = PROFILE.read_bytes()
    if len(raw) > 128 * 1024 or hashlib.sha256(raw).hexdigest() != PROFILE_SHA256:
        raise ledger.error("HARNESS_POSTGRES_PROFILE_REFUSED")
    return json.loads(raw)


def validate(catalog, versions):
    if catalog != expected() or versions != list(range(len(PostgresSaver.MIGRATIONS))):
        raise ledger.error("HARNESS_SCHEMA_REFUSED")


def connection_args(source):
    pg._validate_url(source)
    pg._environment(source)
    return dict(
        host=source.host,
        port=source.port or 5432,
        dbname=source.database,
        user=source.username,
        password=source.password,
        **dict(source.query),
        connect_timeout=5,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )


def initialize(lease):
    """Only trusted startup may create an absent namespace; never repair an existing one."""
    import sqlalchemy as sa

    require_configuration()

    if lease.connection.in_transaction():
        raise ledger.error("HARNESS_POSTGRES_SETUP_TRANSACTION_REFUSED")
    proof = lease.connection.execute(
        sa.text("""SELECT pg_backend_pid(), EXISTS (
          SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=:high AND objid=:low AND objsubid=1 AND granted AND mode='ExclusiveLock'
        )"""),
        {"high": pg._LOCK_KEY >> 32, "low": pg._LOCK_KEY & 0xFFFFFFFF},
    ).one()
    lease.connection.rollback()
    if proof != (lease.backend_pid, True):
        raise ledger.error("HARNESS_POSTGRES_SETUP_LEASE_REFUSED")
    with psycopg.connect(**connection_args(lease.source)) as connection:
        row = connection.execute(CATALOG_SQL, {"ns": NAMESPACE}).fetchone()
        if row is None:
            raise ledger.error("HARNESS_SCHEMA_REFUSED")
        actual = row["jsonb_build_object"]
        if actual["namespace"] is None:
            # Fixed server name, not an input identifier; no privilege changes.
            connection.execute("CREATE SCHEMA statforge_h02 AUTHORIZATION CURRENT_USER")
            connection.execute("SET search_path TO statforge_h02, pg_catalog")
            PostgresSaver(connection).setup()  # Official CONCURRENTLY runs outside a transaction.
            row = connection.execute(CATALOG_SQL, {"ns": NAMESPACE}).fetchone()
            if row is None:
                raise ledger.error("HARNESS_SCHEMA_REFUSED")
            actual = row["jsonb_build_object"]
        else:
            if actual != expected():
                raise ledger.error("HARNESS_SCHEMA_REFUSED")
            connection.execute("SET search_path TO statforge_h02, pg_catalog")
        versions = [
            row["v"]
            for row in connection.execute(
                "SELECT v FROM statforge_h02.checkpoint_migrations ORDER BY v"
            ).fetchall()
        ]
        validate(actual, versions)


def verify_sync(connection):
    """Read-only catalog/owner/ACL check on the actual SQLAlchemy transaction."""
    import sqlalchemy as sa

    statement = CATALOG_SQL.replace("%(ns)s", ":ns")
    catalog = connection.execute(sa.text(statement), {"ns": NAMESPACE}).scalar_one()
    if catalog != expected():
        raise ledger.error("HARNESS_SCHEMA_REFUSED")
    versions = (
        connection.exec_driver_sql("SELECT v FROM statforge_h02.checkpoint_migrations ORDER BY v")
        .scalars()
        .all()
    )
    validate(catalog, list(versions))


async def verify(connection):
    cursor = await connection.execute(CATALOG_SQL, {"ns": NAMESPACE})
    catalog = (await cursor.fetchone())["jsonb_build_object"]
    # Validate DDL/ACL before a read of any potentially changed saver table.
    if catalog != expected():
        raise ledger.error("HARNESS_SCHEMA_REFUSED")
    cursor = await connection.execute(
        "SELECT v FROM statforge_h02.checkpoint_migrations ORDER BY v"
    )
    validate(catalog, [row["v"] for row in await cursor.fetchall()])
