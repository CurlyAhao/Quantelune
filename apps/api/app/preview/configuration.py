"""Safe local settings and atomic, conservative per-call budget reservations."""

import json
import uuid
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.preview import PreviewCharge, PreviewConfig
from app.preview import vault as credentials
from app.preview.context import canonical
from app.preview.security import contains_secret
from app.preview.write_fence import acquire_write_fence
from app.schemas.preview import ProviderConfig, SaveProviderConfig

ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/chat/completions",
    "mimo": "https://api.xiaomimimo.com/v1/chat/completions",
    "glm": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "opencode_zen": "https://opencode.ai/zen/v1/chat/completions",
}


def not_ready() -> StatForgeError:
    return StatForgeError(
        409, "AGENT_NOT_READY", "Configure secure key, model, price ceilings and budget first."
    )


async def get_config(db: AsyncSession, user_id: uuid.UUID) -> PreviewConfig | None:
    with db.no_autoflush:
        return await db.scalar(
            select(PreviewConfig)
            .where(PreviewConfig.user_id == user_id)
            .execution_options(populate_existing=True)
        )


def public_config(row: PreviewConfig | None) -> dict[str, Any]:
    settings: dict[str, Any] | None = None
    if row and row.config_json != "{}":
        try:
            candidate = json.loads(row.config_json)
            settings = ProviderConfig.model_validate(candidate).model_dump(mode="json")
        except Exception:
            raise StatForgeError(
                409, "PREVIEW_CONFIG_INVALID", "Configuration requires secure review."
            ) from None
        if row.key_configured:
            secret = credentials.vault.get(row.vault_namespace, row.user_id)
            if not secret:
                raise not_ready()
            if contains_secret(candidate, secret):
                raise StatForgeError(
                    409, "PREVIEW_CONFIG_INVALID", "Configuration requires secure review."
                )
    return {
        "configured": bool(row and row.key_configured and row.config_json != "{}"),
        "key_configured": bool(row and row.key_configured),
        "revision": row.revision if row else None,
        "settings": settings,
        "reserved": None
        if settings and settings.get("pricing_mode") == "off"
        else str(row.reserved)
        if row
        else "0",
        "provider_verified": False,
        "billing_notice": "Local conservative call budget; provider invoice ceiling is unverified.",
    }


async def save_config(
    db: AsyncSession, user_id: uuid.UUID, body: SaveProviderConfig
) -> dict[str, Any]:
    secret = body.key.get_secret_value() if body.key is not None else None
    if secret is not None and (not 8 <= len(secret) <= 4096 or any(c in secret for c in "\r\n")):
        raise StatForgeError(422, "PREVIEW_CONFIG_INVALID", "Invalid secure key input.")
    candidate = body.model_dump(mode="json", exclude={"key"})
    if secret is not None and contains_secret(candidate, secret):
        raise StatForgeError(422, "PREVIEW_SECRET_INPUT", "Use only the secure key entry.")
    # Preliminary credential scan avoids even a lock for known bad input.
    row = await get_config(db, user_id)
    if row is not None and row.key_configured:
        existing_secret = credentials.vault.get(row.vault_namespace, user_id)
        if not existing_secret and secret is None:
            raise not_ready()
        if existing_secret and contains_secret(candidate, existing_secret):
            raise StatForgeError(422, "PREVIEW_SECRET_INPUT", "Use only the secure key entry.")
    await acquire_write_fence(db, user_id)
    row = await get_config(db, user_id)
    if row is not None and row.key_configured:
        existing_secret = credentials.vault.get(row.vault_namespace, user_id)
        if not existing_secret and secret is None:
            raise not_ready()
        if existing_secret and contains_secret(candidate, existing_secret):
            raise StatForgeError(422, "PREVIEW_SECRET_INPUT", "Use only the secure key entry.")
    if row is None:
        row = PreviewConfig(user_id=user_id)
        db.add(row)
        await db.flush()
    if body.budget is not None and row.reserved > body.budget:
        raise StatForgeError(
            409, "PREVIEW_BUDGET_INVALID", "Budget cannot be below retained reservations."
        )
    old = ProviderConfig.model_validate_json(row.config_json) if row.config_json != "{}" else None
    if old and old.provider != body.provider and secret is None:
        raise StatForgeError(
            409, "PREVIEW_PROVIDER_KEY_REQUIRED", "Changing provider requires a newly supplied key."
        )
    if old and old.currency != body.currency and row.reserved:
        raise StatForgeError(
            409, "PREVIEW_BUDGET_INVALID", "Currency cannot change with retained reservations."
        )
    namespace = row.vault_namespace
    key_configured = row.key_configured
    if secret is not None:
        # Immutable credential binding: rotation never overwrites a target used
        # by an in-flight approved provider configuration.
        namespace = uuid.uuid4().hex
        credentials.vault.set(namespace, user_id, secret)
        key_configured = True
    statement = update(PreviewConfig).where(
        PreviewConfig.id == row.id,
        PreviewConfig.revision == row.revision,
        *([PreviewConfig.reserved <= body.budget] if body.budget is not None else []),
    )
    if old and old.currency != body.currency:
        statement = statement.where(PreviewConfig.reserved == 0)
    changed = await db.scalar(
        statement.values(
            config_json=canonical(candidate),
            vault_namespace=namespace,
            key_configured=key_configured,
            revision=row.revision + 1,
        )
        .returning(PreviewConfig.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        await db.rollback()
        if secret is not None:
            try:
                credentials.vault.delete(namespace, user_id)
            except StatForgeError:
                pass  # Unused owned credential may require local cleanup; never reuse it.
        raise StatForgeError(409, "PREVIEW_CONFIG_CONFLICT", "Configuration changed; review again.")
    await db.commit()
    return public_config(await get_config(db, user_id))


async def reserve(
    db: AsyncSession,
    row: PreviewConfig,
    turn_id: uuid.UUID,
    config: ProviderConfig,
    input_bytes: int,
) -> None:
    if input_bytes > config.max_input_bytes:
        raise StatForgeError(
            413, "PREVIEW_INPUT_LIMIT", "Approved prompt exceeds configured input limit."
        )
    if config.pricing_mode == "off":
        await db.commit()
        return  # No monetary reservation or invented price for an unpriced call.
    # UTF-8 bytes plus protocol overhead bound token input conservatively. Output
    # includes a 4x margin; unverified hidden reasoning/vendor prices remain an
    # explicitly unverified invoice limit. Never release missing usage as zero.
    amount = (
        (
            Decimal(input_bytes + 4096) * config.input_price_per_million
            + Decimal(
                config.max_output_tokens * 4 if config.max_output_tokens is not None else 65536
            )
            * config.output_price_per_million
        )
        / Decimal(1_000_000)
    ).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
    result = await db.execute(
        update(PreviewConfig)
        .where(
            PreviewConfig.id == row.id,
            PreviewConfig.revision == row.revision,
            *(
                [PreviewConfig.reserved + amount <= config.budget]
                if config.budget is not None
                else []
            ),
        )
        .values(reserved=PreviewConfig.reserved + amount)
        .returning(PreviewConfig.id)
        .execution_options(synchronize_session=False)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise StatForgeError(
            409, "PREVIEW_BUDGET_EXCEEDED", "Configuration changed or local budget exhausted."
        )
    db.add(PreviewCharge(config_id=row.id, turn_id=turn_id, amount=amount))
    await db.commit()
