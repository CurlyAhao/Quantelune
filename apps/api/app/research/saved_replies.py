"""Historical model-visible output, with no live-send or configuration dependency.

This API reads committed advisory content. It does not prove business completion,
model quality, or that the Provider's original HTTP bytes were retained.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import ResearchEvent, ResearchWorkspace
from app.models.research_harness import ResearchAdvisoryReceipt
from app.models.research_security import ResearchCharge
from app.research import historical_reply
from app.research.contracts import canonical_bytes, parse_document
from app.research.egress import failure
from app.services import research_service as ledger

MAX_REPLIES = 300
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


async def read(db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID) -> dict:
    run, scope = await ledger.run(db, run_id, owner)
    head_query = select(ResearchWorkspace.event_sequence, ResearchWorkspace.event_head_hash).where(
        ResearchWorkspace.id == scope.id, ResearchWorkspace.owner_id == owner
    )
    before = (await db.execute(head_query)).one_or_none()
    if before is None:
        raise failure("REPLY_UNPROVEN")
    charges = (
        await db.scalars(
            select(ResearchCharge)
            .where(ResearchCharge.run_id == run.id)
            .order_by(ResearchCharge.id)
            .limit(MAX_REPLIES + 1)
        )
    ).all()
    if len(charges) > MAX_REPLIES:
        raise failure("REPLY_LIMIT")
    replies: list[dict[str, Any]] = []
    try:
        for charge in charges:
            if charge.owner_id != owner or charge.state != "settled":
                raise failure("REPLY_UNPROVEN")
            row = await db.get(ResearchAdvisoryReceipt, charge.id, populate_existing=True)
            if row is None:
                raise failure("REPLY_UNPROVEN")
            requests = (
                await db.scalars(
                    select(ResearchEvent)
                    .where(
                        ResearchEvent.workspace_id == scope.id,
                        ResearchEvent.entity_id == charge.approval_id,
                        ResearchEvent.event_type == "harness.model_request",
                    )
                    .limit(2)
                )
            ).all()
            if len(requests) != 1:
                raise failure("REPLY_UNPROVEN")
            request = parse_document(requests[0].payload_json)
            proof = await historical_reply.prove(
                db,
                owner,
                run.id,
                row.attempt_id,
                charge.id,
                reply_hash=row.document_hash,
                binding_hash=request["binding_hash"],
                call_hash=request["call_hash"],
            )
            document = parse_document(proof.document_json)
            advisory = document["advisory"]
            if (
                not isinstance(advisory, dict)
                or set(advisory) != {"text", "tool_calls"}
                or (advisory["text"] is not None and not isinstance(advisory["text"], str))
                or not isinstance(advisory["tool_calls"], list)
            ):
                raise failure("REPLY_UNPROVEN")
            replies.append(
                {
                    "charge_id": str(charge.id),
                    "attempt_id": str(row.attempt_id),
                    "reply_receipt_hash": row.document_hash,
                    "request_hash": row.request_hash,
                    "execution_kind": document["execution_kind"],
                    "recorded_sequence": proof.recorded_sequence,
                    "resumed_sequence": proof.resumed_sequence,
                    "advisory": advisory,
                }
            )
        replies.sort(key=lambda item: item["recorded_sequence"])
        result = {
            "version": "research-saved-model-replies-v1",
            "run_id": str(run.id),
            "source": "persisted_advisory",
            "replies": replies,
            "reply_count": len(replies),
            "model_quality": "NOT_VERIFIED",
            "business_completion": "NOT_VERIFIED",
            "workspace_event_sequence": before[0],
            "workspace_event_head_sha256": before[1],
        }
        if (await db.execute(head_query)).one_or_none() != before:
            raise failure("REPLY_CHANGED_DURING_READ")
        if len(canonical_bytes(result)) > MAX_OUTPUT_BYTES:
            raise failure("REPLY_LIMIT")
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure("REPLY_UNPROVEN") from None
