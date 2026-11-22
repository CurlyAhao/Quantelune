"""FastAPI dependencies: current user extraction from JWT cookie."""

import os
import uuid

import jwt
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.desktop import ensure_desktop_user
from app.exceptions import TokenExpiredError, TokenInvalidError
from app.models.user import User
from app.services.identity_service import find_user
from app.services.token_service import decode_access_token


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Extract and validate the current user from the access token cookie.

    Raises TokenInvalidError / TokenExpiredError on failure.
    """
    if settings.desktop_mode:
        expected = os.environ.get("SIDECAR_SESSION_TOKEN", "")
        provided = request.headers.get("X-Session-Token", "")
        if expected and provided == expected:
            return await ensure_desktop_user(db)

    token = request.cookies.get(settings.access_cookie_name)
    if not token:
        # Also check Authorization header as fallback (for API clients)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise TokenInvalidError()

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError()
    except jwt.InvalidTokenError:
        raise TokenInvalidError()

    user_id = payload.get("sub")
    if not user_id:
        raise TokenInvalidError()

    user = await find_user(db, user_id=uuid.UUID(user_id))
    if user is None or not user.is_active:
        raise TokenInvalidError()

    return user


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User | None:
    """Like get_current_user but returns None instead of raising."""
    try:
        return await get_current_user(request, db)
    except (TokenInvalidError, TokenExpiredError):
        return None
