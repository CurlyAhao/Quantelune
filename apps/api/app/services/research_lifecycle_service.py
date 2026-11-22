"""Explicit owner lifecycle operations. Nothing here cancels or completes a Run."""

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, ProjectStatus
from app.models.research import ResearchRun, ResearchSession, ResearchWorkspace
from app.models.research_security import ResearchCharge
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import TERMINAL
from app.services import research_service as ledger


async def workspaces(db: AsyncSession, owner: uuid.UUID) -> list[ResearchWorkspace]:
    rows = (
        await db.scalars(
            select(ResearchWorkspace)
            .where(ResearchWorkspace.owner_id == owner, ResearchWorkspace.is_deleted.is_(False))
            .order_by(ResearchWorkspace.updated_at.desc(), ResearchWorkspace.id)
            .limit(100)
        )
    ).all()
    # Deleted/reassigned project ACL must also hide its workspace.
    result = []
    for row in rows:
        if row.project_id is None:
            result.append(row)
        else:
            valid = await db.scalar(
                select(Project.id).where(
                    Project.id == row.project_id,
                    Project.owner_id == owner,
                    Project.is_deleted.is_(False),
                )
            )
            if valid is not None:
                result.append(row)
    return result


async def sessions(
    db: AsyncSession, owner: uuid.UUID, workspace_id: uuid.UUID, *, include_archived: bool = False
) -> list[ResearchSession]:
    await ledger.workspace(db, workspace_id, owner)
    query = select(ResearchSession).where(
        ResearchSession.workspace_id == workspace_id, ResearchSession.is_deleted.is_(False)
    )
    if not include_archived:
        query = query.where(ResearchSession.is_archived.is_(False))
    return list(
        (
            await db.scalars(
                query.order_by(ResearchSession.updated_at.desc(), ResearchSession.id).limit(100)
            )
        ).all()
    )


def _revision(actual: int, expected: int) -> None:
    if type(expected) is not int or expected < 0 or actual != expected:
        raise ledger.error("STALE_REVISION")


async def change_session(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    expected_revision: int,
    action: Literal["archive", "restore", "delete"],
) -> ResearchSession:
    await acquire_write_fence(db, owner)
    conversation, scope = await ledger.session(db, session_id, owner)
    _revision(conversation.revision, expected_revision)
    if action not in {"archive", "restore", "delete"}:
        raise ledger.error("INVALID_LIFECYCLE", 422)
    if conversation.is_deleted:
        raise ledger.error("SESSION_INACTIVE")
    if action == "restore":
        await ledger.workspace(db, scope.id, owner, mode="live")
    archived = action != "restore"
    deleted = action == "delete"
    if conversation.is_archived == archived and not deleted:
        return conversation
    conversation.is_archived = archived
    conversation.is_deleted = deleted
    conversation.revision += 1
    conversation.lifecycle_epoch += 1
    await ledger._append(
        db,
        scope,
        owner,
        "session",
        conversation.id,
        "session." + action,
        {
            "revision": conversation.revision,
            "lifecycle_epoch": conversation.lifecycle_epoch,
            "is_archived": archived,
            "is_deleted": deleted,
        },
    )
    return conversation


async def save_workspace(
    db: AsyncSession,
    owner: uuid.UUID,
    workspace_id: uuid.UUID,
    expected_revision: int,
    project_id: uuid.UUID,
) -> ResearchWorkspace:
    await acquire_write_fence(db, owner)
    scope = await ledger.workspace(db, workspace_id, owner, mode="live")
    _revision(scope.revision, expected_revision)
    await ledger._project(db, project_id, owner)
    project = await db.get(Project, project_id, populate_existing=True)
    if project is None:
        raise ledger.error("ACCESS_DENIED", 404)
    if project.status in {ProjectStatus.FROZEN, ProjectStatus.ARCHIVED}:
        raise ledger.error("PROJECT_READ_ONLY")
    if scope.project_id == project_id:
        return scope
    if scope.project_id is not None:
        raise ledger.error("WORKSPACE_ALREADY_SAVED")
    session_ids = select(ResearchSession.id).where(ResearchSession.workspace_id == scope.id)
    run_ids = select(ResearchRun.id).where(ResearchRun.session_id.in_(session_ids))
    active = await db.scalar(
        select(ResearchRun.id)
        .where(
            ResearchRun.id.in_(run_ids),
            ResearchRun.state.not_in([state.value for state in TERMINAL]),
        )
        .limit(1)
    )
    unsettled = await db.scalar(
        select(ResearchCharge.id)
        .where(
            ResearchCharge.run_id.in_(run_ids),
            ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
        )
        .limit(1)
    )
    if active is not None or unsettled is not None:
        raise ledger.error("WORKSPACE_SAVE_PENDING_WORK")
    # Associations are explicit; old input snapshots and source projects remain
    # unchanged. No copy, join, new Run, Artifact rewrite or budget release.
    scope.project_id = project_id
    scope.revision += 1
    scope.lifecycle_epoch += 1
    await ledger._append(
        db,
        scope,
        owner,
        "workspace",
        scope.id,
        "workspace.saved",
        {
            "project_id": str(project_id),
            "revision": scope.revision,
            "lifecycle_epoch": scope.lifecycle_epoch,
        },
    )
    return scope


async def delete_workspace(
    db: AsyncSession,
    owner: uuid.UUID,
    workspace_id: uuid.UUID,
    expected_revision: int,
) -> ResearchWorkspace:
    await acquire_write_fence(db, owner)
    scope = await ledger.workspace(db, workspace_id, owner, mode="live")
    _revision(scope.revision, expected_revision)
    scope.is_deleted = True
    scope.revision += 1
    scope.lifecycle_epoch += 1
    await ledger._append(
        db,
        scope,
        owner,
        "workspace",
        scope.id,
        "workspace.deleted",
        {
            "revision": scope.revision,
            "lifecycle_epoch": scope.lifecycle_epoch,
        },
    )
    return scope
