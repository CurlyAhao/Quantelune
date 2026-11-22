"""Restricted preview API; credentials never use automatic validation error echoes."""

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.models.preview import PreviewTurn
from app.models.user import User
from app.preview.configuration import get_config, public_config, save_config
from app.preview.context import prepare_context
from app.preview.recovery import read_saved
from app.preview.service import run_turn
from app.schemas.preview import PreviewScope, SaveProviderConfig, TurnRequest
from app.services.workflow_service import check_project_access

router = APIRouter(prefix="/api/v1/preview", tags=["restricted-preview"])


async def safe_body[Schema: BaseModel](
    request: Request, schema: type[Schema], *, max_bytes: int = 32000
) -> Schema:
    try:
        raw = await request.body()
        if len(raw) > max_bytes:
            raise ValueError()
        return schema.model_validate_json(raw)
    except Exception:
        raise StatForgeError(422, "PREVIEW_INPUT_INVALID", "Invalid preview input.") from None


@router.get("/configuration")
async def configuration(
    db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)
) -> dict:
    return public_config(await get_config(db, user.id))


@router.patch("/configuration")
async def configure(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        # No key can appear in FastAPI's default 422 input echo.
        raw = await request.body()
        if len(raw) > 16000:
            raise ValueError()
        body = SaveProviderConfig.model_validate_json(raw)
    except Exception:
        raise StatForgeError(
            422, "PREVIEW_CONFIG_INVALID", "Invalid secure configuration."
        ) from None
    return await save_config(db, user.id, body)


@router.post("/context")
async def context(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    body = await safe_body(request, PreviewScope)
    return await prepare_context(db, body, user)


@router.post("/turns")
async def turn(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    body = await safe_body(request, TurnRequest)
    return await run_turn(db, user, body)


@router.get("/turns/{turn_id}")
async def saved_turn(
    turn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    return await read_saved(db, turn_id, user)


@router.get("/turns")
async def saved_turns(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    await check_project_access(db, project_id, user)
    with db.no_autoflush:
        rows = (
            await db.execute(
                select(PreviewTurn.id, PreviewTurn.status, PreviewTurn.created_at)
                .where(PreviewTurn.user_id == user.id, PreviewTurn.project_id == project_id)
                .order_by(PreviewTurn.created_at.desc(), PreviewTurn.id)
                .limit(20)
            )
        ).all()
    return {
        "turns": [
            {"turn_id": str(r.id), "status": r.status, "created_at": r.created_at.isoformat()}
            for r in rows
        ]
    }
