"""Freeze one planner question/schema selection before any model proposal/send."""

from sqlalchemy import select

from app.models.research import ResearchEvent
from app.models.research_planning import ResearchPlannerThread
from app.research import planning_lease
from app.research.contracts import State, logical_hash, parse_document
from app.research.egress import DisclosureSelection, failure
from app.research.harness_serde import SafeSerializer
from app.research.planner_protocol import assert_tools
from app.services import research_service as ledger

EVENT = "planning.invocation_registered"


async def register(db, token, binding, selection, tools, gateway):
    from app.research.harness_binding import _verify
    from app.research.replay import verify_business

    run, scope, lease, _ = await planning_lease.validate(db, token)
    await verify_business(db, token.owner_id, scope.id)
    if (
        binding.purpose != "planner"
        or binding.attempt_id != token.attempt_id
        or lease.pause_requested
    ):
        raise failure("INVOCATION_ADMISSION_REFUSED")
    thread = await db.get(ResearchPlannerThread, binding.id, populate_existing=True)
    if thread is None or any(
        getattr(thread, name) != getattr(binding, name) for name in binding.__dataclass_fields__
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    _verify(thread, token, run)
    selection = DisclosureSelection.model_validate(selection)
    tools = assert_tools(tools)
    from app.research import planner_v2
    from app.research.planner_protocol import assert_tools_v2

    snapshot = planner_v2.captured(run)
    if snapshot is not None:
        tools = assert_tools_v2(tools)
        planner_v2.selected(snapshot, selection)
    if selection.purpose != "planning" or selection.scope != "metadata_only" or selection.rows:
        raise failure("PLANNER_DISCLOSURE_REFUSED")
    value = dict(
        version=2 if snapshot is not None else 1,
        owner_id=str(token.owner_id),
        run_id=str(run.id),
        attempt_id=str(token.attempt_id),
        input_hash=run.input_hash,
        binding_id=str(binding.id),
        binding_hash=binding.binding_hash,
        selection=selection.model_dump(mode="json"),
        tools=tools,
    )
    if snapshot is not None:
        value.update(
            protocol=planner_v2.metadata(snapshot), profile_hash=logical_hash(planner_v2.PROFILE)
        )
    SafeSerializer().check(value)
    old = await load(db, token.owner_id, run, token.attempt_id)
    if old is not None:
        if old != value:
            raise failure("INVOCATION_CONFLICT")
        return old
    if run.state != State.RUNNING.value:
        raise failure("INVOCATION_ADMISSION_REFUSED")
    prepared = await gateway.prepare(db, token.owner_id, run.id, selection, model_tools=tools)
    gateway.guard(prepared)
    await ledger._append(
        db, scope, token.owner_id, "planner_invocation", token.attempt_id, EVENT, value
    )
    return value


async def load(db, owner, run, attempt_id):
    from uuid import UUID

    from app.research.harness_binding import document, profile

    _, scope = await ledger.run(db, run.id, owner)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == attempt_id,
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
    fields = {
        "version",
        "owner_id",
        "run_id",
        "attempt_id",
        "input_hash",
        "binding_id",
        "binding_hash",
        "selection",
        "tools",
    }
    if snapshot is not None:
        fields |= {"protocol", "profile_hash"}
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or type(value["version"]) is not int
        or value["version"] != (2 if snapshot is not None else 1)
        or event.entity_kind != "planner_invocation"
        or event.actor_id != owner
        or value["owner_id"] != str(owner)
        or value["run_id"] != str(run.id)
        or value["attempt_id"] != str(attempt_id)
        or value["input_hash"] != run.input_hash
        or event.logical_hash != logical_hash(value)
        or run.kind != "planner"
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    thread = await db.get(ResearchPlannerThread, UUID(value["binding_id"]), populate_existing=True)
    if (
        thread is None
        or thread.owner_id != owner
        or thread.run_id != run.id
        or thread.attempt_id != attempt_id
        or thread.input_hash != run.input_hash
        or thread.purpose != "planner"
        or thread.binding_hash != value["binding_hash"]
        or thread.profile_hash
        != logical_hash(profile("planner", run.kind, parse_document(run.input_json)["parameters"]))
        or thread.binding_hash != logical_hash(document(thread))
    ):
        raise failure("INVOCATION_BINDING_CHANGED")
    selection = DisclosureSelection.model_validate(value["selection"])
    if selection.purpose != "planning" or selection.scope != "metadata_only" or selection.rows:
        raise failure("PLANNER_DISCLOSURE_REFUSED")
    if assert_tools(value["tools"]) != value["tools"]:
        raise failure("INVOCATION_BINDING_CHANGED")
    if snapshot is not None:
        from app.research.planner_protocol import assert_tools_v2

        if (
            value["protocol"] != planner_v2.metadata(snapshot)
            or value["profile_hash"] != thread.profile_hash
            or assert_tools_v2(value["tools"]) != value["tools"]
        ):
            raise failure("INVOCATION_BINDING_CHANGED")
        planner_v2.selected(snapshot, selection)
        await planner_v2.before_event(db, event, owner, run)
    SafeSerializer().check(value)
    return value
