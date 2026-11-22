"""Authentication routes: register, login, refresh, logout, me."""

import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user, get_optional_user
from app.exceptions import InvalidCredentialsError
from app.models.user import User
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from app.services import audit_service, auth_service

logger = logging.getLogger("statforge.auth")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
) -> None:
    """Set HttpOnly cookies for access and refresh tokens."""
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": settings.cookie_path,
    }
    if settings.cookie_domain:
        common["domain"] = settings.cookie_domain

    response.set_cookie(
        key=settings.access_cookie_name,
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        **common,
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    """Delete auth cookies."""
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": settings.cookie_path,
    }
    if settings.cookie_domain:
        common["domain"] = settings.cookie_domain
    response.delete_cookie(key=settings.access_cookie_name, **common)
    response.delete_cookie(key=settings.refresh_cookie_name, **common)


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    """Register a new user account."""
    user = await auth_service.register_user(
        db,
        payload.email,
        payload.password,
        payload.display_name,
    )
    access_token, refresh_token = await auth_service.create_session(
        db,
        user,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_auth_cookies(response, access_token, refresh_token)
    await audit_service.record(
        db,
        event_type="auth.register",
        entity_type="user",
        entity_id=user.id,
        actor=user,
    )
    return AuthResponse(user=UserResponse.model_validate(user), message="registered")


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    """Log in with email and password."""
    try:
        user = await auth_service.authenticate_user(db, payload.email, payload.password)
    except InvalidCredentialsError:
        await audit_service.record(
            db,
            event_type="auth.login_failed",
            entity_type="user",
            entity_id=payload.email,
            actor_email=payload.email,
        )
        raise
    access_token, refresh_token = await auth_service.create_session(
        db,
        user,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_auth_cookies(response, access_token, refresh_token)
    await audit_service.record(
        db,
        event_type="auth.login",
        entity_type="user",
        entity_id=user.id,
        actor=user,
    )
    return AuthResponse(user=UserResponse.model_validate(user), message="logged_in")


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    """Rotate the refresh token and issue a new access token."""
    old_refresh = request.cookies.get(settings.refresh_cookie_name)
    if not old_refresh:
        from app.exceptions import TokenInvalidError

        raise TokenInvalidError()

    new_access, new_refresh, user = await auth_service.rotate_refresh_token(
        db,
        old_refresh,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_auth_cookies(response, new_access, new_refresh)
    return AuthResponse(user=UserResponse.model_validate(user), message="refreshed")


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_optional_user),
) -> None:
    """Revoke the current refresh token and clear cookies."""
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token:
        await auth_service.revoke_session(db, refresh_token)
    _clear_auth_cookies(response)
    await audit_service.record(
        db,
        event_type="auth.logout",
        entity_type="user",
        entity_id=current_user.id if current_user else "anonymous",
        actor=current_user,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get the currently authenticated user."""
    return current_user
