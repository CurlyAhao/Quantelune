"""Bounded, authorized deletion of Workflow output files after confirmed DB deletion.

Existing immutable audit events keep the original object plan and append progress.
This is a domain cleanup receipt, not a general job/lease or business runner.
"""

from __future__ import annotations

import json
import re
import uuid
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import noload

from app.exceptions import StatForgeError
from app.ml.worker_control import MLTaskScope
from app.models.audit import AuditEvent
from app.models.user import User
from app.models.workflow import Workflow
from app.workflow.publication import publish_workflow_transaction, rollback_preserving_error
from app.workflow.storage import delete_output

REQUESTED = "workflow.output_delete_requested"
PROGRESS = "workflow.output_delete_progress"
MAX_OBJECTS = 2048
MAX_SOURCES = 4096
MAX_HISTORY = 1024
MAX_PLAN_BYTES = 2 * 1024 * 1024


def validated_key(key: str, project_id: uuid.UUID, run_ids: set[str]) -> bool:
    match = re.fullmatch(
        r"workflow-artifacts/([0-9a-f-]{36})/([0-9a-f-]{36})/([0-9a-f]{32})/([a-z0-9_.-]{1,160})",
        key,
    )
    return bool(match and match[1] == str(project_id) and match[2] in run_ids and ".." not in key)


async def record_plan(
    db: AsyncSession,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    user: User,
    objects: list[dict[str, Any]],
    sources: list[str],
    name: str,
) -> AuditEvent:
    if len(objects) > MAX_OBJECTS or len(sources) > MAX_SOURCES:
        raise StatForgeError(422, "WORKFLOW_DELETE_LIMIT", "Too many outputs for one deletion.")
    encoded = json.dumps(
        {"version": 1, "objects": objects, "source_ids": sources},
        ensure_ascii=False,
        sort_keys=True,
    )
    if len(encoded.encode("utf-8")) > MAX_PLAN_BYTES:
        raise StatForgeError(422, "WORKFLOW_DELETE_LIMIT", "Deletion plan exceeds its byte limit.")
    receipt = AuditEvent(
        id=uuid.uuid4(),
        project_id=project_id,
        actor_id=user.id,
        actor_email=user.email,
        event_type=REQUESTED,
        entity_type="workflow",
        entity_id=str(workflow_id),
        before_json=json.dumps(
            {
                "name": name,
                "plan_sha256": sha256(encoded.encode()).hexdigest(),
                "run_ids": sorted({item["run_id"] for item in objects}),
            },
            ensure_ascii=False,
        ),
        after_json=encoded,
    )
    db.add(receipt)
    # Unlike optional ordinary logging, losing this deletion index must block
    # the DB deletion while the original Run references still exist.
    await db.flush()
    return receipt


async def authorized_plan(db: AsyncSession, receipt_id: uuid.UUID, user: User) -> AuditEvent:
    from app.services.workflow_service import check_project_access

    with db.no_autoflush:
        identity = (
            await db.execute(
                select(AuditEvent.project_id, AuditEvent.event_type).where(
                    AuditEvent.id == receipt_id
                )
            )
        ).one_or_none()
    if identity is None or identity.event_type != REQUESTED or identity.project_id is None:
        raise StatForgeError(404, "WORKFLOW_CLEANUP_NOT_FOUND", "Deletion receipt not found.")
    await check_project_access(db, identity.project_id, user)
    return (
        await db.scalars(
            select(AuditEvent)
            .options(noload("*"))
            .where(AuditEvent.id == receipt_id)
            .with_for_update()
        )
    ).one()


async def progress(db: AsyncSession, receipt: AuditEvent) -> dict:
    identity = json.loads(receipt.before_json or "{}")
    if sha256((receipt.after_json or "").encode()).hexdigest() != identity.get("plan_sha256"):
        raise StatForgeError(
            409, "WORKFLOW_CLEANUP_INVALID", "Deletion receipt changed after authorization."
        )
    plan = json.loads(receipt.after_json or "{}")
    if plan.get("version") != 1 or not isinstance(plan.get("objects"), list):
        raise StatForgeError(409, "WORKFLOW_CLEANUP_INVALID", "Deletion receipt is unverifiable.")
    rows = (
        await db.scalars(
            select(AuditEvent.after_json).where(
                AuditEvent.project_id == receipt.project_id,
                AuditEvent.event_type == PROGRESS,
                AuditEvent.entity_id == str(receipt.id),
            )
        )
    ).all()
    records = [json.loads(row) for row in rows if row]
    latest = max(records, key=lambda item: item.get("sequence", 0), default=None)
    return latest or {
        "sequence": 0,
        "remaining": [item["key"] for item in plan["objects"]],
        "deleted": 0,
    }


async def summary(db: AsyncSession, receipt: AuditEvent) -> dict:
    state = await progress(db, receipt)
    return {
        "receipt_id": str(receipt.id),
        "workflow_id": receipt.entity_id,
        "status": "cleanup_pending" if state["remaining"] else "completed",
        "pending_artifacts": len(state["remaining"]),
        "deleted_artifacts": state["deleted"],
    }


async def list_pending(
    db: AsyncSession, project_id: uuid.UUID, user: User, *, offset: int = 0
) -> dict:
    from app.services.workflow_service import check_project_access

    await check_project_access(db, project_id, user)
    statement = (
        select(AuditEvent)
        .options(noload("*"))
        .where(AuditEvent.project_id == project_id, AuditEvent.event_type == REQUESTED)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .execution_options(yield_per=50)
    )
    stream = await db.stream_scalars(statement)
    pending: list[dict] = []
    seen = 0
    next_offset = None
    try:
        async for receipt in stream:
            item = await summary(db, receipt)
            if item["status"] == "cleanup_pending":
                if seen < offset:
                    seen += 1
                    continue
                if len(pending) == 100:
                    next_offset = offset + len(pending)
                    break
                pending.append(item)
    finally:
        await stream.close()
    return {"receipts": pending, "next_offset": next_offset}


async def cleanup(
    db: AsyncSession,
    receipt_id: uuid.UUID,
    user: User,
    task: MLTaskScope,
) -> dict:
    from app.services.workflow_service import check_project_access

    receipt = await authorized_plan(db, receipt_id, user)
    # Independent visibility proves the plan was committed. A caller's own
    # uncommitted DELETE/session cache is not authority to erase storage.
    if not isinstance(db.bind, AsyncEngine):
        raise StatForgeError(
            409, "WORKFLOW_DELETE_UNCONFIRMED", "Deletion is not independently confirmed."
        )
    async with db.bind.connect() as connection:
        committed = await connection.scalar(
            select(AuditEvent.id).where(
                AuditEvent.id == receipt_id, AuditEvent.event_type == REQUESTED
            )
        )
        still_exists = await connection.scalar(
            select(Workflow.id).where(Workflow.id == uuid.UUID(receipt.entity_id))
        )
    if committed is None or still_exists is not None:
        raise StatForgeError(
            409, "WORKFLOW_DELETE_UNCONFIRMED", "Workflow deletion has not been confirmed."
        )
    receipt_identity = {"receipt_id": str(receipt.id), "workflow_id": receipt.entity_id}
    plan = json.loads(receipt.after_json or "{}")
    state = await progress(db, receipt)
    if not state["remaining"]:
        return await summary(db, receipt)
    objects = plan["objects"]
    run_ids = set(json.loads(receipt.before_json or "{}")["run_ids"])
    allowed = {item["key"] for item in objects}
    if (
        len(objects) > MAX_OBJECTS
        or not set(state["remaining"]) <= allowed
        or any(not validated_key(key, receipt.project_id, run_ids) for key in allowed)
    ):
        raise StatForgeError(
            409, "WORKFLOW_CLEANUP_INVALID", "Deletion keys do not match the owned output scope."
        )
    # The committed retirement plan already authorized historical inputs before
    # removing the Workflow. Erasing its exact owned output keys reads no source
    # data and must remain possible after source rows are removed or moved.
    # Current project ownership is still checked before every storage deletion.
    remaining = list(state["remaining"])
    failures = 0
    for key in list(remaining):
        try:
            await task.check()
            await check_project_access(db, receipt.project_id, user)
            # Local unlink is synchronous and finite, so cancellation cannot
            # abandon a delete thread. Remote delete has a strict two-second cap.
            delete_output(key)
            remaining.remove(key)
        except StatForgeError as exc:
            if exc.status_code in {403, 404}:
                raise
            failures += 1
            if failures >= 2:
                break
        except Exception:
            failures += 1
            if failures >= 2:
                break
    event = AuditEvent(
        project_id=receipt.project_id,
        actor_id=user.id,
        actor_email=user.email,
        event_type=PROGRESS,
        entity_type="workflow_output_deletion",
        entity_id=str(receipt.id),
        after_json=json.dumps(
            {
                "sequence": state["sequence"] + 1,
                "remaining": remaining,
                "deleted": len(objects) - len(remaining),
            }
        ),
    )
    db.add(event)
    try:
        await db.flush()
        await publish_workflow_transaction(db, task)
    except BaseException:
        # Storage deletions are idempotent. Retain the last committed plan;
        # another authorized request can reconcile even a lost progress ACK.
        await rollback_preserving_error(db)
        return {
            **receipt_identity,
            "status": "cleanup_pending",
            "pending_artifacts": len(state["remaining"]),
            "deleted_artifacts": state["deleted"],
        }
    return {
        "receipt_id": str(receipt.id),
        "workflow_id": receipt.entity_id,
        "status": "cleanup_pending" if remaining else "completed",
        "pending_artifacts": len(remaining),
        "deleted_artifacts": len(objects) - len(remaining),
    }
