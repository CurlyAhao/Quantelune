"""Authenticated project conversations and their observed background turns."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.project_assistant import AssistantTurn
from app.services import material_guidance as guidance
from app.services import project_assistant as assistant

router = APIRouter(prefix="/api/v1/projects/{project_id}/assistant", tags=["project-assistant"])


class SuggestionPreference(BaseModel):
    enabled: bool


@router.get("/suggestions/preference")
async def preference(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await assistant.access(db, project_id, user)
    return {"enabled": guidance.enabled(project_id, user.id)}


@router.put("/suggestions/preference")
async def set_preference(
    project_id: uuid.UUID,
    body: SuggestionPreference,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await assistant.access(db, project_id, user)
    guidance.write_json(guidance.pref_path(project_id, user.id), body.model_dump())
    return body


@router.post("/suggestions")
async def suggestions(
    project_id: uuid.UUID,
    body: guidance.AdviceInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await guidance.suggest(db, project_id, user, body, request)


@router.post("/figures/{figure_id}/style-preview")
async def style_preview(
    project_id: uuid.UUID,
    figure_id: uuid.UUID,
    body: guidance.StyleInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await guidance.figure_style(db, project_id, user, figure_id, body, request)


@router.get("/material-result")
async def material_result(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    context_key: str = Query(default="project", max_length=200),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await assistant.access(db, project_id, user)
    from app.services import dataset_service

    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    if dataset.project_id != project_id:
        raise assistant.failure("请重新选择当前项目中的材料。", 404)
    return {
        "item": await assistant.latest_material_result(
            db, user.id, project_id, dataset, context_key
        )
    }


@router.get("/conversations")
async def conversations(
    project_id: uuid.UUID,
    context_key: str = Query(default="project", max_length=200),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await assistant.access(db, project_id, user)
    return {"items": assistant.list_conversations(user.id, project_id, context_key)}


@router.get("/conversations/{conversation_id}")
async def conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await assistant.access(db, project_id, user)
    return assistant.read(user.id, project_id, conversation_id)


@router.post("/turns")
async def turn(
    project_id: uuid.UUID,
    body: AssistantTurn,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await assistant.start(db, project_id, user, body)


@router.post("/conversations/{conversation_id}/stop")
async def stop(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await assistant.access(db, project_id, user)
    return assistant.stop(user.id, project_id, conversation_id)
