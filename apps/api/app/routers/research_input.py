"""Begin a project discussion once, from a saved local input. No provider call."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.research import ResearchWorkspace
from app.models.user import User
from app.preview.workspace import load_record, read_record, rejected, save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.research import SessionResponse, WorkspaceResponse
from app.schemas.workspace import ResearchInputState, SaveRecord
from app.services import research_context_service as contexts
from app.services import research_service as ledger

router = APIRouter(prefix="/api/v1/research/inputs", tags=["research"])


class BeginInput(BaseModel):
    expected_revision: int = Field(ge=1)


@router.post("/{record_id}/begin")
async def begin(
    record_id: uuid.UUID,
    body: BeginInput,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await acquire_write_fence(db, user.id)
    row = await load_record(db, record_id, user)
    if row.kind != "research_input":
        raise rejected("WORKSPACE_NOT_FOUND", 404)
    payload = ResearchInputState.model_validate_json(row.payload_json)
    if body.expected_revision > row.revision:
        raise rejected("WORKSPACE_REVISION_CONFLICT")
    if payload.session_id:
        # A lost response can be read again without making another discussion.
        conversation, scope = await ledger.session(db, payload.session_id, user.id, mode="live")
    else:
        if row.revision != body.expected_revision:
            raise rejected("WORKSPACE_REVISION_CONFLICT")
        if payload.workspace_id:
            scope = await ledger.workspace(db, payload.workspace_id, user.id, mode="live")
        else:
            scope = await db.scalar(
                select(ResearchWorkspace)
                .where(
                    ResearchWorkspace.owner_id == user.id,
                    ResearchWorkspace.project_id == row.project_id,
                    ResearchWorkspace.is_deleted.is_(False),
                )
                .order_by(ResearchWorkspace.updated_at.desc())
                .limit(1)
            )
        if scope is None:
            scope = await ledger.create_workspace(db, user.id, "项目研究", row.project_id)
        if scope.project_id != row.project_id:
            raise rejected()
        conversation = await ledger.create_session(
            db, user.id, scope.id, payload.question.strip()[:80] or "新的讨论"
        )
        if payload.dataset_id:
            await ledger.attach(db, user.id, conversation.id, "dataset_version", payload.dataset_id)
        context = await contexts.snapshot(db, user.id, conversation.id)
        columns = dict(payload.columns)
        for dataset in context.datasets:
            columns.setdefault(
                dataset.dataset_id,
                [field.reference.name for field in dataset.fields]
                if len(dataset.fields) <= 10
                else [],
            )
        payload = payload.model_copy(
            update={
                "workspace_id": scope.id,
                "session_id": conversation.id,
                "columns": columns,
            }
        )
        await save_record(
            db,
            user,
            SaveRecord(project_id=row.project_id, expected_revision=row.revision, payload=payload),
            row.id,
            commit=False,
        )
    return {
        "input": await read_record(db, row.id, user),
        "workspace": WorkspaceResponse.model_validate(scope).model_dump(mode="json"),
        "session": SessionResponse.model_validate(conversation).model_dump(mode="json"),
    }
