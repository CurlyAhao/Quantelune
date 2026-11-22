"""Independent protocol events before a saved Plan; never asks today's source to rewrite history."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchEvent
from app.models.research_protocol import ResearchProtocolActivation, ResearchProtocolVersion
from app.research import protocol_proof as proof
from app.research.contracts import parse_document


async def before_plan(db, event: ResearchEvent, owner: uuid.UUID, value: dict) -> dict:
    """Caller additionally verifies the whole ledger or its ordered replay cursor."""
    snapshot = value.get("protocol")
    if type(snapshot) is not dict or set(snapshot) != {"version", "activation"}:
        raise ValueError("Saved Plan protocol snapshot missing.")
    saved, active = snapshot["version"], snapshot["activation"]
    session_id = uuid.UUID(value["session_id"])
    version_id = uuid.UUID(saved["version_id"])
    version_event = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == event.workspace_id,
                ResearchEvent.entity_id == version_id,
                ResearchEvent.event_type == proof.SAVED,
                ResearchEvent.sequence < event.sequence,
            )
            .limit(2)
        )
    ).all()
    if len(version_event) != 1 or parse_document(version_event[0].payload_json) != saved:
        raise ValueError("Protocol version event did not precede the Plan.")
    latest = (
        await db.scalars(
            select(ResearchEvent)
            .join(
                ResearchProtocolActivation,
                ResearchProtocolActivation.id == ResearchEvent.entity_id,
            )
            .where(
                ResearchEvent.workspace_id == event.workspace_id,
                ResearchEvent.event_type.in_([proof.CONFIRMED, proof.REVOKED]),
                ResearchEvent.sequence < event.sequence,
                ResearchProtocolActivation.session_id == session_id,
            )
            .order_by(ResearchEvent.sequence.desc())
            .limit(1)
        )
    ).first()
    if (
        latest is None
        or latest.event_type != proof.CONFIRMED
        or latest.sequence <= version_event[0].sequence
        or parse_document(latest.payload_json) != active
        or saved["session_id"] != value["session_id"]
        or active["session_id"] != value["session_id"]
        or saved["owner_id"] != str(owner)
        or active["owner_id"] != str(owner)
    ):
        raise ValueError("Plan Protocol was not confirmed at its save event.")
    previous_version = await db.scalar(
        select(ResearchProtocolVersion)
        .where(
            ResearchProtocolVersion.session_id == session_id,
            ResearchProtocolVersion.revision == saved["revision"] - 1,
        )
        .execution_options(populate_existing=True)
    )
    previous_activation = await db.scalar(
        select(ResearchProtocolActivation)
        .where(
            ResearchProtocolActivation.session_id == session_id,
            ResearchProtocolActivation.generation == active["generation"] - 1,
        )
        .execution_options(populate_existing=True)
    )
    previous_value = parse_document(previous_version.document_json) if previous_version else None
    previous_action = (
        parse_document(previous_activation.document_json) if previous_activation else None
    )
    await proof.verify_version(db, version_event[0], owner, value["attachments"], previous_value)
    # Confirming this version required the latest draft at that earlier confirmation,
    # which the whole replay proves. A newer unconfirmed draft may exist by Plan save.
    await proof.verify_activation(
        db,
        latest,
        owner,
        {saved["version_id"]: saved},
        saved,
        previous_action,
    )
    if saved["context"] != value["context"] or saved["attachments"] != value["attachments"]:
        raise ValueError("Plan and Protocol captured different sources.")
    return snapshot
