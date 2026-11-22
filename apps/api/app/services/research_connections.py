"""Connection views over the existing immutable provider and credential journal."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_provider import ResearchProviderConfiguration
from app.preview.vault import Vault
from app.research.contracts import parse_document
from app.research.egress import ENDPOINTS
from app.schemas.research_security import ProviderSettings
from app.services import research_provider_service as providers

NAMES = {"deepseek": "DeepSeek", "mimo": "MiMo", "glm": "GLM", "opencode_zen": "OpenCode Zen"}


def group(row: ResearchProviderConfiguration) -> str:
    return parse_document(row.document_json).get("connection_id", str(row.id))


async def owned(db: AsyncSession, owner: uuid.UUID):
    return (
        await db.scalars(
            select(ResearchProviderConfiguration)
            .where(
                ResearchProviderConfiguration.owner_id == owner,
                ResearchProviderConfiguration.key_state != "deleted",
            )
            .order_by(
                ResearchProviderConfiguration.created_at.desc(),
                ResearchProviderConfiguration.id.desc(),
            ),
            execution_options={"populate_existing": True},
        )
    ).all()


async def listing(db: AsyncSession, owner: uuid.UUID, offset=0, limit=20):
    state, current = await providers.current(db, owner)
    candidates = list(await owned(db, owner))
    if current and current.key_state != "deleted":
        candidates = [current, *[row for row in candidates if row.id != current.id]]
    seen, connections = set(), []
    for row in candidates:
        identifier = group(row)
        if identifier in seen:
            continue
        seen.add(identifier)
        document = parse_document(row.document_json)
        settings = ProviderSettings.model_validate(document["settings"]).model_dump(mode="json")
        connections.append(
            {
                "id": identifier,
                "configuration_id": str(row.id),
                "is_current": bool(state and state.current_id == row.id),
                "enabled": bool(state and state.current_id == row.id and state.enabled),
                "key_state": row.key_state,
                "name": document.get("display_name", settings["model"]),
                "settings": settings,
                "endpoint": ENDPOINTS.get(settings["provider"]),
                "created_at": row.created_at.isoformat(),
            }
        )
    return {
        "revision": state.revision if state else 0,
        "connections": connections[offset : offset + limit],
        "total": len(connections),
        "next_offset": offset + limit if offset + limit < len(connections) else None,
        "profiles": [
            {"id": key, "name": NAMES[key], "endpoint": value} for key, value in ENDPOINTS.items()
        ],
    }


async def selected(db, owner, configuration_id, expected_revision):
    state, _ = await providers.current(db, owner)
    if state is None or state.revision != expected_revision:
        raise providers.failure("REVISION_CONFLICT")
    row = await db.get(ResearchProviderConfiguration, configuration_id, populate_existing=True)
    if row is None or row.owner_id != owner or row.key_state == "deleted":
        raise providers.failure("NOT_FOUND", 404)
    return state, row


async def activate(db, owner, configuration_id, expected_revision):
    state, row = await selected(db, owner, configuration_id, expected_revision)
    if row.key_state != "configured":
        raise providers.failure("NOT_CONFIGURED")
    if state.current_id != row.id or not state.enabled:
        state.current_id, state.enabled, state.revision = row.id, True, state.revision + 1
        await providers._record(db, state, state)
    return await providers.public(db, owner)


async def inspect(db, owner, configuration_id, expected_revision, backend: Vault):
    _, row = await selected(db, owner, configuration_id, expected_revision)
    if row.key_state != "configured":
        raise providers.failure("NOT_CONFIGURED")
    settings = ProviderSettings.model_validate(parse_document(row.document_json)["settings"])
    settings.unverified_price(row.fingerprint)
    if settings.provider not in ENDPOINTS:
        raise providers.failure("UNSUPPORTED_PROFILE", 422)
    providers._secret(backend, row, owner)
    return {"configuration_id": str(row.id), "configuration_valid": True, "network_checked": False}


async def remove(db, owner, configuration_id, expected_revision, backend: Vault):
    state, row = await selected(db, owner, configuration_id, expected_revision)
    identifier = group(row)
    versions = [item for item in await owned(db, owner) if group(item) == identifier]
    for version in versions:
        if await providers._in_use(db, owner, version):
            raise providers.failure("CREDENTIAL_IN_USE")
    if state.current_id in {version.id for version in versions}:
        state.current_id, state.enabled, state.revision = None, False, state.revision + 1
        await providers._record(db, state, state)
        await db.commit()
    for version in versions:
        await providers.cleanup_retired(db, owner, version.id, backend=backend)
    return {"id": identifier, "status": "deleted"}
