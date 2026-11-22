"""Isolated local SQLite fixtures for the public project archive example."""
import os
import tempfile
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Configure the disposable database before importing application modules.
_root = Path(__file__).resolve().parents[3] / ".local" / "tests"
_root.mkdir(parents=True, exist_ok=True)
_temp = tempfile.TemporaryDirectory(prefix="archive-", dir=_root)
_data = Path(_temp.name)
os.environ.update(
    ENVIRONMENT="test", DB_BACKEND="sqlite", SQLITE_PATH=str(_data / "test.db"),
    STORAGE_BACKEND="local", LOCAL_STORAGE_PATH=str(_data / "storage"),
    APP_DATA_PATH=str(_data), UPLOAD_TEMP_DIR=str(_data / "uploads"), LOG_PATH="",
    DESKTOP_MODE="false", AI_ENABLED="false", AI_API_KEY="", SIDECAR_SESSION_TOKEN="",
)
from app.main import app


@pytest_asyncio.fixture
async def client():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as session:
            yield session


async def register_and_login(
    client: AsyncClient,
    email: str = "test@example.com",
    password: str = "SecurePass123!",
) -> dict:
    """Register a user and return the user dict. Cookies are set on the client."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    return resp.json()["user"]


async def create_project(
    client: AsyncClient,
    title: str = "Test Project",
) -> dict:
    """Create a project and return the project dict."""
    resp = await client.post(
        "/api/v1/projects",
        json={"title": title, "description": "A test project"},
    )
    assert resp.status_code == 201, f"Create project failed: {resp.text}"
    return resp.json()
