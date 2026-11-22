"""One private Gateway preparation, inside an already proven owner transaction.

This is never an authorization cache. Only the fixed read-only Gateway preparation
and live PlannerModel authorization graphs may use it, immediately after complete
PlanningToken/source validation. The new-approval preflight finishes and closes
before the waiting transition; its following proposal requires fresh proof.
Normal writes contend on the same owner fence;
each later source boundary still projects real bytes and ACL afresh. Raw DBAPI
execution is outside the observed interface and is not in those fixed graphs.
"""

import asyncio

from sqlalchemy import event
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import TextClause

from app.preview.write_fence import _MARKER
from app.research import planner_v2
from app.research.contracts import canonical_bytes, parse_document
from app.research.egress import failure

_ADVISORY = "SELECT pg_advisory_xact_lock(:key)"
_CLOCK = frozenset(
    {
        "SELECT floor(extract(epoch from clock_timestamp()) * 1000)::bigint",
        "SELECT CAST((julianday('now') - 2440587.5)*86400000 AS INTEGER)",
    }
)


def _read_only(statement):
    if isinstance(statement, TextClause):
        return statement.text == _ADVISORY
    return bool(getattr(statement, "is_select", False)) and all(
        not getattr(node, "is_dml", False) and not isinstance(node, TextClause)
        for node in visitors.iterate(statement)
    )


class _Preparation:
    """Task/session/root/fence/actor/run/claim bound; consumed exactly once."""

    def __init__(self, db, owner, run, token, connection):
        self._db, self._owner, self.token = db, owner, token
        self._task = asyncio.current_task()
        self._root = db.sync_session.get_transaction()
        self._connection = connection.sync_connection
        self._connection_root = self._connection.get_transaction()
        self._run_id, self._input_hash = run.id, run.input_hash
        self._snapshot = canonical_bytes(planner_v2.captured(run))
        self._invalid = False
        self._consumed = False
        self._closed = False
        self._listeners = []
        self.check(db, owner, run)

    @classmethod
    async def open(cls, db, owner, run, token):
        proof = cls(db, owner, run, token, await db.connection())
        try:
            proof._listen(db.sync_session, "do_orm_execute", proof._statement)
            proof._listen(db.sync_session, "before_flush", proof._invalidate)
            proof._listen(db.sync_session, "after_transaction_create", proof._invalidate)
            proof._listen(db.sync_session, "after_transaction_end", proof._invalidate)
            proof._listen(proof._connection, "before_cursor_execute", proof._cursor)
            for name in (
                "commit",
                "rollback",
                "savepoint",
                "rollback_savepoint",
                "release_savepoint",
            ):
                proof._listen(proof._connection, name, proof._invalidate)
        except BaseException:
            proof.close()
            raise
        return proof

    def _listen(self, target, name, listener):
        event.listen(target, name, listener)
        self._listeners.append((target, name, listener))

    def _invalidate(self, *_args):
        self._invalid = True

    def _statement(self, state):
        if not _read_only(state.statement):
            self._invalid = True

    def _cursor(self, _connection, _cursor, statement, _parameters, context, _many):
        compiled = context.compiled
        if not (_read_only(compiled.statement) if compiled is not None else statement in _CLOCK):
            self._invalid = True

    def check(self, db, owner, run):
        marker = db.info.get(_MARKER)
        if (
            self._invalid
            or self._closed
            or db is not self._db
            or owner != self._owner
            or owner != self.token.owner_id
            or run.id != self._run_id
            or run.id != self.token.run_id
            or run.input_hash != self._input_hash
            or asyncio.current_task() is not self._task
            or self._task is None
            or self._root is None
            or not self._root.is_active
            or db.sync_session.get_transaction() is not self._root
            or db.sync_session.get_nested_transaction() is not None
            or marker != (self._root, owner)
            or self._connection.closed
            or self._connection.get_transaction() is not self._connection_root
            or self._connection.get_nested_transaction() is not None
            or db.new
            or db.dirty
            or db.deleted
        ):
            raise failure("MODEL_BINDING_CHANGED")
        snapshot = planner_v2.captured(run)
        if (
            snapshot is None
            or snapshot["version"]["owner_id"] != str(owner)
            or canonical_bytes(snapshot) != self._snapshot
        ):
            raise failure("MODEL_BINDING_CHANGED")

    def consume(self, db, owner, run):
        self.check(db, owner, run)
        if self._consumed:
            raise failure("MODEL_BINDING_CHANGED")
        self._consumed = True
        return parse_document(self._snapshot.decode("utf-8"))

    async def finish(self, db, owner):
        from app.models.user import User
        from app.research import planning_lease

        if not self._consumed:
            raise failure("MODEL_BINDING_CHANGED")
        # Real database time/claim/producer and actor are checked after prepare.
        # Only the already-proven source portion is omitted, under this fence.
        current, _, _, _ = await planning_lease.validate(
            db, self.token, running=False, current_source=False
        )
        actor = await db.get(User, owner, populate_existing=True)
        if (
            actor is None
            or not actor.is_active
            or current.state not in {"running", "waiting_approval"}
        ):
            raise failure("MODEL_CANCELLED")
        self.check(db, owner, current)

    def close(self):
        self._closed = True
        for target, name, listener in reversed(self._listeners):
            event.remove(target, name, listener)
        self._listeners.clear()

    def __reduce__(self):
        raise TypeError("A private preparation cannot be serialized.")
