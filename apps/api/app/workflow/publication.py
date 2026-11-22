"""Ownership and compensation of new workflow objects, without early commits."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.ml.worker_control import MLTaskScope
from app.services import ml_service

logger = logging.getLogger("statforge.workflow")
NAMESPACE = "_statforge_workflow_objects"


def pending_object(db: AsyncSession, key: str, delete: Callable[[str], None]) -> None:
    """Register only a freshly allocated object; reused datasets are never owned."""
    session = db.sync_session
    if NAMESPACE not in session.info:
        session.info[NAMESPACE] = []

        def current(s):
            return s.get_nested_transaction() or s.get_transaction()

        def committed(s):
            transaction = current(s)
            pending = s.info.get(NAMESPACE, [])
            if transaction is not None and transaction.parent is not None:
                s.info[NAMESPACE] = [
                    (transaction.parent if owner is transaction else owner, key, delete)
                    for owner, key, delete in pending
                ]
            else:
                uncertain = set(s.info.get(NAMESPACE + "_uncertain", []))
                uncertain.difference_update(key for _, key, _ in pending)
                s.info[NAMESPACE + "_uncertain"] = sorted(uncertain)
                s.info[NAMESPACE] = []

        def ended(s, previous):
            if previous.parent is not None and not previous.nested:
                return
            keep = []
            cleanup = dict(s.info.pop(NAMESPACE + "_failed", {}))
            uncertain = set(s.info.get(NAMESPACE + "_uncertain", []))
            for owner, key, delete in s.info.get(NAMESPACE, []):
                cursor = owner
                while cursor is not None and cursor is not previous:
                    cursor = cursor.parent
                if cursor is None:
                    keep.append((owner, key, delete))
                elif key in uncertain:
                    logger.error("WORKFLOW_PUBLICATION_UNKNOWN object_key=%s", key)
                else:
                    cleanup[key] = delete
            s.info[NAMESPACE] = keep
            failed = {}
            for key, delete in cleanup.items():
                try:
                    delete(key)
                except Exception:
                    failed[key] = delete
                    logger.error("WORKFLOW_OBJECT_CLEANUP_FAILED object_key=%s", key)
            s.info[NAMESPACE + "_failed"] = failed

        event.listen(session, "after_commit", committed)
        event.listen(session, "after_transaction_end", ended)
    owner = session.get_nested_transaction() or session.get_transaction()
    if owner is None:
        raise RuntimeError("Workflow storage object has no owning transaction.")
    session.info[NAMESPACE].append((owner, key, delete))


async def publish_workflow_transaction(db: AsyncSession, task: MLTaskScope) -> None:
    try:
        # Reuse the real after_commit witness and remaining absolute deadline;
        # this also covers the MLRun objects staged in the caller transaction.
        await ml_service.publish_transaction(db, task, additional_owned_namespaces=(NAMESPACE,))
    except StatForgeError as exc:
        if exc.error_code == "ML_PUBLICATION_UNKNOWN":
            raise StatForgeError(
                503,
                "WORKFLOW_PUBLICATION_UNKNOWN",
                "Publication outcome is unknown; reopen run history before retrying.",
            ) from exc
        raise


async def rollback_preserving_error(db: AsyncSession) -> None:
    try:
        await asyncio.shield(db.rollback())
    except BaseException as exc:
        logger.error("WORKFLOW_ROLLBACK_CLEANUP_FAILED type=%s", type(exc).__name__)
