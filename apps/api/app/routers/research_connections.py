"""Saved model connections, sharing the existing owner/revision/credential journal."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.routers.research_security import SafeSecurityRoute
from app.schemas.research_security import ProviderConnectionTest, ProviderDelete
from app.services import research_connections as connections
from app.services.research_gateway_service import Gateway, get_gateway

router = APIRouter(
    prefix="/api/v1/research/security/provider/connections",
    tags=["model-connections"],
    route_class=SafeSecurityRoute,
)


@router.post("/test")
async def test_connection(
    body: ProviderConnectionTest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    from app.exceptions import StatForgeError
    from app.research.contracts import parse_document
    from app.services import research_provider_service as providers
    from app.services.provider_connection_test import probe

    secret = body.key.get_secret_value() if body.key else None
    if secret is None:
        if body.configuration_id is None:
            raise StatForgeError(
                422, "CONNECTION_TEST_KEY_REQUIRED", "请填写 API Key 或选择已保存的连接。"
            )
        _, row = await connections.selected(
            db, user.id, uuid.UUID(body.configuration_id), body.expected_revision
        )
        settings = parse_document(row.document_json)["settings"]
        if row.key_state != "configured" or settings.get("provider") != body.provider:
            raise StatForgeError(422, "CONNECTION_TEST_KEY_REQUIRED", "请填写当前服务的 API Key。")
        secret = providers._secret(runtime.vault, row, user.id)
    if not 8 <= len(secret) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in secret):
        raise StatForgeError(422, "CONNECTION_TEST_KEY_INVALID", "API Key 格式无效，请重新复制。")
    return await probe(body.provider, body.model.strip(), secret, body.timeout_ms)


@router.get("")
async def listing(
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await connections.listing(db, user.id, offset, limit)


@router.post("/{configuration_id}/activate")
async def activate(
    configuration_id: uuid.UUID,
    body: ProviderDelete,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await connections.activate(db, user.id, configuration_id, body.expected_revision)


@router.post("/{configuration_id}/inspect")
async def inspect(
    configuration_id: uuid.UUID,
    body: ProviderDelete,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await connections.inspect(
        db, user.id, configuration_id, body.expected_revision, runtime.vault
    )


@router.delete("/{configuration_id}")
async def remove(
    configuration_id: uuid.UUID,
    body: ProviderDelete,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    runtime: Gateway = Depends(get_gateway),
):
    return await connections.remove(
        db, user.id, configuration_id, body.expected_revision, runtime.vault
    )
