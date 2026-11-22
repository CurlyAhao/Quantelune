"""Authentication orchestration: register, login, refresh, logout."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    TokenInvalidError,
    WeakPasswordError,
)
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services.identity_service import find_user
from app.services.password_service import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.services.token_service import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_token_expiry,
)

logger = logging.getLogger("statforge.auth")


async def register_user(
    db: AsyncSession,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    """Register a new user. Raises on duplicate email or weak password."""
    normalized_email = email.strip().lower()

    weakness = validate_password_strength(password)
    if weakness:
        raise WeakPasswordError(weakness)

    existing = await find_user(db, email=normalized_email)
    if existing is not None:
        raise EmailAlreadyRegisteredError()

    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=display_name or normalized_email.split("@")[0],
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    logger.info("User registered: %s", normalized_email)
    return user


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> User:
    """Verify credentials. Raises InvalidCredentialsError on failure.

    Deliberately uses the same error for wrong-email and wrong-password
    to avoid leaking account existence.
    """
    normalized_email = email.strip().lower()
    user = await find_user(db, email=normalized_email)

    if user is None or not verify_password(password, user.password_hash):
        logger.info("Login failed for email=%s", normalized_email)
        raise InvalidCredentialsError()

    if not user.is_active:
        raise InvalidCredentialsError()

    logger.info("User logged in: %s", normalized_email)
    return user


async def create_session(
    db: AsyncSession,
    user: User,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str]:
    """Create access + refresh tokens. Returns (access_token, refresh_token)."""
    access_token = create_access_token(user.id, user.email)
    refresh_token = generate_refresh_token()

    session = AuthSession(
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=refresh_token_expiry(),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(session)
    await db.flush()
    return access_token, refresh_token


async def rotate_refresh_token(
    db: AsyncSession,
    old_refresh_token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str, User]:
    """Validate old refresh token, revoke it, issue new pair.

    Returns (new_access_token, new_refresh_token, user).
    Raises TokenInvalidError if the token is unknown, expired, or revoked.
    """
    from datetime import UTC, datetime

    token_hash = hash_refresh_token(old_refresh_token)
    result = await db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash))
    session = result.scalar_one_or_none()

    if session is None or session.revoked:
        raise TokenInvalidError()

    if session.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        session.revoked = True
        await db.flush()
        raise TokenInvalidError()

    # Revoke old session
    session.revoked = True

    # Load user
    user = await find_user(db, user_id=session.user_id)
    if user is None or not user.is_active:
        raise TokenInvalidError()

    # Issue new pair
    new_access, new_refresh = await create_session(
        db,
        user,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return new_access, new_refresh, user


async def revoke_session(db: AsyncSession, refresh_token: str) -> None:
    """Revoke a single refresh token session (logout)."""
    token_hash = hash_refresh_token(refresh_token)
    result = await db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash))
    session = result.scalar_one_or_none()
    if session is not None:
        session.revoked = True
        await db.flush()


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke all sessions for a user. Returns count revoked."""
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked.is_(False),
        )
    )
    sessions = result.scalars().all()
    for session in sessions:
        session.revoked = True
    await db.flush()
    return len(sessions)
