"""Desktop-mode bootstrap and safe runtime diagnostics."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.services.identity_service import find_user

DESKTOP_USER_EMAIL = "local@statforge.desktop"


async def ensure_desktop_user(db: AsyncSession) -> User:
    """Create the private, single-user desktop identity exactly once."""
    existing = await find_user(db, email=DESKTOP_USER_EMAIL)
    if existing is not None:
        return existing

    user = User(
        email=DESKTOP_USER_EMAIL,
        display_name="Local researcher",
        # Desktop authentication is the per-launch sidecar token, not this hash.
        password_hash="desktop-sidecar-token-auth",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


def directory_status(path: str | None) -> dict[str, object]:
    """Return a non-secret path and a real writeability probe."""
    if not path:
        return {"path": None, "writable": False}
    directory = Path(path).resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".statforge-write-", mode="wb"
        ) as probe:
            probe.write(b"ok")
        writable = True
    except OSError:
        writable = False
    return {"path": str(directory), "writable": writable}


def desktop_diagnostics() -> dict[str, object]:
    """Build a diagnostics payload that deliberately excludes credentials."""
    sqlite_path = Path(settings.sqlite_path).resolve()
    storage_path = Path(settings.local_storage_path).resolve()
    return {
        "desktop_mode": settings.desktop_mode,
        "api_version": settings.app_version,
        "db_backend": settings.db_backend,
        "sqlite_path": str(sqlite_path),
        "storage_backend": settings.storage_backend,
        "storage_path": str(storage_path),
        "application_data": directory_status(settings.app_data_path),
        "database_directory": directory_status(str(sqlite_path.parent)),
        "storage_directory": directory_status(str(storage_path)),
        "log_directory": directory_status(settings.log_path),
        "ai_configured": bool(
            settings.ai_enabled
            and settings.ai_api_key
            and settings.ai_model
            and settings.ai_base_url
        ),
        "session_token_configured": bool(os.environ.get("SIDECAR_SESSION_TOKEN")),
    }
