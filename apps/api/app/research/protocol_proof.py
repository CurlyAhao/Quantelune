"""Historical independent-row protocol proofs, with no current source reinterpretation."""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import ResearchEvent
from app.models.research_planning import ResearchContextVersion
from app.models.research_protocol import ResearchProtocolActivation, ResearchProtocolVersion
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import logical_hash, parse_document
from app.research.protocol_contracts import ResearchProtocolDocument
from app.research.protocol_validation import assess_protocol, protocol_hash

SAVED = "protocol.version_saved"
CONFIRMED = "protocol.confirmed"
REVOKED = "protocol.revoked"
KINDS = frozenset({SAVED, CONFIRMED, REVOKED})
VERSION_FIELDS = {
    "version",
    "owner_id",
    "session_id",
    "revision",
    "version_id",
    "context_id",
    "key_hash",
    "request_hash",
    "protocol_hash",
    "protocol",
    "context",
    "attachments",
}
ACTIVATION_FIELDS = {
    "version",
    "owner_id",
    "session_id",
    "activation_id",
    "version_id",
    "protocol_hash",
    "generation",
    "action",
    "key_hash",
    "request_hash",
}


def save_request(document: dict, revision: int, key_hash: str) -> str:
    return logical_hash({"protocol": document, "expected_revision": revision, "key_hash": key_hash})


def activation_request(value: dict) -> str:
    return logical_hash(
        {
            "version_id": value["version_id"],
            "protocol_hash": value["protocol_hash"],
            "expected_generation": value["generation"] - 1,
            "action": value["action"],
            "key_hash": value["key_hash"],
        }
    )


def source_checked(document: ResearchProtocolDocument, context: ContextSnapshot) -> None:
    if document.source_version != context.source_version:
        raise ValueError("Protocol source version changed.")
    references = [field.reference for source in context.datasets for field in source.fields]
    selected = [variable.reference for variable in document.variables]
    if document.pairing and document.pairing.pair_id:
        selected.append(document.pairing.pair_id)
    if any(references.count(reference) != 1 for reference in selected):
        raise ValueError("Protocol field does not bind the captured authorized source.")


def envelope(event: ResearchEvent, owner: uuid.UUID, fields: set[str], entity: str) -> dict:
    value = parse_document(event.payload_json)
    if (
        type(value) is not dict
        or set(value) != fields
        or type(value["version"]) is not int
        or value["version"] != 1
        or event.actor_id != owner
        or event.entity_kind != entity
        or value["owner_id"] != str(owner)
        or event.logical_hash != logical_hash(value)
        or any(
            type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
            for key in ("key_hash", "request_hash", "protocol_hash")
        )
        or str(uuid.UUID(value["session_id"])) != value["session_id"]
    ):
        raise ValueError("Protocol event envelope changed.")
    return value


async def verify_version(
    db: AsyncSession,
    event: ResearchEvent,
    owner: uuid.UUID,
    attachments: list[dict],
    previous: dict | None,
) -> dict:
    value = envelope(event, owner, VERSION_FIELDS, "protocol_version")
    session_id = uuid.UUID(value["session_id"])
    if (
        event.event_type != SAVED
        or value["version_id"] != str(event.entity_id)
        or type(value["revision"]) is not int
        or not 1 <= value["revision"] <= 100
        or value["revision"] != (previous["revision"] + 1 if previous else 1)
        or value["version_id"]
        != str(uuid.uuid5(session_id, "protocol-version:" + value["key_hash"]))
        or value["request_hash"]
        != save_request(value["protocol"], value["revision"] - 1, value["key_hash"])
    ):
        raise ValueError("Protocol version order or request changed.")
    row = await db.get(ResearchProtocolVersion, event.entity_id, populate_existing=True)
    context_row = await db.get(
        ResearchContextVersion, uuid.UUID(value["context_id"]), populate_existing=True
    )
    context_document = {"context": value["context"], "attachments": value["attachments"]}
    if (
        row is None
        or context_row is None
        or row.owner_id != owner
        or context_row.owner_id != owner
        or row.session_id != session_id
        or context_row.session_id != session_id
        or row.context_id != context_row.id
        or str(context_row.id) != value["context_id"]
        or row.document_hash != logical_hash(value)
        or parse_document(row.document_json) != value
        or any(
            getattr(row, key) != value[key]
            for key in ("revision", "key_hash", "request_hash", "protocol_hash")
        )
        or context_row.document_hash != logical_hash(context_document)
        or parse_document(context_row.document_json) != context_document
        or context_row.source_version != value["context"]["source_version"]
        or value["attachments"] != attachments
    ):
        raise ValueError("Protocol independent version/context row changed.")
    context = ContextSnapshot.model_validate(value["context"])
    doc = ResearchProtocolDocument.model_validate(value["protocol"])
    source_checked(doc, context)
    source_document = context.model_dump(mode="json")
    source_document.pop("source_version")
    if (
        context.workspace_id != event.workspace_id
        or context.session_id != session_id
        or logical_hash({"context": source_document, "attachments": attachments})
        != context.source_version
        or doc.model_dump(mode="json") != value["protocol"]
        or protocol_hash(doc) != value["protocol_hash"]
    ):
        raise ValueError("Protocol captured source identity changed.")
    return value


async def verify_activation(
    db: AsyncSession,
    event: ResearchEvent,
    owner: uuid.UUID,
    versions: dict[str, dict],
    latest: dict | None,
    previous: dict | None,
) -> dict:
    value = envelope(event, owner, ACTIVATION_FIELDS, "protocol_activation")
    session_id = uuid.UUID(value["session_id"])
    version = versions.get(value["version_id"])
    action = "confirm" if event.event_type == CONFIRMED else "revoke"
    if (
        event.event_type not in {CONFIRMED, REVOKED}
        or value["action"] != action
        or value["activation_id"] != str(event.entity_id)
        or value["activation_id"]
        != str(uuid.uuid5(session_id, "protocol-activation:" + value["key_hash"]))
        or type(value["generation"]) is not int
        or not 1 <= value["generation"] <= 200
        or value["generation"] != (previous["generation"] + 1 if previous else 1)
        or version is None
        or version["session_id"] != value["session_id"]
        or version["protocol_hash"] != value["protocol_hash"]
        or value["request_hash"] != activation_request(value)
    ):
        raise ValueError("Protocol activation order or request changed.")
    if action == "confirm":
        if (
            version != latest
            or (
                previous is not None
                and previous["action"] == "confirm"
                and previous["version_id"] == value["version_id"]
            )
            or assess_protocol(
                ResearchProtocolDocument.model_validate(version["protocol"]),
                ContextSnapshot.model_validate(version["context"]),
            ).status
            != "ready"
        ):
            raise ValueError("Protocol confirmation is not a ready latest draft.")
    elif (
        previous is None
        or previous["action"] != "confirm"
        or previous["version_id"] != value["version_id"]
    ):
        raise ValueError("Protocol revocation has no active version.")
    row = await db.get(ResearchProtocolActivation, event.entity_id, populate_existing=True)
    if (
        row is None
        or row.owner_id != owner
        or row.session_id != session_id
        or str(row.protocol_version_id) != value["version_id"]
        or row.document_hash != logical_hash(value)
        or parse_document(row.document_json) != value
        or any(
            getattr(row, key) != value[key]
            for key in ("generation", "action", "key_hash", "request_hash")
        )
    ):
        raise ValueError("Protocol independent activation row changed.")
    return value


async def verify_sets(db: AsyncSession, sessions: dict, versions: dict, activations: set) -> None:
    for model, expected in (
        (ResearchProtocolVersion, {uuid.UUID(key) for key in versions}),
        (ResearchProtocolActivation, activations),
    ):
        actual = set(
            (await db.scalars(select(model.id).where(model.session_id.in_(sessions)))).all()
        )
        if actual != expected:
            raise ValueError("Protocol event/independent row set differs.")
