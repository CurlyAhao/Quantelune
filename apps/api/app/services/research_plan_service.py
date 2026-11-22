"""Immutable event-backed Plan drafts; saving a proposal never starts a Run."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.research import ResearchEvent
from app.models.research_planning import ResearchContextVersion, ResearchPlanVersion
from app.models.user import User
from app.preview.write_fence import acquire_write_fence
from app.research import plan_versioning, protocol_history
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.plan_contracts import PlanDocument
from app.research.plan_v2_contracts import PlanDocumentV2
from app.services import research_context_service as contexts
from app.services import research_service as ledger

EVENT = "plan.draft_saved"


def document(event: ResearchEvent) -> dict:
    value = parse_document(event.payload_json)
    if event.entity_kind != "plan_draft" or event.logical_hash != logical_hash(value):
        raise ledger.error("PLAN_UNPROVEN")
    return value


async def versions(
    db: AsyncSession, owner: uuid.UUID, session_id: uuid.UUID, draft_id: uuid.UUID
) -> list[dict]:
    from app.research.replay import verify_business

    _conversation, scope = await ledger.session(db, session_id, owner)
    await verify_business(db, owner, scope.id)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == draft_id,
                ResearchEvent.event_type == EVENT,
            )
            .order_by(ResearchEvent.sequence)
            .limit(101)
        )
    ).all()
    if not events or len(events) > 100:
        raise ledger.error("PLAN_UNAVAILABLE", 404)
    result = [document(event) for event in events]
    if any(
        item["owner_id"] != str(owner)
        or item["session_id"] != str(session_id)
        or item["draft_id"] != str(draft_id)
        for item in result
    ):
        raise ledger.error("ACCESS_DENIED", 404)
    return result


async def save(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    plan: PlanDocument | PlanDocumentV2,
    expected_revision: int,
    idempotency_key: str,
    draft_id: uuid.UUID | None = None,
) -> dict:
    from app.research.replay import verify_business

    await acquire_write_fence(db, owner)
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    _conversation, scope = await ledger.session(db, session_id, owner)
    await verify_business(db, owner, scope.id)
    if (
        type(expected_revision) is not int
        or expected_revision < 0
        or not 1 <= len(idempotency_key) <= 200
    ):
        raise ledger.error("PLAN_CONFIGURATION_INVALID", 422)
    identity = draft_id or uuid.uuid5(session_id, "plan-draft:" + idempotency_key)
    existing = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == identity,
                ResearchEvent.event_type == EVENT,
            )
            .order_by(ResearchEvent.sequence)
            .limit(101)
        )
    ).all()
    values = [document(event) for event in existing]
    if any(
        item["owner_id"] != str(owner)
        or item["session_id"] != str(session_id)
        or item["draft_id"] != str(identity)
        for item in values
    ):
        raise ledger.error("ACCESS_DENIED", 404)
    try:
        for step in plan.steps:
            canonical_bytes(step.configuration)
        request_hash = logical_hash(
            {
                "plan": plan.model_dump(mode="json"),
                "expected_revision": expected_revision,
                "idempotency_key": idempotency_key,
                "draft_id": str(identity),
            }
        )
    except (ValueError, TypeError):
        raise ledger.error("PLAN_CONFIGURATION_INVALID", 422) from None
    for old in values:
        if old["idempotency_key"] == idempotency_key:
            if old["request_hash"] != request_hash or old["revision"] != expected_revision + 1:
                raise ledger.error("IDEMPOTENCY_CONFLICT")
            return old  # Proven historical response only; no new source authority.
    if len(existing) >= 100:
        raise ledger.error("PLAN_VERSION_LIMIT", 422)
    revision = values[-1]["revision"] if values else 0
    if revision != expected_revision or (draft_id is not None and not values):
        raise ledger.error("STALE_REVISION")
    current = await contexts.snapshot(
        db, owner, session_id, expected_source_version=plan.source_version
    )
    normalized, protocol_snapshot = await plan_versioning.live(db, owner, session_id, plan, current)
    digest = plan_versioning.digest(normalized)
    attachments = await ledger.bindings(db, owner, session_id, mode="live")
    context_document = {"context": current.model_dump(mode="json"), "attachments": attachments}
    context_record = await db.scalar(
        select(ResearchContextVersion)
        .where(
            ResearchContextVersion.session_id == session_id,
            ResearchContextVersion.source_version == current.source_version,
        )
        .execution_options(populate_existing=True)
    )
    if context_record is None:
        context_record = ResearchContextVersion(
            id=uuid.uuid4(),
            owner_id=owner,
            session_id=session_id,
            source_version=current.source_version,
            document_json=canonical_bytes(context_document).decode(),
            document_hash=logical_hash(context_document),
        )
        db.add(context_record)
        await db.flush()
    elif (
        context_record.owner_id != owner
        or context_record.document_hash != logical_hash(context_document)
        or parse_document(context_record.document_json) != context_document
    ):
        raise ledger.error("PLAN_SOURCE_UNPROVEN")
    version_id = uuid.uuid4()
    value = {
        "version": 3 if protocol_snapshot is not None else 2,
        "owner_id": str(owner),
        "session_id": str(session_id),
        "draft_id": str(identity),
        "revision": revision + 1,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "version_id": str(version_id),
        "context_id": str(context_record.id),
        "plan": normalized.model_dump(mode="json"),
        "plan_hash": digest,
        "context": current.model_dump(mode="json"),
        "attachments": attachments,
    }
    if protocol_snapshot is not None:
        value["protocol"] = protocol_snapshot
    db.add(
        ResearchPlanVersion(
            id=version_id,
            owner_id=owner,
            session_id=session_id,
            context_id=context_record.id,
            draft_id=identity,
            revision=revision + 1,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            plan_hash=digest,
            document_json=canonical_bytes(value).decode(),
            document_hash=logical_hash(value),
        )
    )
    await db.flush()
    await ledger._append(db, scope, owner, "plan_draft", identity, EVENT, value)
    return value


async def verify_event(
    db: AsyncSession,
    event: ResearchEvent,
    owner: uuid.UUID,
    projected_attachments: list[dict],
    previous: dict | None,
) -> dict:
    try:
        value = document(event)
    except StatForgeError:
        raise ValueError("Plan draft event hash or kind changed.") from None
    expected = {
        "version",
        "owner_id",
        "session_id",
        "draft_id",
        "revision",
        "idempotency_key",
        "plan",
        "plan_hash",
        "context",
        "attachments",
        "request_hash",
        "version_id",
        "context_id",
    }
    if value.get("version") == 3:
        expected.add("protocol")
    if (
        set(value) != expected
        or type(value["version"]) is not int
        or value["version"] not in {2, 3}
        or event.actor_id != owner
        or value["owner_id"] != str(owner)
        or value["draft_id"] != str(event.entity_id)
    ):
        raise ValueError("Plan draft event route.")
    row = await db.get(ResearchPlanVersion, uuid.UUID(value["version_id"]), populate_existing=True)
    context_record = await db.get(
        ResearchContextVersion, uuid.UUID(value["context_id"]), populate_existing=True
    )
    if (
        row is None
        or context_record is None
        or row.owner_id != owner
        or context_record.owner_id != owner
        or str(row.session_id) != value["session_id"]
        or context_record.session_id != row.session_id
        or row.context_id != context_record.id
        or row.draft_id != event.entity_id
        or row.revision != value["revision"]
        or row.request_hash != value["request_hash"]
        or row.idempotency_key != value["idempotency_key"]
        or row.plan_hash != value["plan_hash"]
        or row.document_hash != logical_hash(value)
        or parse_document(row.document_json) != value
        or parse_document(context_record.document_json)
        != {"context": value["context"], "attachments": value["attachments"]}
        or context_record.document_hash
        != logical_hash(parse_document(context_record.document_json))
        or context_record.source_version != value["context"]["source_version"]
    ):
        raise ValueError("Plan independent version/source receipt changed.")
    if type(value["revision"]) is not int or value["revision"] != (
        previous["revision"] + 1 if previous else 1
    ):
        raise ValueError("Plan draft CAS history.")
    if previous is not None and value["session_id"] != previous["session_id"]:
        raise ValueError("Plan draft Session changed.")
    context = ContextSnapshot.model_validate(value["context"])
    if context.workspace_id != event.workspace_id or str(context.session_id) != value["session_id"]:
        raise ValueError("Plan context route.")
    if value["attachments"] != projected_attachments:
        raise ValueError("Plan attachments at save changed.")
    data = context.model_dump(mode="json")
    data.pop("source_version")
    if context.source_version != logical_hash(
        {"context": data, "attachments": value["attachments"]}
    ):
        raise ValueError("Plan context source hash changed.")
    try:
        plan = plan_versioning.parse(value["plan"])
        if plan_versioning.is_v2(plan) != (value["version"] == 3):
            raise ValueError("Plan payload and model versions differ.")
        protocol_snapshot = (
            await protocol_history.before_plan(db, event, owner, value)
            if value["version"] == 3
            else None
        )
        normalized = plan_versioning.validate(plan, context, protocol_snapshot)
    except StatForgeError:
        raise ValueError("Plan draft validation failed.") from None
    if (
        normalized.model_dump(mode="json") != value["plan"]
        or plan_versioning.digest(normalized) != value["plan_hash"]
    ):
        raise ValueError("Plan immutable version changed.")
    return value
