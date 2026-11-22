"""Immutable protocol drafts and explicit activation under the actor transaction fence."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_planning import ResearchContextVersion
from app.models.research_protocol import ResearchProtocolActivation, ResearchProtocolVersion
from app.models.user import User
from app.preview.write_fence import acquire_write_fence
from app.research import protocol_proof as proof
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import credentials
from app.research.protocol_contracts import ResearchProtocolDocument
from app.research.protocol_requests import ProtocolActivate
from app.research.protocol_validation import assess_protocol, protocol_hash
from app.services import research_context_service as contexts
from app.services import research_service as ledger


def _key(key: str) -> str:
    if type(key) is not str or not 1 <= len(key) <= 200 or key.isspace() or credentials(key):
        raise ledger.error("PROTOCOL_REQUEST_INVALID", 422)
    return logical_hash({"protocol_key": key})


async def _scope(db: AsyncSession, owner: uuid.UUID, session_id: uuid.UUID, *, live: bool):
    from app.research.replay import verify_business

    await acquire_write_fence(db, owner)
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    _, scope = await ledger.session(db, session_id, owner, mode="live" if live else "history")
    await verify_business(db, owner, scope.id)
    return scope


async def _rows(db: AsyncSession, session_id: uuid.UUID, model, order) -> list[dict]:
    rows = (
        await db.scalars(
            select(model)
            .where(model.session_id == session_id)
            .order_by(order)
            .execution_options(populate_existing=True)
        )
    ).all()
    return [parse_document(row.document_json) for row in rows]


async def history(db: AsyncSession, owner: uuid.UUID, session_id: uuid.UUID) -> dict:
    await _scope(db, owner, session_id, live=False)
    return {
        "versions": await _rows(
            db, session_id, ResearchProtocolVersion, ResearchProtocolVersion.revision
        ),
        "activations": await _rows(
            db, session_id, ResearchProtocolActivation, ResearchProtocolActivation.generation
        ),
    }


async def save(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    document: ResearchProtocolDocument,
    expected_revision: int,
    idempotency_key: str,
) -> dict:
    scope = await _scope(db, owner, session_id, live=True)
    key_hash = _key(idempotency_key)
    try:
        document = ResearchProtocolDocument.model_validate(document.model_dump(mode="python"))
        digest = protocol_hash(document)
    except (ValueError, TypeError):
        raise ledger.error("PROTOCOL_INVALID", 422) from None
    if type(expected_revision) is not int or not 0 <= expected_revision < 100:
        raise ledger.error("PROTOCOL_REQUEST_INVALID", 422)
    content = document.model_dump(mode="json")
    request_hash = proof.save_request(content, expected_revision, key_hash)
    old = await _rows(db, session_id, ResearchProtocolVersion, ResearchProtocolVersion.revision)
    for old_version in old:
        if old_version["key_hash"] == key_hash:
            if old_version["request_hash"] != request_hash:
                raise ledger.error("IDEMPOTENCY_CONFLICT")
            return old_version  # Historical response, never an active authorization.
    if len(old) >= 100:
        raise ledger.error("PROTOCOL_VERSION_LIMIT", 422)
    if expected_revision != (old[-1]["revision"] if old else 0):
        raise ledger.error("STALE_REVISION")
    current = await contexts.snapshot(
        db, owner, session_id, expected_source_version=document.source_version
    )
    try:
        proof.source_checked(document, current)
    except ValueError:
        raise ledger.error("PROTOCOL_SOURCE_UNPROVEN", 422) from None
    attachments = await ledger.bindings(db, owner, session_id, mode="live")
    context_document = {"context": current.model_dump(mode="json"), "attachments": attachments}
    context_row = await db.scalar(
        select(ResearchContextVersion)
        .where(
            ResearchContextVersion.session_id == session_id,
            ResearchContextVersion.source_version == current.source_version,
        )
        .execution_options(populate_existing=True)
    )
    if context_row is None:
        context_row = ResearchContextVersion(
            id=uuid.uuid4(),
            owner_id=owner,
            session_id=session_id,
            source_version=current.source_version,
            document_json=canonical_bytes(context_document).decode(),
            document_hash=logical_hash(context_document),
        )
        db.add(context_row)
        await db.flush()
    elif (
        context_row.owner_id != owner
        or context_row.document_hash != logical_hash(context_document)
        or parse_document(context_row.document_json) != context_document
    ):
        raise ledger.error("PROTOCOL_SOURCE_UNPROVEN")
    version_id = uuid.uuid5(session_id, "protocol-version:" + key_hash)
    value: dict = {
        "version": 1,
        "owner_id": str(owner),
        "session_id": str(session_id),
        "revision": expected_revision + 1,
        "version_id": str(version_id),
        "context_id": str(context_row.id),
        "key_hash": key_hash,
        "request_hash": request_hash,
        "protocol_hash": digest,
        "protocol": content,
        **context_document,
    }
    db.add(
        ResearchProtocolVersion(
            id=version_id,
            owner_id=owner,
            session_id=session_id,
            context_id=context_row.id,
            revision=expected_revision + 1,
            key_hash=key_hash,
            request_hash=request_hash,
            protocol_hash=digest,
            document_json=canonical_bytes(value).decode(),
            document_hash=logical_hash(value),
        )
    )
    await db.flush()
    await ledger._append(db, scope, owner, "protocol_version", version_id, proof.SAVED, value)
    return value


async def activate(
    db: AsyncSession, owner: uuid.UUID, session_id: uuid.UUID, request: ProtocolActivate
) -> dict:
    scope = await _scope(db, owner, session_id, live=True)
    request = ProtocolActivate.model_validate(request.model_dump(mode="python"))
    key_hash = _key(request.idempotency_key)
    value: dict = {
        "version": 1,
        "owner_id": str(owner),
        "session_id": str(session_id),
        "activation_id": str(uuid.uuid5(session_id, "protocol-activation:" + key_hash)),
        "version_id": str(request.version_id),
        "protocol_hash": request.protocol_hash,
        "generation": request.expected_generation + 1,
        "action": request.action,
        "key_hash": key_hash,
    }
    value["request_hash"] = proof.activation_request(value)
    previous = await _rows(
        db, session_id, ResearchProtocolActivation, ResearchProtocolActivation.generation
    )
    for old in previous:
        if old["key_hash"] == key_hash:
            if old["request_hash"] != value["request_hash"]:
                raise ledger.error("IDEMPOTENCY_CONFLICT")
            return old  # A retry can report a revoked historical confirmation, never revive it.
    if len(previous) >= 200:
        raise ledger.error("PROTOCOL_ACTIVATION_LIMIT", 422)
    latest_activation = previous[-1] if previous else None
    if request.expected_generation != (latest_activation["generation"] if latest_activation else 0):
        raise ledger.error("STALE_REVISION")
    versions = await _rows(
        db, session_id, ResearchProtocolVersion, ResearchProtocolVersion.revision
    )
    version = next(
        (item for item in versions if item["version_id"] == str(request.version_id)), None
    )
    if version is None or version["protocol_hash"] != request.protocol_hash:
        raise ledger.error("PROTOCOL_UNAVAILABLE", 404)
    if request.action == "confirm":
        if version != versions[-1]:
            raise ledger.error("PROTOCOL_NOT_LATEST")
        if (
            latest_activation
            and latest_activation["action"] == "confirm"
            and latest_activation["version_id"] == version["version_id"]
        ):
            raise ledger.error("PROTOCOL_ALREADY_ACTIVE")
        current = await contexts.snapshot(
            db, owner, session_id, expected_source_version=version["protocol"]["source_version"]
        )
        assessment = assess_protocol(
            ResearchProtocolDocument.model_validate(version["protocol"]), current
        )
        if assessment.status != "ready":
            raise ledger.error("PROTOCOL_NEEDS_REVIEW", 422)
    elif (
        not latest_activation
        or latest_activation["action"] != "confirm"
        or latest_activation["version_id"] != version["version_id"]
    ):
        raise ledger.error("PROTOCOL_NOT_ACTIVE")
    db.add(
        ResearchProtocolActivation(
            id=uuid.UUID(value["activation_id"]),
            owner_id=owner,
            session_id=session_id,
            protocol_version_id=request.version_id,
            action=request.action,
            generation=value["generation"],
            key_hash=key_hash,
            request_hash=value["request_hash"],
            document_json=canonical_bytes(value).decode(),
            document_hash=logical_hash(value),
        )
    )
    await db.flush()
    await ledger._append(
        db,
        scope,
        owner,
        "protocol_activation",
        uuid.UUID(value["activation_id"]),
        proof.CONFIRMED if request.action == "confirm" else proof.REVOKED,
        value,
    )
    return value


async def require_active(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    version_id: uuid.UUID,
    digest: str,
    generation: int,
) -> dict:
    await _scope(db, owner, session_id, live=True)
    activations = await _rows(
        db, session_id, ResearchProtocolActivation, ResearchProtocolActivation.generation
    )
    latest = activations[-1] if activations else None
    if (
        latest is None
        or latest["action"] != "confirm"
        or latest["version_id"] != str(version_id)
        or latest["protocol_hash"] != digest
        or type(generation) is not int
        or latest["generation"] != generation
    ):
        raise ledger.error("PROTOCOL_NOT_ACTIVE")
    row = await db.get(ResearchProtocolVersion, version_id, populate_existing=True)
    if row is None or row.owner_id != owner or row.session_id != session_id:
        raise ledger.error("PROTOCOL_UNAVAILABLE", 404)
    value = parse_document(row.document_json)
    await contexts.snapshot(
        db, owner, session_id, expected_source_version=value["protocol"]["source_version"]
    )
    return {"version": value, "activation": latest}
