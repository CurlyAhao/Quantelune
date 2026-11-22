"""Reader admission facts. Public activation waits for the actual owned runtime."""

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent, ResearchRun
from app.research import execution_lease, reader_contracts, reader_sources, reader_tools
from app.research.contracts import State, logical_hash, parse_document
from app.research.egress import failure
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger

EVENT = "reader.admitted"


def parameters_bound(run, parameters):
    """Legacy inputs remain readable; only new inputs commit the request key."""
    if not isinstance(parameters, dict):
        return False
    legacy = {"operation", "source_version"}
    if set(parameters) == legacy:
        return parameters.get("operation") == reader_contracts.OPERATION
    if set(parameters) != legacy | {"admission_key_hash"}:
        return False
    digest = parameters["admission_key_hash"]
    return (
        isinstance(digest, str)
        and len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest)
        and run.idempotency_key == "reader-admission:" + digest
    )


async def create(db, owner, session_id, request, gateway):
    request = reader_contracts.ReaderRunCreate.model_validate(request)
    proof = await reader_sources.prove(
        db,
        owner,
        session_id,
        request.selection,
        request.source_version,
        projection_version=request.projection_version,
    )
    key_hash = logical_hash(request.idempotency_key)
    run_key = "reader-admission:" + key_hash
    parameters = dict(
        operation=reader_contracts.view_operation(request.projection_version),
        source_version=request.source_version,
    )
    existing = await db.scalar(
        select(ResearchRun).where(
            ResearchRun.session_id == session_id, ResearchRun.idempotency_key == run_key
        )
    )
    # Retain a previously admitted legacy input exactly; never backfill a bare
    # legacy Run or relabel it as an event-bound fresh model launch.
    if existing is not None and parse_document(existing.input_json).get("parameters") == parameters:
        previous = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == existing.id,
                ResearchAttempt.number == existing.attempt_count,
            )
        )
        if await load(db, owner, existing, previous) is None:
            raise failure("INVOCATION_REQUIRED")
    else:
        parameters["admission_key_hash"] = key_hash
    run = await ledger.start_run(
        db,
        owner,
        session_id,
        reader_contracts.KIND,
        run_key,
        parameters,
    )
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    if attempt is None or attempt.state != run.state:
        raise ledger.error("STATE_INCONSISTENT")
    value = dict(
        version=request.projection_version,
        owner_id=str(owner),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        input_hash=run.input_hash,
        source_version=request.source_version,
        source_proof_hash=proof.document_hash,
        profile_hash=logical_hash(reader_contracts.view_profile(request.projection_version)),
        selection=request.selection.model_dump(mode="json"),
        tools=reader_tools.schemas(projection_version=request.projection_version),
    )
    old = await load(db, owner, run, attempt)
    if old is not None:
        if old != value:
            raise failure("INVOCATION_CONFLICT")
        return run
    if run.state != State.QUEUED.value or run.attempt_count != 1:
        raise failure("INVOCATION_ADMISSION_REFUSED")
    # The original Gateway proves known-secret disclosure and real production
    # price qualification before any question is committed. This does not send.
    prepared = await gateway.prepare(
        db, owner, run.id, request.selection, model_tools=value["tools"]
    )
    gateway.guard(prepared)
    SafeSerializer().check(value)
    _, scope = await ledger.run(db, run.id, owner)
    await ledger._append(db, scope, owner, "reader_invocation", attempt.id, EVENT, value)
    return run


async def load(db, owner, run, attempt):
    if attempt is None or attempt.run_id != run.id:
        raise failure("INVOCATION_BINDING_CHANGED")
    _, scope = await ledger.run(db, run.id, owner)
    rows = (
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
    if not rows:
        return None
    if len(rows) != 1:
        raise failure("INVOCATION_CONFLICT")
    event = rows[0]
    value = parse_document(event.payload_json)
    parameters = parse_document(run.input_json)["parameters"]
    projection_version = reader_contracts.view_version(parameters)
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "version",
            "owner_id",
            "run_id",
            "attempt_id",
            "input_hash",
            "source_version",
            "source_proof_hash",
            "profile_hash",
            "selection",
            "tools",
        }
        or type(value["version"]) is not int
        or value["version"] != projection_version
        or run.kind != reader_contracts.KIND
        or not parameters_bound(run, parameters)
        or event.entity_kind != "reader_invocation"
        or event.actor_id != owner
        or event.logical_hash != logical_hash(value)
        or value["owner_id"] != str(owner)
        or value["run_id"] != str(run.id)
        or value["attempt_id"] != str(attempt.id)
        or value["input_hash"] != run.input_hash
        or logical_hash(parse_document(run.input_json)) != run.input_hash
        or value["source_version"] != parameters["source_version"]
        or value["profile_hash"] != logical_hash(reader_contracts.view_profile(projection_version))
        or not isinstance(value["source_proof_hash"], str)
        or len(value["source_proof_hash"]) != 64
        or any(x not in "0123456789abcdef" for x in value["source_proof_hash"])
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    selected = reader_contracts.selection(value["selection"])
    if selected.model_dump(mode="json") != value["selection"]:
        raise failure("INVOCATION_BINDING_CHANGED")
    reader_tools.assert_tools(value["tools"], projection_version=projection_version)
    SafeSerializer().check(value)
    return value


async def source(db, owner, run):
    from app.research.replay import verify_business

    _, scope = await ledger.run(db, run.id, owner)
    await verify_business(db, owner, scope.id)
    await execution_lease._source(db, owner, run, mode="live")
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
        )
    )
    value = await load(db, owner, run, attempt)
    if value is None:
        raise failure("INVOCATION_REQUIRED")
    return await reader_sources.verify(
        db,
        owner,
        run.session_id,
        value["selection"],
        value["source_version"],
        value["source_proof_hash"],
        projection_version=value["version"],
    )
