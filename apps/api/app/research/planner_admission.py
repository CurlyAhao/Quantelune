"""Server-frozen queued planner selection; no client schema or runtime is accepted."""

from langchain.agents.middleware import TodoListMiddleware
from langchain_core.utils.function_calling import convert_to_openai_tool
from sqlalchemy import select

from app.models.research import ResearchEvent, ResearchRun
from app.research import planning_lease
from app.research.contracts import State, logical_hash, parse_document
from app.research.egress import DisclosureSelection, credentials, failure
from app.research.harness_serde import SafeSerializer
from app.research.planner_tools import build_tools
from app.services import research_service as ledger

EVENT = "planning.admitted"


def schemas():
    async def unavailable(*_args):
        raise failure("INVOCATION_REQUIRED")

    tools = build_tools(unavailable) + list(TodoListMiddleware().tools)
    return sorted(
        [convert_to_openai_tool(tool) for tool in tools], key=lambda item: item["function"]["name"]
    )


def schemas_v2():
    from app.research.planner_tools import build_tools_v2

    async def unavailable(*_args):
        raise failure("INVOCATION_REQUIRED")

    tools = build_tools_v2(unavailable) + list(TodoListMiddleware().tools)
    return sorted(
        [convert_to_openai_tool(tool) for tool in tools], key=lambda item: item["function"]["name"]
    )


async def create(
    db, owner, session_id, key, source_version, selection, gateway, *, protocol_request=None
):
    selection = DisclosureSelection.model_validate(selection)
    if selection.purpose != "planning" or selection.scope != "metadata_only" or selection.rows:
        raise failure("PLANNER_DISCLOSURE_REFUSED")
    if credentials(key):
        raise failure("INVOCATION_SECRET")
    from app.research.execution_lease import _boundary

    await _boundary(db, owner)
    await ledger.session(db, session_id, owner, mode="live")
    snapshot = None
    if protocol_request is not None:
        from app.research import planner_v2
        from app.services import research_protocol_service as protocols

        snapshot = await protocols.require_active(
            db,
            owner,
            session_id,
            protocol_request.protocol_version_id,
            protocol_request.protocol_hash,
            protocol_request.protocol_generation,
        )
        planner_v2.selected(snapshot, selection)
        if source_version != snapshot["version"]["protocol"]["source_version"]:
            raise failure("PLANNER_PROTOCOL_CHANGED")
    admission_key = "planner-admission:" + logical_hash(key)
    from app.research.planner_wire import CURRENT_VERSION as CURRENT_WIRE_VERSION

    wire_version = CURRENT_WIRE_VERSION if snapshot is not None else None
    existing = await db.scalar(
        select(ResearchRun).where(
            ResearchRun.session_id == session_id, ResearchRun.idempotency_key == admission_key
        )
    )
    if existing is not None:
        # Prove the old admission before preserving its exact input generation.
        # start_run still checks complete current bindings/parameters/hash below.
        previous = await load(db, owner, existing)
        if previous is None:
            raise failure("INVOCATION_ADMISSION_REFUSED")
        from app.research import planner_v2

        wire_version = planner_v2.wire_version(existing)
    run = await planning_lease.start(
        db,
        owner,
        session_id,
        admission_key,
        source_version,
        protocol=snapshot,
        wire_version=wire_version,
    )
    old = await load(db, owner, run)
    value = dict(
        version=2 if snapshot is not None else 1,
        owner_id=str(owner),
        run_id=str(run.id),
        input_hash=run.input_hash,
        source_version=source_version,
        selection=selection.model_dump(mode="json"),
        tools=schemas_v2() if snapshot is not None else schemas(),
    )
    if snapshot is not None:
        value.update(protocol=snapshot, profile_hash=logical_hash(planner_v2.PROFILE))
    SafeSerializer().check(value)
    if old is not None:
        if old != value:
            raise failure("INVOCATION_CONFLICT")
        return run
    if run.state != State.QUEUED.value or run.attempt_count != 1:
        raise failure("INVOCATION_ADMISSION_REFUSED")
    prepared = await gateway.prepare(db, owner, run.id, selection, model_tools=value["tools"])
    gateway.guard(prepared)  # Price/source/owned-secret proof precedes saved question.
    _, scope = await ledger.run(db, run.id, owner)
    await ledger._append(db, scope, owner, "run", run.id, EVENT, value)
    return run


async def load(db, owner, run):
    _, scope = await ledger.run(db, run.id, owner)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == run.id,
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
    value = parse_document(event.payload_json)
    from app.research import planner_v2

    snapshot = planner_v2.captured(run)
    fields = {"version", "owner_id", "run_id", "input_hash", "source_version", "selection", "tools"}
    if snapshot is not None:
        fields |= {"protocol", "profile_hash"}
    if (
        set(value) != fields
        or type(value["version"]) is not int
        or value["version"] != (2 if snapshot is not None else 1)
        or event.entity_kind != "run"
        or event.actor_id != owner
        or value["owner_id"] != str(owner)
        or value["run_id"] != str(run.id)
        or value["input_hash"] != run.input_hash
        or event.logical_hash != logical_hash(value)
        or run.kind != "planner"
        or value["source_version"] != parse_document(run.input_json)["parameters"]["source_version"]
        or value["tools"] != (schemas_v2() if snapshot is not None else schemas())
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    selected = DisclosureSelection.model_validate(value["selection"])
    if selected.purpose != "planning" or selected.scope != "metadata_only" or selected.rows:
        raise failure("PLANNER_DISCLOSURE_REFUSED")
    if snapshot is not None:
        if value["protocol"] != snapshot or value["profile_hash"] != logical_hash(
            planner_v2.PROFILE
        ):
            raise failure("INVOCATION_BINDING_CHANGED")
        planner_v2.selected(snapshot, selected)
        await planner_v2.before_event(db, event, owner, run)
    SafeSerializer().check(value)
    return value
