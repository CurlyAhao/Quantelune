"""Bounded immutable local Artifact bytes and transaction-owned compensation."""

import hashlib
import os
import uuid
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.config import settings
from app.exceptions import StatForgeError
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import MAX_DOCUMENT_BYTES
from app.services import storage_service

_PENDING = "statforge_research_fresh_content"
_REGISTERED = "statforge_research_content_hooks"


def _compensate(session: Session) -> None:
    pending: dict[str, Path] = session.info.get(_PENDING, {})
    failed = []
    for key, path in list(pending.items()):
        try:
            # Only fresh files recorded before creation, never a reused source.
            current = storage_service.get_local_object_path(key)
            if current != path:
                raise OSError("Controlled storage location changed.")
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()  # Empty, freshly allocated Artifact directory only.
            except OSError:
                pass
            pending.pop(key)
        except (OSError, ValueError):
            failed.append(key)
    if failed:
        # Preserve unresolved ownership for A08 recovery; do not report clean rollback.
        session.info["statforge_research_cleanup_failed"] = failed
        raise StatForgeError(500, "RESEARCH_CONTENT_CLEANUP_FAILED", "Artifact cleanup incomplete.")


def _register(db: AsyncSession) -> None:
    session = db.sync_session
    if session.info.get(_REGISTERED):
        return

    def committed(current: Session) -> None:
        if not current.in_nested_transaction():
            current.info[_PENDING] = {}

    def rolled_back(current: Session) -> None:
        if not current.in_nested_transaction():
            _compensate(current)

    def ended(current: Session, transaction: SessionTransaction) -> None:
        if transaction.parent is None:
            _compensate(current)  # Includes Session.close without explicit rollback.

    event.listen(session, "after_commit", committed)
    event.listen(session, "after_rollback", rolled_back)
    event.listen(session, "after_transaction_end", ended)
    session.info[_REGISTERED] = True
    session.info[_PENDING] = {}


async def publish_json(
    db: AsyncSession,
    owner_id: uuid.UUID,
    artifact_id: uuid.UUID,
    content: bytes,
) -> tuple[str, str]:
    """Server-generated identity only; content becomes readable after ledger commit.

    Exclusive create prevents overwriting. Bytes are complete and fsynced before
    Artifact metadata can commit. A08 owns process-crash orphan reconciliation.
    """
    if (
        settings.storage_backend != "local"
        or db.in_nested_transaction()
        or not db.in_transaction()
        or not 0 < len(content) <= MAX_DOCUMENT_BYTES
    ):
        raise StatForgeError(409, "RESEARCH_CONTENT_CONTRACT", "Artifact publish unavailable.")
    await acquire_write_fence(db, owner_id)
    digest = hashlib.sha256(content).hexdigest()
    key = f"research-artifacts/{owner_id}/{artifact_id}/{digest}.json"
    path = storage_service.get_local_object_path(key)
    _register(db)
    pending: dict[str, Path] = db.sync_session.info[_PENDING]
    path.parent.mkdir(parents=True, exist_ok=False)
    # Register ownership only after exclusive allocation of the new directory.
    # A collision can never make compensation delete someone else's file.
    pending[key] = path
    try:
        with path.open("xb") as target:
            written = target.write(content)
            if written != len(content):
                raise OSError("Incomplete Artifact write.")
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        _compensate(db.sync_session)
        raise
    return key, digest
