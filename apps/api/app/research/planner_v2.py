"""Server-owned Protocol binding for explicit Planner-v2, separate from v1 authority."""

import uuid
from typing import Literal

from pydantic import Field

from app.research.context_contracts import ContextModel, ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import DisclosureSelection, failure
from app.research.protocol_contracts import ResearchProtocolDocument
from app.research.protocol_validation import protocol_hash

VERSION = "research-planner-v2"
PROFILE = {
    "version": 2,
    "sdk": "deepagents-0.7.21",
    "profile": "statforge:planner-v2",
    "durability": "sync",
    "tools": "inspect/profile/propose_plan/write_todos",
    "plan": "research-plan-v2",
    "protocol": "research-protocol-v1",
}


class ProtocolMetadata(ContextModel):
    version: Literal["research-planner-context-v2"]
    protocol_version_id: uuid.UUID
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    generation: int = Field(strict=True, ge=1, le=200)
    protocol: ResearchProtocolDocument


def content(snapshot: dict) -> ResearchProtocolDocument:
    """Pure capture consistency; actual independent rows/events are checked separately."""
    from app.research.protocol_proof import source_checked

    if type(snapshot) is not dict or set(snapshot) != {"version", "activation"}:
        raise failure("PLANNER_PROTOCOL_CHANGED")
    canonical_bytes(snapshot)
    saved, active = snapshot["version"], snapshot["activation"]
    context = ContextSnapshot.model_validate(saved["context"])
    document = ResearchProtocolDocument.model_validate(saved["protocol"])
    if (
        active["action"] != "confirm"
        or active["version_id"] != saved["version_id"]
        or active["protocol_hash"] != saved["protocol_hash"]
        or active["owner_id"] != saved["owner_id"]
        or active["session_id"] != saved["session_id"]
        or saved["session_id"] != str(context.session_id)
        or document.source_version != context.source_version
        or protocol_hash(document) != saved["protocol_hash"]
        or type(active["generation"]) is not int
        or not 1 <= active["generation"] <= 200
    ):
        raise failure("PLANNER_PROTOCOL_CHANGED")
    source_checked(document, context)
    return document


def from_parameters(parameters: dict) -> dict | None:
    if (
        type(parameters) is dict
        and set(parameters) == {"operation", "source_version"}
        and parameters["operation"] == "plan"
    ):
        return None
    if (
        type(parameters) is not dict
        or set(parameters)
        not in (
            {"operation", "source_version", "planner_version", "protocol"},
            {"operation", "source_version", "planner_version", "protocol", "planner_wire_version"},
        )
        or parameters["operation"] != "plan"
        or parameters["planner_version"] != VERSION
    ):
        raise failure("PLANNER_VERSION_UNSUPPORTED")
    if "planner_wire_version" in parameters:
        from app.research.planner_wire import VERSION as WIRE_VERSION
        from app.research.planner_wire import VERSION_V2 as WIRE_VERSION_V2
        from app.research.planner_wire import VERSION_V3 as WIRE_VERSION_V3
        from app.research.planner_wire import VERSION_V4 as WIRE_VERSION_V4

        if type(parameters["planner_wire_version"]) is not str or parameters[
            "planner_wire_version"
        ] not in {WIRE_VERSION, WIRE_VERSION_V2, WIRE_VERSION_V3, WIRE_VERSION_V4}:
            raise failure("PLANNER_WIRE_VERSION_UNSUPPORTED")
    snapshot = parameters["protocol"]
    document = content(snapshot)
    if parameters["source_version"] != document.source_version:
        raise failure("PLANNER_PROTOCOL_CHANGED")
    return snapshot


def captured(run) -> dict | None:
    value = parse_document(run.input_json)
    snapshot = from_parameters(value["parameters"])
    if snapshot is not None and (
        run.kind != "planner"
        or value["kind"] != "planner"
        or logical_hash(value) != run.input_hash
        or snapshot["version"]["session_id"] != str(run.session_id)
        or snapshot["version"]["attachments"] != value["attachments"]
    ):
        raise failure("PLANNER_PROTOCOL_CHANGED")
    return snapshot


def wire_version(run) -> str | None:
    """Select only from a validated server-frozen Run, never current deployment defaults."""
    parameters = parse_document(run.input_json)["parameters"]
    if run.kind != "planner":
        if "planner_wire_version" in parameters:
            raise failure("PLANNER_WIRE_PROFILE_REFUSED")
        return None
    captured(run)
    return parameters.get("planner_wire_version")


def metadata(snapshot: dict) -> dict:
    document = content(snapshot)
    return ProtocolMetadata(
        version="research-planner-context-v2",
        protocol_version_id=uuid.UUID(snapshot["version"]["version_id"]),
        protocol_hash=snapshot["version"]["protocol_hash"],
        generation=snapshot["activation"]["generation"],
        protocol=document,
    ).model_dump(mode="json")


def check_metadata(value: dict) -> dict:
    canonical_bytes(value)
    normalized = ProtocolMetadata.model_validate(value)
    if protocol_hash(normalized.protocol) != normalized.protocol_hash:
        raise failure("PLANNER_PROTOCOL_CHANGED")
    return normalized.model_dump(mode="json")


def selected(snapshot: dict, selection: DisclosureSelection) -> None:
    document = content(snapshot)
    selected_metadata(metadata(snapshot), selection)
    if selection.question != document.research_question:
        raise failure("PLANNER_PROTOCOL_QUESTION_CHANGED")


def selected_metadata(value: dict, selection: DisclosureSelection) -> None:
    normalized = ProtocolMetadata.model_validate(check_metadata(value))
    if (
        selection.purpose != "planning"
        or selection.scope != "metadata_only"
        or selection.rows
        or selection.question != normalized.protocol.research_question
    ):
        raise failure("PLANNER_DISCLOSURE_REFUSED")
    allowed = {item.dataset_id: set(item.columns) for item in selection.datasets}
    if len(allowed) != len(selection.datasets):
        raise failure("PLANNER_SCOPE_CHANGED")
    fields = [item.reference for item in normalized.protocol.variables]
    pairing = normalized.protocol.pairing
    if pairing is not None and pairing.pair_id is not None:
        fields.append(pairing.pair_id)
    if any(field.name not in allowed.get(str(field.dataset_id), set()) for field in fields):
        raise failure("PLANNER_SCOPE_CHANGED")


async def live(db, owner, run) -> dict | None:
    from app.research import plan_versioning
    from app.services import research_protocol_service as protocols

    snapshot = captured(run)
    if snapshot is None:
        if await plan_versioning.enabled(db, owner, run.session_id):
            raise failure("PLANNER_PROTOCOL_REQUIRED")
        return None
    if snapshot["version"]["owner_id"] != str(owner):
        raise failure("PLANNER_PROTOCOL_CHANGED")
    current = await protocols.require_active(
        db,
        owner,
        run.session_id,
        uuid.UUID(snapshot["version"]["version_id"]),
        snapshot["version"]["protocol_hash"],
        snapshot["activation"]["generation"],
    )
    if current != snapshot:
        raise failure("PLANNER_PROTOCOL_CHANGED")
    return snapshot


async def before_event(db, event, owner, run) -> None:
    from app.research.protocol_history import before_plan

    snapshot = captured(run)
    if snapshot is None:
        return
    saved = snapshot["version"]
    await before_plan(
        db,
        event,
        owner,
        dict(
            protocol=snapshot,
            session_id=str(run.session_id),
            context=saved["context"],
            attachments=saved["attachments"],
        ),
    )


def as_of(run, versions, current) -> ContextSnapshot | None:
    snapshot = captured(run)
    if snapshot is None:
        if current is not None:
            raise ValueError("Legacy Planner work after Protocol activation.")
        return None
    if (
        current is None
        or current["action"] != "confirm"
        or snapshot
        != {"version": versions.get(snapshot["version"]["version_id"]), "activation": current}
    ):
        raise ValueError("Planner Protocol was not active at this event.")
    return ContextSnapshot.model_validate(snapshot["version"]["context"])


def plan_matches(run, plan):
    from app.research import plan_versioning

    snapshot = captured(run)
    if snapshot is None:
        if plan_versioning.is_v2(plan):
            raise failure("PLANNER_PROTOCOL_CHANGED")
        return plan
    if not plan_versioning.is_v2(plan):
        raise failure("PLANNER_PROTOCOL_REQUIRED")
    normalized = plan_versioning.validate(
        plan, ContextSnapshot.model_validate(snapshot["version"]["context"]), snapshot
    )
    return normalized
