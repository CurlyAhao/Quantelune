"""Original Reader producer identity; historical evidence is not death authority."""

import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import select

from app.models.research import ResearchEvent
from app.research.contracts import logical_hash, parse_document
from app.services import research_service as ledger

EVENT = "reader.claimed"
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class ReaderClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: StrictInt
    owner_id: str = Field(pattern=UUID_PATTERN)
    run_id: str = Field(pattern=UUID_PATTERN)
    attempt_id: str = Field(pattern=UUID_PATTERN)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    fence: StrictInt
    worker_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    producer_host: str = Field(pattern=r"^[0-9a-f]{64}$")
    producer_pid: StrictInt = Field(gt=0)
    producer_birth: str = Field(pattern=r"^(win:[1-9][0-9]*|linux:[0-9a-f-]{36}:[0-9]+)$")
    claimed_at_ms: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def original_epoch(self):
        if self.version != 1 or self.fence != 1:
            raise ValueError("Unsupported original Reader claim.")
        return self


def matches(value, owner, run, attempt, lease):
    return (
        value.owner_id == str(owner)
        and value.run_id == str(run.id)
        and value.attempt_id == str(attempt.id)
        and value.input_hash == run.input_hash
        and lease.owner_id == owner
        and lease.attempt_id == attempt.id
        and all(
            getattr(value, name) == getattr(lease, name)
            for name in (
                "fence",
                "worker_id",
                "producer_host",
                "producer_pid",
                "producer_birth",
                "claimed_at_ms",
            )
        )
    )


async def original(db, owner, run, attempt, lease):
    """No backfill: absent or changed original identity is unproven."""
    if attempt is None or lease is None:
        raise ledger.error("READER_CLAIM_UNPROVEN")
    _, scope = await ledger.run(db, run.id, owner)
    await ledger.verify_events(db, owner, scope.id)
    events = (
        await db.scalars(
            select(ResearchEvent).where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == run.id,
                ResearchEvent.event_type == EVENT,
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    claims = []
    try:
        for event in events:
            value = ReaderClaim.model_validate(parse_document(event.payload_json))
            if uuid.UUID(value.attempt_id) != attempt.id:
                continue
            if (
                event.actor_id != owner
                or event.entity_kind != "run"
                or event.logical_hash != logical_hash(value.model_dump(mode="json"))
                or not matches(value, owner, run, attempt, lease)
            ):
                raise ValueError("Reader original identity changed.")
            claims.append((event, value))
        if len(claims) != 1:
            raise ValueError("Reader original identity is absent or duplicated.")
    except (ValueError, TypeError):
        raise ledger.error("READER_CLAIM_UNPROVEN") from None
    return claims[0]
