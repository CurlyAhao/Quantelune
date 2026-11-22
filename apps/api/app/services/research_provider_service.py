"""Journaled formal credentials. Explicit owned namespace; no secret persistence."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.research_provider import (
    ResearchCredentialOperation,
    ResearchProviderConfiguration,
    ResearchProviderEvent,
    ResearchProviderState,
)
from app.models.research_security import ResearchCharge
from app.preview.vault import Vault
from app.preview.write_fence import acquire_write_fence
from app.research import credential_liveness as liveness
from app.research.contracts import (
    HASH_VERSION,
    ZERO_HASH,
    canonical_bytes,
    event_hash,
    logical_hash,
    parse_document,
)
from app.research.egress import credentials
from app.research.vault import ResearchWindowsVault
from app.schemas.research_security import ProviderSave, ProviderSettings

vault: Vault = ResearchWindowsVault()


def failure(code: str, status: int = 409) -> StatForgeError:
    return StatForgeError(
        status, "RESEARCH_PROVIDER_" + code, "Secure provider configuration unavailable."
    )


def snapshot(
    row: ResearchProviderState | ResearchProviderConfiguration | ResearchCredentialOperation,
) -> dict[str, Any]:
    if isinstance(row, ResearchProviderState):
        return {
            "owner_id": str(row.owner_id),
            "current_id": str(row.current_id) if row.current_id else None,
            "revision": row.revision,
            "enabled": row.enabled,
        }
    if isinstance(row, ResearchProviderConfiguration):
        return {
            "id": str(row.id),
            "owner_id": str(row.owner_id),
            "namespace": row.namespace,
            "document": parse_document(row.document_json),
            "fingerprint": row.fingerprint,
            "key_state": row.key_state,
        }
    result: dict[str, Any] = {
        "id": str(row.id),
        "owner_id": str(row.owner_id),
        "namespace": row.namespace,
        "configuration_id": str(row.configuration_id),
        "kind": row.kind,
        "state": row.state,
        "result_code": row.result_code,
    }
    if any(
        value is not None
        for value in (
            row.producer_instance,
            row.producer_pid,
            row.producer_birth,
            row.lease_until_ms,
        )
    ):
        result.update(
            producer_instance=row.producer_instance,
            producer_pid=row.producer_pid,
            producer_birth=row.producer_birth,
            lease_until_ms=row.lease_until_ms,
        )
    return result


async def _state(db: AsyncSession, owner: uuid.UUID) -> ResearchProviderState | None:
    return await db.scalar(
        select(ResearchProviderState)
        .where(ResearchProviderState.owner_id == owner)
        .execution_options(populate_existing=True)
    )


async def _record(
    db: AsyncSession,
    state: ResearchProviderState,
    row: ResearchProviderState | ResearchProviderConfiguration | ResearchCredentialOperation,
) -> None:
    kind = (
        "provider_state"
        if isinstance(row, ResearchProviderState)
        else (
            "provider_configuration"
            if isinstance(row, ResearchProviderConfiguration)
            else "credential_operation"
        )
    )
    identifier = row.owner_id if isinstance(row, ResearchProviderState) else row.id
    payload = canonical_bytes(snapshot(row)).decode()
    digest = logical_hash(snapshot(row))
    sequence, occurred = (
        state.event_sequence + 1,
        datetime.now(UTC).isoformat(timespec="microseconds"),
    )
    chain = event_hash(
        str(state.owner_id),
        sequence,
        state.event_head_hash,
        kind,
        str(identifier),
        kind,
        digest,
        str(state.owner_id),
        occurred,
    )
    db.add(
        ResearchProviderEvent(
            id=uuid.uuid4(),
            owner_id=state.owner_id,
            sequence=sequence,
            entity_kind=kind,
            entity_id=identifier,
            event_type=kind,
            payload_json=payload,
            logical_hash=digest,
            previous_hash=state.event_head_hash,
            chain_hash=chain,
            hash_version=HASH_VERSION,
            occurred_at=occurred,
        )
    )
    state.event_sequence, state.event_head_hash = sequence, chain
    await db.flush()


async def verify(db: AsyncSession, owner: uuid.UUID) -> ResearchProviderState | None:
    # The caller holds the actor fence; this owner chain is not a business Run.
    state = await _state(db, owner)
    rows = (
        await db.scalars(
            select(ResearchProviderEvent)
            .where(ResearchProviderEvent.owner_id == owner)
            .order_by(ResearchProviderEvent.sequence),
            execution_options={"populate_existing": True},
        )
    ).all()
    configurations = (
        await db.scalars(
            select(ResearchProviderConfiguration).where(
                ResearchProviderConfiguration.owner_id == owner
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    operations = (
        await db.scalars(
            select(ResearchCredentialOperation).where(
                ResearchCredentialOperation.owner_id == owner
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    if state is None:
        if rows or configurations or operations:
            raise failure("LEDGER_INCONSISTENT")
        return None
    if len(rows) > 10000:
        raise failure("VERIFY_LIMIT")
    expected: dict[str, dict[uuid.UUID, dict[str, Any]]] = {
        "provider_state": {},
        "provider_configuration": {},
        "credential_operation": {},
    }
    previous = ZERO_HASH
    try:
        for number, row in enumerate(rows, start=1):
            document = parse_document(row.payload_json)
            if (
                row.entity_kind not in expected
                or row.event_type != row.entity_kind
                or (
                    row.sequence != number
                    or row.previous_hash != previous
                    or row.hash_version != HASH_VERSION
                    or row.logical_hash != logical_hash(document)
                )
            ):
                raise ValueError("Invalid provider event.")
            chain = event_hash(
                str(owner),
                number,
                previous,
                row.entity_kind,
                str(row.entity_id),
                row.event_type,
                row.logical_hash,
                str(owner),
                row.occurred_at,
            )
            if chain != row.chain_hash:
                raise ValueError("Invalid provider chain.")
            previous = chain
            expected[row.entity_kind][row.entity_id] = document
        if len(rows) != state.event_sequence or previous != state.event_head_hash:
            raise ValueError("Provider header mismatch.")
        for kind, entities in (
            ("provider_state", [state]),
            ("provider_configuration", configurations),
            ("credential_operation", operations),
        ):
            ids = {
                entity.owner_id if isinstance(entity, ResearchProviderState) else entity.id
                for entity in entities
            }
            if ids != set(expected[kind]):
                raise ValueError("Provider entity set mismatch.")
            for entity in entities:
                identity = (
                    entity.owner_id if isinstance(entity, ResearchProviderState) else entity.id
                )
                if snapshot(entity) != expected[kind][identity]:
                    raise ValueError("Provider business row mismatch.")
        by_id = {item.id: item for item in configurations}
        if state.current_id is not None and state.current_id not in by_id:
            raise ValueError("Missing current configuration.")
        if state.enabled and (
            state.current_id is None or by_id[state.current_id].key_state != "configured"
        ):
            raise ValueError("Invalid enabled state.")
        for cfg in configurations:
            document = parse_document(cfg.document_json)
            if logical_hash(document) != cfg.fingerprint or document["namespace"] != cfg.namespace:
                raise ValueError("Configuration fingerprint mismatch.")
            settings = ProviderSettings.model_validate(document["settings"])
            settings.unverified_price(cfg.fingerprint)
        for operation in operations:
            if operation.state == "confirmed":
                cfg = by_id[operation.configuration_id]
                if cfg.namespace != operation.namespace:
                    raise ValueError("Credential reference mismatch.")
    except (ValueError, KeyError, TypeError, ValidationError):
        raise failure("LEDGER_INCONSISTENT") from None
    return state


async def current(
    db: AsyncSession, owner: uuid.UUID, *, required: bool = False
) -> tuple[ResearchProviderState | None, ResearchProviderConfiguration | None]:
    await acquire_write_fence(db, owner)
    state = await verify(db, owner)
    cfg = (
        await db.get(ResearchProviderConfiguration, state.current_id, populate_existing=True)
        if state and state.current_id
        else None
    )
    if required and (
        state is None or not state.enabled or cfg is None or cfg.key_state != "configured"
    ):
        raise failure("NOT_CONFIGURED", 503)
    return state, cfg


def binding(state: ResearchProviderState, cfg: ResearchProviderConfiguration) -> str:
    return logical_hash(
        {"configuration": cfg.fingerprint, "revision": state.revision, "enabled": state.enabled}
    )


async def public(db: AsyncSession, owner: uuid.UUID) -> dict[str, Any]:
    state, cfg = await current(db, owner)
    return {
        "revision": state.revision if state else 0,
        "enabled": bool(state and state.enabled),
        "key_configured": bool(cfg and cfg.key_state == "configured"),
        "configuration_id": str(cfg.id) if cfg else None,
        "configuration_hash": binding(state, cfg) if state and cfg else None,
        "settings": ProviderSettings.model_validate(
            parse_document(cfg.document_json)["settings"]
        ).model_dump(mode="json")
        if cfg
        else None,
        "provider_verified": False,
    }  # No actual Keyring read or price/compatibility claim.


async def maintenance(db: AsyncSession, owner: uuid.UUID) -> dict[str, Any]:
    """Bounded owned journal metadata, never a Credential Manager enumeration."""
    state, _ = await current(db, owner)
    configurations = (
        await db.scalars(
            select(ResearchProviderConfiguration)
            .where(
                ResearchProviderConfiguration.owner_id == owner,
                ResearchProviderConfiguration.key_state != "deleted",
            )
            .order_by(
                (ResearchProviderConfiguration.id == (state.current_id if state else None)).desc(),
                ResearchProviderConfiguration.created_at.desc(),
                ResearchProviderConfiguration.id.desc(),
            )
            .limit(21),
            execution_options={"populate_existing": True},
        )
    ).all()
    operations = (
        await db.scalars(
            select(ResearchCredentialOperation)
            .where(
                ResearchCredentialOperation.owner_id == owner,
                ResearchCredentialOperation.state.in_(["intent", "cleanup_pending"]),
            )
            .order_by(ResearchCredentialOperation.created_at, ResearchCredentialOperation.id)
            .limit(21),
            execution_options={"populate_existing": True},
        )
    ).all()
    return {
        "revision": state.revision if state else 0,
        "limit": 20,
        "configurations_truncated": len(configurations) > 20,
        "operations_truncated": len(operations) > 20,
        "configurations": [
            {
                "configuration_id": str(row.id),
                "is_current": bool(state and state.current_id == row.id),
                "key_state": row.key_state,
            }
            for row in configurations[:20]
        ],
        "operations": [
            {
                "operation_id": str(row.id),
                "kind": row.kind,
                "state": row.state,
                "result_code": row.result_code,
            }
            for row in operations[:20]
        ],
    }


async def set_enabled(
    db: AsyncSession, owner: uuid.UUID, expected_revision: int, enabled: bool
) -> dict[str, Any]:
    state, cfg = await current(db, owner)
    if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
        raise failure("INVALID_CONFIGURATION", 422)
    if state is None:
        if enabled or expected_revision != 0:
            raise failure("NOT_CONFIGURED", 503)
        return await public(db, owner)
    if state.revision != expected_revision:
        raise failure("REVISION_CONFLICT")
    if enabled and (cfg is None or cfg.key_state != "configured"):
        raise failure("NOT_CONFIGURED", 503)
    if state.enabled != enabled:
        state.enabled = enabled
        state.revision += 1
        await _record(db, state, state)
    return await public(db, owner)


def _secret(backend: Vault, cfg: ResearchProviderConfiguration, owner: uuid.UUID) -> str:
    try:
        value = backend.get(cfg.namespace, owner)
    except Exception:
        raise failure("VAULT_UNAVAILABLE", 503) from None
    if not isinstance(value, str) or not 8 <= len(value) <= 4096 or "\r" in value or "\n" in value:
        raise failure("KEY_MISSING", 503)
    return value


async def _cleanup_new(
    db: AsyncSession, owner: uuid.UUID, operation_id: uuid.UUID, backend: Vault, reason: str
) -> None:
    await db.rollback()
    await acquire_write_fence(db, owner)
    state = await verify(db, owner)
    operation = await db.get(ResearchCredentialOperation, operation_id, populate_existing=True)
    if (
        state is None
        or operation is None
        or operation.owner_id != owner
        or operation.kind != "create"
    ):
        raise failure("CLEANUP_INCONSISTENT", 503)
    # Never compensate an activated configuration or another operation's target.
    if (
        state.current_id == operation.configuration_id
        or await db.get(
            ResearchProviderConfiguration, operation.configuration_id, populate_existing=True
        )
        is not None
    ):
        raise failure("CLEANUP_INCONSISTENT", 503)
    try:
        _delete_owned(backend, operation.namespace, owner)
        operation.state, operation.result_code = "deleted", reason
    except Exception:
        operation.state, operation.result_code = "cleanup_pending", "VAULT_DELETE_FAILED"
    await _record(db, state, operation)
    await db.commit()


@liveness.flow
async def save(
    db: AsyncSession, owner: uuid.UUID, request: ProviderSave, *, backend: Vault | None = None
) -> dict[str, Any]:
    """Commit an intent before OS write; only a fresh namespace can be compensated."""
    backend = backend if backend is not None else vault
    try:
        request = ProviderSave.model_validate(request.model_dump())
        settings = request.settings
        settings.unverified_price("0" * 64)
    except (ValueError, ValidationError):
        raise failure("INVALID_CONFIGURATION", 422) from None
    state, old = await current(db, owner)
    if (state.revision if state else 0) != request.expected_revision:
        raise failure("REVISION_CONFLICT")
    old_secret = None
    if old and old.key_state == "configured":
        try:
            old_secret = backend.get(old.namespace, owner)
        except Exception:
            raise failure("VAULT_UNAVAILABLE", 503) from None
    source = old
    if request.new_connection:
        if request.source_configuration_id is not None or request.key is None:
            raise failure("INVALID_CONFIGURATION", 422)
        source = None
    elif request.source_configuration_id is not None:
        source = await db.get(
            ResearchProviderConfiguration,
            uuid.UUID(request.source_configuration_id),
            populate_existing=True,
        )
        if source is None or source.owner_id != owner or source.key_state != "configured":
            raise failure("NOT_FOUND", 404)
    source_secret = (
        old_secret if source is old else (_secret(backend, source, owner) if source else None)
    )
    key = request.key.get_secret_value() if request.key is not None else source_secret
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or "\r" in key or "\n" in key:
        raise failure("INVALID_KEY", 422)
    if (
        source
        and request.key is None
        and parse_document(source.document_json)["settings"]["provider"] != settings.provider
    ):
        raise failure("NEW_PROVIDER_KEY_REQUIRED", 422)
    document = {
        "version": "research-provider-v1",
        "namespace": uuid.uuid4().hex,
        "settings": settings.model_dump(mode="json"),
    }
    source_document = parse_document(source.document_json) if source else {}
    if (
        request.new_connection
        or request.source_configuration_id is not None
        or "connection_id" in source_document
    ):
        document["connection_id"] = (
            source_document.get("connection_id", str(source.id)) if source else str(uuid.uuid4())
        )
        document["display_name"] = (
            request.display_name or source_document.get("display_name") or settings.model
        )
    if credentials(document, [value for value in (key, old_secret, source_secret) if value]):
        raise failure("CREDENTIAL_IN_METADATA", 422)
    if state is None:
        state = ResearchProviderState(
            owner_id=owner,
            current_id=None,
            revision=0,
            enabled=False,
            event_sequence=0,
            event_head_hash=ZERO_HASH,
        )
        db.add(state)
        await _record(db, state, state)
    cfg_id = uuid.uuid4()
    operation = ResearchCredentialOperation(
        id=uuid.uuid4(),
        owner_id=owner,
        namespace=document["namespace"],
        configuration_id=cfg_id,
        kind="create",
        state="intent",
        result_code=None,
        **liveness.metadata(),
    )
    operation_id = operation.id
    db.add(operation)
    await _record(db, state, operation)
    await db.commit()  # Durable intention contains no key bytes.
    try:
        backend.set(operation.namespace, owner, key)
    except Exception:
        await _cleanup_new(db, owner, operation_id, backend, "VAULT_WRITE_FAILED")
        raise failure("VAULT_WRITE_FAILED", 503) from None
    try:
        await acquire_write_fence(db, owner)
        state = await verify(db, owner)
        if state is None or state.revision != request.expected_revision:
            raise failure("REVISION_CONFLICT")
        cfg = ResearchProviderConfiguration(
            id=cfg_id,
            owner_id=owner,
            namespace=document["namespace"],
            document_json=canonical_bytes(document).decode(),
            fingerprint=logical_hash(document),
            key_state="configured",
        )
        db.add(cfg)
        await _record(db, state, cfg)
        state.current_id, state.revision, state.enabled = cfg.id, state.revision + 1, True
        confirmed_operation = await db.get(
            ResearchCredentialOperation, operation_id, populate_existing=True
        )
        assert confirmed_operation is not None
        confirmed_operation.state, confirmed_operation.result_code = (
            "confirmed",
            "CONFIGURATION_COMMITTED",
        )
        await _record(db, state, confirmed_operation)
        await _record(db, state, state)
        await db.commit()
    except Exception as error:
        await _cleanup_new(db, owner, operation_id, backend, "CONFIGURATION_COMMIT_FAILED")
        if isinstance(error, StatForgeError):
            raise error from None
        raise failure("CONFIGURATION_COMMIT_FAILED", 503) from None
    return await public(db, owner)


@liveness.flow
async def reconcile_one(
    db: AsyncSession,
    owner: uuid.UUID,
    operation_id: uuid.UUID,
    *,
    backend: Vault | None = None,
) -> str:
    """Recover only a verified stopped flow. Unknown/live writers retain their keys."""
    backend = backend if backend is not None else vault
    await acquire_write_fence(db, owner)
    state = await verify(db, owner)
    operation = await db.get(ResearchCredentialOperation, operation_id, populate_existing=True)
    if state is None or operation is None or operation.owner_id != owner:
        raise failure("CLEANUP_UNAVAILABLE", 404)
    if operation.state in {"confirmed", "deleted", "failed"}:
        return operation.state
    if operation.state not in {"intent", "cleanup_pending"} or not liveness.safe_to_reconcile(
        operation.producer_instance, operation.producer_pid, operation.producer_birth
    ):
        raise failure("RECOVERY_PROTECTED")
    cfg = await db.get(
        ResearchProviderConfiguration, operation.configuration_id, populate_existing=True
    )
    if operation.kind == "create":
        if cfg is not None or state.current_id == operation.configuration_id:
            raise failure("CLEANUP_INCONSISTENT", 503)
    elif operation.kind == "delete":
        if (
            cfg is None
            or cfg.owner_id != owner
            or cfg.namespace != operation.namespace
            or cfg.key_state != "delete_pending"
        ):
            raise failure("CLEANUP_INCONSISTENT", 503)
        if await _in_use(db, owner, cfg):
            raise failure("CREDENTIAL_IN_USE")
    else:
        raise failure("CLEANUP_INCONSISTENT", 503)
    claim = liveness.metadata()
    for name, value in claim.items():
        setattr(operation, name, value)
    operation.state, operation.result_code = "cleanup_pending", "RECOVERY_CLAIMED"
    await _record(db, state, operation)
    await db.commit()  # A crash during recovery has a fresh, identifiable writer.
    namespace, kind, configuration_id = (
        operation.namespace,
        operation.kind,
        operation.configuration_id,
    )
    try:
        _delete_owned(backend, namespace, owner)
        success = True
    except Exception:
        success = False
    await acquire_write_fence(db, owner)
    state = await verify(db, owner)
    recovered = await db.get(ResearchCredentialOperation, operation_id, populate_existing=True)
    if (
        state is None
        or recovered is None
        or recovered.producer_instance != claim["producer_instance"]
    ):
        raise failure("CLEANUP_INCONSISTENT", 503)
    recovered.state = "deleted" if success else "cleanup_pending"
    recovered.result_code = "RECOVERED_KEY_DELETED" if success else "VAULT_DELETE_FAILED"
    if kind == "delete":
        cfg = await db.get(ResearchProviderConfiguration, configuration_id, populate_existing=True)
        assert cfg is not None
        cfg.key_state = "deleted" if success else "delete_pending"
        await _record(db, state, cfg)
    await _record(db, state, recovered)
    await db.commit()
    if not success:
        raise failure("VAULT_DELETE_FAILED", 503)
    return recovered.state


@liveness.flow
async def cleanup_retired(
    db: AsyncSession,
    owner: uuid.UUID,
    configuration_id: uuid.UUID,
    *,
    backend: Vault | None = None,
) -> str:
    """Explicit retirement of one registered config; never a Credential Manager scan."""
    backend = backend if backend is not None else vault
    await acquire_write_fence(db, owner)
    state = await verify(db, owner)
    cfg = await db.get(ResearchProviderConfiguration, configuration_id, populate_existing=True)
    if state is None or cfg is None or cfg.owner_id != owner:
        raise failure("CLEANUP_UNAVAILABLE", 404)
    if state.current_id == configuration_id:
        raise failure("CURRENT_CONFIGURATION_PROTECTED")
    if cfg.key_state == "deleted":
        return "deleted"
    if await _in_use(db, owner, cfg):
        raise failure("CREDENTIAL_IN_USE")
    if cfg.key_state == "delete_pending":
        pending = await db.scalar(
            select(ResearchCredentialOperation)
            .where(
                ResearchCredentialOperation.owner_id == owner,
                ResearchCredentialOperation.configuration_id == configuration_id,
                ResearchCredentialOperation.kind == "delete",
                ResearchCredentialOperation.state.in_(["intent", "cleanup_pending"]),
            )
            .order_by(ResearchCredentialOperation.created_at.desc()),
            execution_options={"populate_existing": True},
        )
        if pending is None:
            raise failure("CLEANUP_INCONSISTENT", 503)
        return await reconcile_one(db, owner, pending.id, backend=backend)
    cfg.key_state = "delete_pending"
    operation = ResearchCredentialOperation(
        id=uuid.uuid4(),
        owner_id=owner,
        namespace=cfg.namespace,
        configuration_id=cfg.id,
        kind="delete",
        state="intent",
        result_code=None,
        **liveness.metadata(),
    )
    operation_id = operation.id
    db.add(operation)
    await _record(db, state, cfg)
    await _record(db, state, operation)
    await db.commit()
    try:
        _delete_owned(backend, operation.namespace, owner)
        success = True
    except Exception:
        success = False
    await acquire_write_fence(db, owner)
    state = await verify(db, owner)
    cfg = await db.get(ResearchProviderConfiguration, configuration_id, populate_existing=True)
    retired_operation = await db.get(
        ResearchCredentialOperation, operation_id, populate_existing=True
    )
    assert state is not None and cfg is not None and retired_operation is not None
    cfg.key_state = "deleted" if success else "delete_pending"
    retired_operation.state = "deleted" if success else "cleanup_pending"
    retired_operation.result_code = "RETIRED_KEY_DELETED" if success else "VAULT_DELETE_FAILED"
    await _record(db, state, cfg)
    await _record(db, state, retired_operation)
    await db.commit()
    if not success:
        raise failure("VAULT_DELETE_FAILED", 503)
    return "deleted"


async def reconcile_pending(
    db: AsyncSession,
    owner: uuid.UUID,
    *,
    backend: Vault | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 20:
        raise failure("RECOVERY_LIMIT", 422)
    await acquire_write_fence(db, owner)
    await verify(db, owner)
    identifiers = (
        await db.scalars(
            select(ResearchCredentialOperation.id)
            .where(
                ResearchCredentialOperation.owner_id == owner,
                ResearchCredentialOperation.state.in_(["intent", "cleanup_pending"]),
            )
            .order_by(ResearchCredentialOperation.created_at, ResearchCredentialOperation.id)
            .limit(limit),
            execution_options={"populate_existing": True},
        )
    ).all()
    await db.commit()
    results = []
    for identifier in identifiers:
        try:
            status = await reconcile_one(db, owner, identifier, backend=backend)
            await db.commit()
        except StatForgeError as error:
            await db.rollback()
            status = error.error_code
        results.append({"operation_id": str(identifier), "status": status})
    return {"items": results, "limit": limit}


def _delete_owned(backend: Vault, namespace: str, owner: uuid.UUID) -> None:
    if backend.get(namespace, owner) is None:
        return
    backend.delete(namespace, owner)
    if backend.get(namespace, owner) is not None:
        raise failure("VAULT_DELETE_FAILED", 503)


async def retry_cleanup(
    db: AsyncSession, owner: uuid.UUID, operation_id: uuid.UUID, *, backend: Vault | None = None
) -> None:
    await reconcile_one(db, owner, operation_id, backend=backend)


async def _in_use(db: AsyncSession, owner: uuid.UUID, cfg: ResearchProviderConfiguration) -> bool:
    records = (
        await db.scalars(
            select(ResearchProviderEvent).where(
                ResearchProviderEvent.owner_id == owner,
                ResearchProviderEvent.entity_kind == "provider_state",
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    hashes = set()
    for record in records:
        value = parse_document(record.payload_json)
        if value.get("current_id") == str(cfg.id) and value.get("enabled") is True:
            hashes.add(
                logical_hash(
                    {
                        "configuration": cfg.fingerprint,
                        "revision": value["revision"],
                        "enabled": True,
                    }
                )
            )
    charges = (
        await db.scalars(
            select(ResearchCharge).where(
                ResearchCharge.owner_id == owner,
                ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    return any(
        parse_document(row.specification_json).get("config_sha256") in hashes for row in charges
    )


@liveness.flow
async def delete(
    db: AsyncSession, owner: uuid.UUID, expected_revision: int, *, backend: Vault | None = None
) -> dict[str, Any]:
    backend = backend if backend is not None else vault
    state, cfg = await current(db, owner, required=True)
    assert state is not None and cfg is not None
    if state.revision != expected_revision:
        raise failure("REVISION_CONFLICT")
    if await _in_use(db, owner, cfg):
        raise failure("CREDENTIAL_IN_USE")
    state.enabled, state.revision = False, state.revision + 1
    configuration_id = cfg.id
    cfg.key_state = "delete_pending"
    operation = ResearchCredentialOperation(
        id=uuid.uuid4(),
        owner_id=owner,
        namespace=cfg.namespace,
        configuration_id=cfg.id,
        kind="delete",
        state="intent",
        result_code=None,
        **liveness.metadata(),
    )
    operation_id = operation.id
    db.add(operation)
    await _record(db, state, cfg)
    await _record(db, state, state)
    await _record(db, state, operation)
    await db.commit()
    try:
        _delete_owned(backend, cfg.namespace, owner)
        success = True
    except Exception:
        success = False
    await acquire_write_fence(db, owner)
    state = await verify(db, owner)
    assert state is not None
    cfg = await db.get(ResearchProviderConfiguration, configuration_id, populate_existing=True)
    deleted_operation = await db.get(
        ResearchCredentialOperation, operation_id, populate_existing=True
    )
    assert cfg is not None and deleted_operation is not None
    cfg.key_state = "deleted" if success else "delete_pending"
    deleted_operation.state = "deleted" if success else "cleanup_pending"
    deleted_operation.result_code = "KEY_DELETED" if success else "VAULT_DELETE_FAILED"
    await _record(db, state, cfg)
    await _record(db, state, deleted_operation)
    await db.commit()
    if not success:
        raise failure("VAULT_DELETE_FAILED", 503)
    return await public(db, owner)
