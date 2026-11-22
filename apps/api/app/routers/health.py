"""Health check and readiness endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.config import settings
from app.database import async_session_factory
from app.dependencies import get_current_user
from app.desktop import desktop_diagnostics
from app.models.user import User
from app.services import redis_service, storage_service

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe — the process is running."""
    return {"status": "ok", "service": "statforge-api", "version": settings.app_version}


@router.get("/ready")
async def readiness_check() -> dict[str, Any]:
    """Readiness probe — checks PostgreSQL, Redis, and MinIO connectivity."""
    checks: dict[str, Any] = {}

    # Active database backend (SQLite on desktop, PostgreSQL on web).
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok", "backend": settings.db_backend}
    except Exception as exc:
        checks["database"] = {
            "status": "unavailable",
            "backend": settings.db_backend,
            "detail": type(exc).__name__,
        }

    if not settings.desktop_mode and settings.environment != "preview":
        checks["redis"] = await redis_service.redis_health_check()

    # MinIO
    checks["minio"] = storage_service.storage_health_check()

    all_ok = all(c.get("status") == "ok" for c in checks.values())
    return {
        "status": "ready" if all_ok else "degraded",
        "service": "statforge-api",
        "checks": checks,
    }


@router.get("/api/v1/desktop/diagnostics")
async def get_desktop_diagnostics(
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Return safe desktop paths, modes, and writeability checks."""
    if not settings.desktop_mode:
        raise HTTPException(status_code=404)
    return desktop_diagnostics()


@router.get("/api/v1/local-environment")
async def get_local_environment(current_user: User = Depends(get_current_user)) -> dict:
    """Inspect the running interpreter and the same editors used by plot files."""
    import platform
    import sys
    from importlib.metadata import PackageNotFoundError, version

    from app.services.plot_code_files import editors

    packages = []
    for name in ("numpy", "pandas", "polars", "scipy", "scikit-learn", "matplotlib", "seaborn"):
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = None
        packages.append({"name": name, "version": installed})
    available = {"editors": [], "default_editor": "", "native_launch": False}
    if (
        settings.storage_backend == "local"
        and settings.db_backend == "sqlite"
        and settings.app_data_path
    ):
        available = editors()
    return {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "packages": packages,
        **available,
    }
