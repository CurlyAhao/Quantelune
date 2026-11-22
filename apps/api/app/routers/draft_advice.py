import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.models.user import User
from app.routers.preview import safe_body
from app.schemas.draft_advice import AdviceAccept, AdviceCreate, AdviceSend
from app.services import draft_advice_service as service
from app.services.research_gateway_service import Gateway, get_gateway

router = APIRouter(prefix="/api/v1/projects/{project_id}/advice", tags=["draft-advice"])


@router.get("")
async def recent(
    project_id: uuid.UUID,
    kind: Literal["plot", "coding"],
    draft_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.recent(db, user, project_id, kind, draft_id)


@router.post("")
async def create(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    try:
        return await service.create(
            db, user, project_id, await safe_body(request, AdviceCreate, max_bytes=32000), gateway
        )
    except ValueError:
        raise StatForgeError(422, "ADVICE_INPUT_INVALID", "请检查问题及所选内容后重试。") from None


@router.get("/{identity}")
async def read(
    project_id: uuid.UUID,
    identity: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    return await service.read(db, user, project_id, identity, gateway)


@router.post("/{identity}/send")
async def send(
    project_id: uuid.UUID,
    identity: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    return await service.send(
        db, user, project_id, identity, await safe_body(request, AdviceSend), gateway
    )


@router.post("/{identity}/cancel")
async def cancel(
    project_id: uuid.UUID,
    identity: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    return await service.cancel(db, user, project_id, identity, gateway)


@router.post("/{identity}/accept")
async def accept(
    project_id: uuid.UUID,
    identity: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    try:
        return await service.accept(
            db,
            user,
            project_id,
            identity,
            await safe_body(request, AdviceAccept, max_bytes=96000),
            gateway,
        )
    except ValueError:
        raise StatForgeError(
            422, "ADVICE_INPUT_INVALID", "请检查建议中的代码、主题及选段后重试。"
        ) from None
