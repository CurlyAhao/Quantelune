"""Source-preview settings, constructed before importing any application module."""

import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path


def configure(root: Path, instance: str, api_port: int, web_port: int) -> Path:
    root = root.resolve(strict=True)
    uuid.UUID(instance)
    if not 1024 <= api_port <= 65535 or not 1024 <= web_port <= 65535 or api_port == web_port:
        raise ValueError("PREVIEW_PORT_INVALID")
    state = root / "q" / "pv"
    # Only minimal Windows/Python runtime support survives. No provider, token,
    # tracing, database, proxy or user package configuration is inherited.
    minimal = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC")
        if key in os.environ
    }
    os.environ.clear()
    os.environ.update(minimal)
    for name in ("s", "t", "l"):
        (state / name).mkdir(parents=True, exist_ok=True)
    cookie = hashlib.sha256(str(state).casefold().encode()).hexdigest()[:12]
    os.environ.update(
        {
            "STATFORGE_SOURCE_PREVIEW": "1",
            "STATFORGE_PREVIEW_INSTANCE": instance,
            "ENVIRONMENT": "preview",
            "DEBUG": "false",
            "DESKTOP_MODE": "false",
            "DB_BACKEND": "sqlite",
            "SQLITE_PATH": str(state / "db.sqlite"),
            "STORAGE_BACKEND": "local",
            "LOCAL_STORAGE_PATH": str(state / "s"),
            "UPLOAD_TEMP_DIR": str(state / "t"),
            "APP_DATA_PATH": str(state),
            "LOG_PATH": str(state / "l"),
            "TEMP": str(state / "t"),
            "TMP": str(state / "t"),
            "AI_ENABLED": "false",
            "AI_API_KEY": "",
            "AI_MODEL": "",
            "AI_BASE_URL": "",
            "JWT_SECRET_KEY": secrets.token_urlsafe(48),
            "ACCESS_COOKIE_NAME": f"sf_ap_{cookie}_access",
            "REFRESH_COOKIE_NAME": f"sf_ap_{cookie}_refresh",
            "COOKIE_DOMAIN": "",
            "COOKIE_PATH": "/",
            "COOKIE_SECURE": "false",
            "CORS_ORIGINS": json.dumps(
                [f"http://127.0.0.1:{web_port}", f"http://localhost:{web_port}"]
            ),
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "OTEL_SDK_DISABLED": "true",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return state
