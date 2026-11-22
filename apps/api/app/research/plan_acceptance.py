"""Actual actor consent binds one immutable version; accepting never computes."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchEvent
from app.models.research_planning import (
    ResearchContextVersion,
    ResearchPlanAcceptance,
    ResearchPlanStep,
    ResearchPlanVersion,
)
from app.research import execution_lease, plan_receipt, plan_versioning
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import credentials
from app.research.plan_validation import capabilities
from app.services import research_context_service as contexts
from app.services import research_plan_service as plans
from app.services import research_service as ledger

EVENT = "plan.accepted"


async def version(db, owner, session_id, version_id):
    """Independent SQL version/context and original event; callers verify temporal replay."""
    _, scope = await ledger.session(db, session_id, owner)
    row = await db.get(ResearchPlanVersion, version_id, populate_existing=True)
    if row is None or row.owner_id != owner or row.session_id != session_id:
        raise ledger.error("PLAN_UNAVAILABLE", 404)
    value = parse_document(row.document_json)
    context = await db.get(ResearchContextVersion, row.context_id, populate_existing=True)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == row.draft_id,
                ResearchEvent.event_type == plans.EVENT,
                ResearchEvent.actor_id == owner,
            )
            .limit(101)
        )
    ).all()
    matches = [
        event
        for event in events
        if parse_document(event.payload_json).get("version_id") == str(row.id)
    ]
    previous = None
    if row.revision > 1:
        prior = [
            parse_document(event.payload_json)
            for event in events
            if parse_document(event.payload_json).get("revision") == row.revision - 1
        ]
        if len(prior) != 1:
            raise ledger.error("PLAN_UNPROVEN")
        previous = prior[0]
    if context is None or len(events) > 100 or len(matches) != 1:
        raise ledger.error("PLAN_UNPROVEN")
    await plans.verify_event(db, matches[0], owner, value["attachments"], previous)
    return row, context, value


def step_documents(acceptance_id, plan):
    registered = {item.id: item for item in capabilities()}
    if plan_versioning.is_v2(plan):
        from app.statistics.registry import METHODSET_VERSION

        return [
            dict(
                version=2,
                acceptance_id=str(acceptance_id),
                step_id=step.id,
                capability_version=(
                    METHODSET_VERSION
                    if step.capability == "statistics"
                    else registered[step.capability].version
                ),
                step=step.model_dump(mode="json"),
                protocol=plan_versioning.binding(plan),
            )
            for step in plan.steps
        ]
    return [
        dict(
            version=1,
            acceptance_id=str(acceptance_id),
            step_id=step.id,
            capability_version=registered[step.capability].version,
            step=step.model_dump(mode="json"),
        )
        for step in plan.steps
    ]


async def create(db, owner, session_id, version_id, expected_plan_hash, source_version, key):
    from app.research.replay import verify_business

    await execution_lease._boundary(db, owner)
    await plan_receipt._actor(db, owner)
    _, scope = await ledger.session(db, session_id, owner)
    await verify_business(db, owner, scope.id)
    if not isinstance(key, str) or not 1 <= len(key) <= 200 or credentials(key):
        raise ledger.error("PLAN_ACCEPTANCE_INVALID", 422)
    key_hash = logical_hash(key)
    request = dict(
        owner_id=str(owner),
        session_id=str(session_id),
        version_id=str(version_id),
        plan_hash=expected_plan_hash,
        source_version=source_version,
        key_hash=key_hash,
    )
    old = await db.scalar(
        select(ResearchPlanAcceptance).where(
            ResearchPlanAcceptance.session_id == session_id,
            ResearchPlanAcceptance.key_hash == key_hash,
        )
    )
    if old is not None:
        value = parse_document(old.document_json)
        if value["request"] != request:
            raise ledger.error("IDEMPOTENCY_CONFLICT")
        return old  # Exact historical consent only; no new launch/source authority.
    await ledger.session(db, session_id, owner, mode="live")
    existing = await db.scalar(
        select(ResearchPlanAcceptance.id).where(
            ResearchPlanAcceptance.plan_version_id == version_id
        )
    )
    row, context_row, draft = await version(db, owner, session_id, version_id)
    if existing is not None:
        raise ledger.error("PLAN_ALREADY_ACCEPTED")
    current = await contexts.snapshot(db, owner, session_id, expected_source_version=source_version)
    normalized, protocol_snapshot = await plan_versioning.live(
        db, owner, session_id, plan_versioning.parse(draft["plan"]), current
    )
    if (
        row.plan_hash != expected_plan_hash
        or plan_versioning.digest(normalized) != expected_plan_hash
        or context_row.source_version != source_version
        or protocol_snapshot != draft.get("protocol")
    ):
        raise ledger.error("PLAN_SOURCE_UNPROVEN")
    identity = uuid.uuid4()
    steps = step_documents(identity, normalized)
    value = dict(
        version=2 if protocol_snapshot is not None else 1,
        acceptance_id=str(identity),
        request=request,
        plan_document_hash=row.document_hash,
        context_id=str(context_row.id),
        context_document_hash=context_row.document_hash,
        steps=steps,
    )
    if protocol_snapshot is not None:
        value["protocol"] = plan_versioning.binding(normalized)
    result = ResearchPlanAcceptance(
        id=identity,
        owner_id=owner,
        session_id=session_id,
        plan_version_id=version_id,
        key_hash=key_hash,
        document_json=canonical_bytes(value).decode(),
        document_hash=logical_hash(value),
        revision=0,
        cancel_requested=False,
    )
    db.add(result)
    await db.flush()
    for step in steps:
        db.add(
            ResearchPlanStep(
                id=uuid.uuid5(identity, step["step_id"]),
                acceptance_id=identity,
                step_id=step["step_id"],
                document_json=canonical_bytes(step).decode(),
                document_hash=logical_hash(step),
            )
        )
    await db.flush()
    await ledger._append(db, scope, owner, "plan_acceptance", identity, EVENT, value)
    return result


async def verify_event(db, event, owner, saved_versions):
    value = parse_document(event.payload_json)
    fields = {
        "version",
        "acceptance_id",
        "request",
        "plan_document_hash",
        "context_id",
        "context_document_hash",
        "steps",
    }
    if value.get("version") == 2:
        fields.add("protocol")
    if (
        event.entity_kind != "plan_acceptance"
        or event.actor_id != owner
        or set(value) != fields
        or type(value["version"]) is not int
        or value["version"] not in {1, 2}
        or value["acceptance_id"] != str(event.entity_id)
        or event.logical_hash != logical_hash(value)
    ):
        raise ValueError("Plan acceptance event route changed.")
    request = value["request"]
    if set(request) != {
        "owner_id",
        "session_id",
        "version_id",
        "plan_hash",
        "source_version",
        "key_hash",
    } or request["owner_id"] != str(owner):
        raise ValueError("Plan acceptance request route changed.")
    saved = saved_versions.get(request["version_id"])
    if (
        saved is None
        or saved["session_id"] != request["session_id"]
        or saved["plan_hash"] != request["plan_hash"]
        or saved["context"]["source_version"] != request["source_version"]
    ):
        raise ValueError("Accepted version is not independently proven before consent.")
    row = await db.get(ResearchPlanAcceptance, event.entity_id, populate_existing=True)
    context = await db.get(
        ResearchContextVersion, uuid.UUID(value["context_id"]), populate_existing=True
    )
    plan = plan_versioning.parse(saved["plan"])
    if plan_versioning.is_v2(plan) != (value["version"] == 2):
        raise ValueError("Plan acceptance version changed.")
    if value["version"] == 2 and value["protocol"] != plan_versioning.binding(plan):
        raise ValueError("Plan acceptance Protocol reference changed.")
    if (
        row is None
        or context is None
        or row.owner_id != owner
        or str(row.session_id) != request["session_id"]
        or str(row.plan_version_id) != request["version_id"]
        or row.key_hash != request["key_hash"]
        or row.document_hash != logical_hash(value)
        or parse_document(row.document_json) != value
        or value["plan_document_hash"] != logical_hash(saved)
        or value["context_id"] != saved["context_id"]
        or value["context_document_hash"] != context.document_hash
        or value["steps"] != step_documents(row.id, plan)
    ):
        raise ValueError("Plan acceptance independent row/version changed.")
    rows = (
        await db.scalars(
            select(ResearchPlanStep)
            .where(ResearchPlanStep.acceptance_id == row.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    expected = {step["step_id"]: step for step in value["steps"]}
    if {step.step_id for step in rows} != set(expected) or len(rows) != len(expected):
        raise ValueError("Accepted step set mismatch.")
    for step in rows:
        document = expected[step.step_id]
        if (
            step.id != uuid.uuid5(row.id, step.step_id)
            or step.document_hash != logical_hash(document)
            or parse_document(step.document_json) != document
        ):
            raise ValueError("Accepted step definition changed.")
    return value, ContextSnapshot.model_validate(saved["context"])
