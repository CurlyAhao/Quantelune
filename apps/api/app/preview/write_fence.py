"""Shared actor write boundary, held until the caller's transaction commits.

SQLite needs a real RESERVED database lock, not ignored FOR UPDATE syntax.
An in-process FIFO only schedules contenders; the database lock and actor
check remain the authority across processes. No auxiliary DML or rollback.
"""

import asyncio
import uuid
from weakref import WeakValueDictionary

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction
from sqlalchemy.sql.elements import TextClause

from app.exceptions import StatForgeError
from app.models.user import User

_MARKER = "statforge_preview_write_fence"
_DML = "statforge_preview_unfenced_dml"
_LOCAL_GATE = "statforge_preview_writer_queue"
_QUEUES: WeakValueDictionary[tuple[object, object], asyncio.Lock] = WeakValueDictionary()


async def _queue_sqlite_writer(db: AsyncSession) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        return
    transaction = db.sync_session.get_transaction()
    if transaction is None:
        transaction = db.sync_session.begin()  # Logical only; no database privilege.
    if transaction.parent is not None or not transaction.is_active:
        raise conflict()
    marker = db.info.get(_LOCAL_GATE)
    if marker is not None:
        if marker[0] is not transaction:
            raise conflict()
        return
    engine = getattr(bind, "engine", bind)
    key = (engine, asyncio.get_running_loop())
    queue = _QUEUES.get(key)
    if queue is None:
        queue = asyncio.Lock()
        _QUEUES[key] = queue
    await queue.acquire()
    # No await between admission and attaching the ticket to its root lifetime.
    if db.sync_session.get_transaction() is not transaction or not transaction.is_active:
        queue.release()
        raise conflict()
    db.info[_LOCAL_GATE] = (transaction, queue)


@event.listens_for(Session, "after_transaction_end")
def release_writer_ticket(session: Session, transaction: SessionTransaction) -> None:
    marker = session.info.get(_LOCAL_GATE)
    if marker is not None and marker[0] is transaction and transaction.parent is None:
        session.info.pop(_LOCAL_GATE)
        marker[1].release()


def remember_unfenced_write(session: Session) -> None:
    transaction = session.get_transaction()
    if transaction is None:
        transaction = session.begin()
    marker = session.info.get(_MARKER)
    if marker is None or marker[0] is not transaction:
        session.info[_DML] = transaction


@event.listens_for(Session, "do_orm_execute")
def remember_statement(state: ORMExecuteState) -> None:
    statement = state.statement
    if isinstance(statement, TextClause):
        # Fixed harmless connectivity probes are the sole textual read exception.
        # Unknown TextClause is potentially writing, even if it begins SELECT.
        if statement.text.strip() in {"SELECT 1", "select 1"}:
            return
        remember_unfenced_write(state.session)
    elif not state.is_select:
        remember_unfenced_write(state.session)


@event.listens_for(Session, "after_flush")
def remember_flush(session: Session, _context: object) -> None:
    remember_unfenced_write(session)


def conflict() -> StatForgeError:
    return StatForgeError(409, "PREVIEW_WRITE_CONFLICT", "Local write requires renewed review.")


async def acquire_write_fence(db: AsyncSession, user_id: uuid.UUID) -> None:
    current = db.sync_session.get_transaction()
    marker = db.info.get(_MARKER)
    if marker is not None and marker[0] is current and current is not None:
        if marker[1] != user_id:
            raise conflict()
        return
    db.info.pop(_MARKER, None)
    if current is not None and db.info.get(_DML) is current:
        raise conflict()
    db.info.pop(_DML, None)
    if db.new or db.dirty or db.deleted:
        raise conflict()
    try:
        await _queue_sqlite_writer(db)
        connection = await db.connection()
        transaction = db.sync_session.get_transaction()
        if transaction is None:
            raise conflict()
        if connection.dialect.name == "sqlite":
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            # aiosqlite.Connection.in_transaction is the public driver state;
            # SQLAlchemy's read-only logical autobegin alone is insufficient.
            if driver is None or driver.in_transaction:
                raise conflict()
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            with db.no_autoflush:
                owner = await db.scalar(select(User.id).where(User.id == user_id))
        elif connection.dialect.name == "postgresql":
            with db.no_autoflush:
                owner = await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
        else:
            raise conflict()
        if owner is None:
            raise conflict()
        db.info[_MARKER] = (transaction, user_id)
    except StatForgeError:
        raise
    except Exception:
        # The caller owns rollback/commit; do not discard a caller transaction
        # in order to acquire a lock or expose driver diagnostics/paths.
        raise conflict() from None
