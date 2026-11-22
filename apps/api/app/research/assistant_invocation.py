"""Explicit server-owned formal invocation, attached to one frozen business attempt."""

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.research import execution_lease, execution_spec, harness_binding
from app.research.contracts import State, logical_hash, parse_document
from app.research.egress import DisclosureSelection, failure
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger

EVENT = "harness.invocation_registered"


async def register(db, owner, run_id, selection, gateway):
    await execution_lease._boundary(db, owner)
    run, scope = await ledger.run(db, run_id, owner)
    await execution_lease._source(db, owner, run, mode="live")
    if run.kind not in {
        "quality",
        "statistics",
        "ml",
        "workflow",
    }:
        raise failure("INVOCATION_ADMISSION_REFUSED")
    if parse_document(run.input_json)["parameters"].get("operation") != "execute":
        raise failure("INVOCATION_ADMISSION_REFUSED")
    attempt = await db.scalar(
        select(ResearchAttempt)
        .where(ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count)
        .execution_options(populate_existing=True)
    )
    if attempt is None or attempt.state != run.state:
        raise failure("INVOCATION_BINDING_CHANGED")
    selection = DisclosureSelection.model_validate(selection)
    existing = await load(db, owner, run, attempt)
    if existing is not None:
        if existing["selection"] != selection.model_dump(mode="json"):
            raise failure("INVOCATION_CONFLICT")
        return existing  # No new proposal/send and no mutation of the frozen record.
    if run.state != State.QUEUED.value:
        raise failure("INVOCATION_ADMISSION_REFUSED")
    # Validate actual ownership, source and known credential disclosure BEFORE
    # persisting the question. No credential or Gateway becomes event content.
    prepared = await gateway.prepare(db, owner, run.id, selection)
    gateway.guard(prepared)
    document = {
        "version": 2,
        "owner_id": str(owner),
        "run_id": str(run.id),
        "attempt_id": str(attempt.id),
        "input_hash": run.input_hash,
        "profile_hash": logical_hash(harness_binding.PROFILES["assistant"]),
        "selection": selection.model_dump(mode="json"),
        "execution_hash": await execution_spec.signature(db, owner, run),
    }
    if selection.artifact_ids:
        from app.services import research_context_service as contexts

        context = await contexts.snapshot(db, owner, run.session_id)
        document["version"] = 3
        document["context_source_version"] = context.source_version
    SafeSerializer().check(document)
    await ledger._append(db, scope, owner, "harness_invocation", attempt.id, EVENT, document)
    return document


async def load(db, owner, run, attempt):
    _, scope = await ledger.run(db, run.id, owner)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == attempt.id,
                ResearchEvent.event_type == EVENT,
            )
            .limit(2)
        )
    ).all()
    if not events:
        return None
    if len(events) != 1:
        raise failure("INVOCATION_CONFLICT")
    event = events[0]
    document = parse_document(event.payload_json)
    expected = {
        "version",
        "owner_id",
        "run_id",
        "attempt_id",
        "input_hash",
        "profile_hash",
        "selection",
    }
    version = document.get("version") if isinstance(document, dict) else None
    if version in {2, 3}:
        expected.add("execution_hash")
    if version == 3:
        expected.add("context_source_version")
    if (
        not isinstance(document, dict)
        or set(document) != expected
        or version not in {1, 2, 3}
        or event.entity_kind != "harness_invocation"
        or event.actor_id != owner
        or event.logical_hash != logical_hash(document)
        or document["owner_id"] != str(owner)
        or document["run_id"] != str(run.id)
        or document["attempt_id"] != str(attempt.id)
        or attempt.run_id != run.id
        or document["input_hash"] != run.input_hash
        or document["profile_hash"] != logical_hash(harness_binding.PROFILES["assistant"])
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    selection = DisclosureSelection.model_validate(document["selection"])
    if version == 3 and (
        not selection.artifact_ids
        or not isinstance(document["context_source_version"], str)
        or len(document["context_source_version"]) != 64
        or any(
            character not in "0123456789abcdef" for character in document["context_source_version"]
        )
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    if selection.model_dump(mode="json") != document["selection"]:
        raise failure("INVOCATION_BINDING_CHANGED")
    SafeSerializer().check(document)
    return document


async def verify_current(db, owner, run, attempt, invocation=None):
    if invocation is None:
        invocation = await load(db, owner, run, attempt)
    if invocation is None or invocation["version"] not in {2, 3}:
        raise failure("INVOCATION_SPEC_REQUIRED")
    if invocation["execution_hash"] != await execution_spec.signature(db, owner, run):
        raise failure("INVOCATION_SPEC_CHANGED")
    if invocation["version"] == 3:
        from app.services import research_context_service as contexts

        await contexts.snapshot(
            db,
            owner,
            run.session_id,
            expected_source_version=invocation["context_source_version"],
        )
    return invocation
