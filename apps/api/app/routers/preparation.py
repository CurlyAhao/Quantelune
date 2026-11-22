"""Preview preparation before creating an immutable analysis-ready dataset."""

import json
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.dataset import DatasetResponse
from app.schemas.preparation import Preparation
from app.services import dataset_service, preparation, preparation_drafts

router = APIRouter(prefix="/api/v1/datasets", tags=["preparation"])


@router.get("/{dataset_id}/preparation/draft")
async def read_draft(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await preparation_drafts.read(db, dataset_id, user)


@router.put("/{dataset_id}/preparation/draft")
async def save_draft(
    dataset_id: uuid.UUID,
    body: preparation_drafts.DraftWrite,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await preparation_drafts.save(db, dataset_id, user, body)


@router.get("/{dataset_id}/preparation")
async def metadata(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return json.loads(dataset.preview_json or "{}").get("preparation")


@router.post("/{dataset_id}/preparation/preview")
async def preview(
    dataset_id: uuid.UUID,
    body: Preparation,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await preparation.prepare(db, user.id, dataset_id, body)


@router.post("/{dataset_id}/preparation/apply", response_model=DatasetResponse)
async def apply(
    dataset_id: uuid.UUID,
    body: Preparation,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    saved = await preparation.prepare(db, user.id, dataset_id, body, apply=True)
    response = DatasetResponse.model_validate(saved)
    # The desktop opens the new preview as soon as this response arrives.
    # Commit before publishing its ID, not during dependency cleanup afterwards.
    await db.commit()
    return response
