"""Explicit Plan versions and protocol snapshots; old v1 wire and hashes stay original."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, TypeGuard

from sqlalchemy import select

from app.models.research_protocol import ResearchProtocolActivation
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash
from app.research.plan_contracts import PlanDocument
from app.research.plan_validation import failure
from app.research.protocol_contracts import ResearchProtocolDocument
from app.research.protocol_validation import protocol_hash

if TYPE_CHECKING:
    from app.research.plan_v2_contracts import PlanDocumentV2


def parse(value: dict) -> PlanDocument | PlanDocumentV2:
    """Missing version keeps the original v1 default; unknown/invalid v2 never downgrades."""
    if type(value) is not dict:
        raise failure("DOCUMENT_INVALID")
    canonical_bytes(value)
    version = value.get("version", "research-plan-v1")
    if version == "research-plan-v1":
        return PlanDocument.model_validate(value)
    if version == "research-plan-v2":
        from app.research.plan_v2_contracts import PlanDocumentV2

        return PlanDocumentV2.model_validate(value)
    raise failure("VERSION_UNSUPPORTED")


def is_v2(plan: PlanDocument | PlanDocumentV2) -> TypeGuard[PlanDocumentV2]:
    return plan.version == "research-plan-v2"


def digest(plan: PlanDocument | PlanDocumentV2) -> str:
    if isinstance(plan, PlanDocument):
        from app.research.plan_validation import plan_hash

        return plan_hash(plan)
    from app.research.plan_v2_validation import plan_v2_hash

    return plan_v2_hash(plan)


def native_statistics(plan: PlanDocument | PlanDocumentV2) -> bool:
    return any(
        step.capability in {"statistics", "descriptive", "correlation"} for step in plan.steps
    )


def binding(plan: PlanDocumentV2) -> dict:
    return {
        "version": "research-protocol-binding-v1",
        "version_id": str(plan.protocol_version_id),
        "protocol_hash": plan.protocol_hash,
        "generation": plan.protocol_generation,
        "plan_hash": digest(plan),
    }


def captured_protocol(
    plan: PlanDocumentV2,
    context: ContextSnapshot,
    snapshot: dict,
) -> ResearchProtocolDocument:
    """Content binding only; callers separately prove actual rows and event order."""
    if type(snapshot) is not dict or set(snapshot) != {"version", "activation"}:
        raise failure("PROTOCOL_UNPROVEN")
    saved, activation = snapshot["version"], snapshot["activation"]
    if (
        type(saved) is not dict
        or type(activation) is not dict
        or saved.get("version_id") != str(plan.protocol_version_id)
        or saved.get("protocol_hash") != plan.protocol_hash
        or saved.get("session_id") != str(context.session_id)
        or saved.get("context") != context.model_dump(mode="json")
        or activation.get("session_id") != str(context.session_id)
        or activation.get("owner_id") != saved.get("owner_id")
        or activation.get("action") != "confirm"
        or activation.get("version_id") != str(plan.protocol_version_id)
        or activation.get("protocol_hash") != plan.protocol_hash
        or type(activation.get("generation")) is not int
        or activation["generation"] != plan.protocol_generation
    ):
        raise failure("PROTOCOL_UNPROVEN")
    document = ResearchProtocolDocument.model_validate(saved["protocol"])
    if (
        document.source_version != plan.source_version
        or protocol_hash(document) != plan.protocol_hash
    ):
        raise failure("PROTOCOL_UNPROVEN")
    return document


def validate(
    plan: PlanDocument | PlanDocumentV2,
    context: ContextSnapshot,
    snapshot: dict | None = None,
) -> PlanDocument | PlanDocumentV2:
    if isinstance(plan, PlanDocument):
        from app.research.plan_validation import validate as validate_v1

        return validate_v1(plan, context)
    if snapshot is None:
        raise failure("PROTOCOL_UNPROVEN")
    from app.research.plan_v2_validation import validate_v2

    return validate_v2(plan, context, captured_protocol(plan, context, snapshot))


async def enabled(db, owner: uuid.UUID, session_id: uuid.UUID) -> bool:
    """A revocation does not re-enable v1 statistical planning in an opted-in Session."""
    return (
        await db.scalar(
            select(ResearchProtocolActivation.id)
            .where(
                ResearchProtocolActivation.owner_id == owner,
                ResearchProtocolActivation.session_id == session_id,
            )
            .limit(1)
        )
        is not None
    )


async def live(
    db,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    plan: PlanDocument | PlanDocumentV2,
    context: ContextSnapshot,
) -> tuple[PlanDocument | PlanDocumentV2, dict | None]:
    """Caller holds the actor fence and has authorized current Context/Session."""
    if isinstance(plan, PlanDocument):
        if native_statistics(plan) and await enabled(db, owner, session_id):
            raise failure("PROTOCOL_VERSION_REQUIRED")
        return validate(plan, context), None
    from app.services import research_protocol_service

    snapshot = await research_protocol_service.require_active(
        db,
        owner,
        session_id,
        plan.protocol_version_id,
        plan.protocol_hash,
        plan.protocol_generation,
    )
    normalized = validate(plan, context, snapshot)
    from app.research.p4_method_capabilities import require_new_plan

    if not is_v2(normalized):
        raise failure("PROTOCOL_UNPROVEN")
    require_new_plan(normalized)
    return normalized, snapshot


def as_of(
    plan: PlanDocument | PlanDocumentV2,
    snapshot: dict | None,
    versions: dict[str, dict],
    activation: dict | None,
) -> None:
    """Use the replay cursor's actual prior state, never the latest activation row."""
    if isinstance(plan, PlanDocument):
        if native_statistics(plan) and activation is not None:
            raise ValueError("Legacy statistical Plan after Protocol activation.")
        return
    if (
        snapshot is None
        or activation is None
        or activation["action"] != "confirm"
        or snapshot
        != {"version": versions.get(str(plan.protocol_version_id)), "activation": activation}
        or activation["version_id"] != str(plan.protocol_version_id)
        or activation["protocol_hash"] != plan.protocol_hash
        or type(activation["generation"]) is not int
        or activation["generation"] != plan.protocol_generation
    ):
        raise ValueError("Plan Protocol confirmation was not active at this event.")


def snapshot_hash(snapshot: dict) -> str:
    """Admission/provenance consumers bind the whole frozen document, including wording."""
    return logical_hash(snapshot)
