"""Audit logging: record critical actions without ever breaking the main flow.

Audit failures are logged and swallowed — auditing must never cause a business
operation to fail. No secrets (passwords, tokens, cookies, file contents) are
ever written.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent
from app.models.user import User

logger = logging.getLogger("statforge.audit")


def _safe_json(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return None


async def record(
    db: AsyncSession,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: User | None = None,
    actor_email: str | None = None,
    project_id: uuid.UUID | None = None,
    before: Any = None,
    after: Any = None,
) -> None:
    """Persist an audit event. Swallows and logs any error."""
    try:
        event = AuditEvent(
            project_id=project_id,
            actor_id=actor.id if actor else None,
            actor_email=actor_email or (actor.email if actor else None),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=str(entity_id),
            before_json=_safe_json(before),
            after_json=_safe_json(after),
        )
        db.add(event)
        await db.flush()
    except Exception:  # noqa: BLE001 - auditing must not break the caller
        logger.exception("Failed to record audit event %s/%s", event_type, entity_type)
