"""Ordered P4 launch authorizations, proved against independent native rows."""

import uuid

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_execution import (
    ResearchExecutionDispatch,
    ResearchExecutionIntent,
    ResearchExecutionLease,
)
from app.models.research_planning import ResearchPlanAcceptance, ResearchPlanStep
from app.research import plan_versioning
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import logical_hash, parse_document
from app.services import research_service as ledger

EVENT = "protocol.launch_authorized"
STAGES = ("claim", "intent", "dispatch")
FIELDS = {
    "version",
    "owner_id",
    "session_id",
    "run_id",
    "attempt_id",
    "acceptance_id",
    "step_row_id",
    "step_id",
    "protocol",
    "run_revision",
    "input_hash",
    "fence",
    "stage",
}


async def record(db, owner, run, acceptance, step, plan, stage):
    if stage not in STAGES or not plan_versioning.is_v2(plan):
        raise ledger.error("PROTOCOL_LAUNCH_INVALID")
    attempt = await db.scalar(
        select(ResearchAttempt)
        .where(
            ResearchAttempt.run_id == run.id,
            ResearchAttempt.number == run.attempt_count,
        )
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise ledger.error("PROTOCOL_LAUNCH_INVALID")
    lease = await db.get(ResearchExecutionLease, attempt.id, populate_existing=True)
    if (stage == "claim" and lease is not None) or (stage != "claim" and lease is None):
        raise ledger.error("PROTOCOL_LAUNCH_INVALID")
    value = dict(
        version=1,
        owner_id=str(owner),
        session_id=str(run.session_id),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        acceptance_id=str(acceptance.id),
        step_row_id=str(step.id),
        step_id=step.step_id,
        protocol=plan_versioning.binding(plan),
        run_revision=run.revision,
        input_hash=run.input_hash,
        fence=lease.fence if lease else 1,
        stage=stage,
    )
    _, scope = await ledger.run(db, run.id, owner)
    await ledger._append(db, scope, owner, "attempt", attempt.id, EVENT, value)


async def verify_event(
    db,
    owner,
    event,
    saved_plans,
    acceptances,
    controls,
    children,
    runs,
    protocol_versions,
    protocol_current,
    seen,
) -> ContextSnapshot:
    value = parse_document(event.payload_json)
    if (
        type(value) is not dict
        or set(value) != FIELDS
        or type(value["version"]) is not int
        or value["version"] != 1
        or event.entity_kind != "attempt"
        or event.actor_id != owner
        or value["owner_id"] != str(owner)
        or value["attempt_id"] != str(event.entity_id)
        or value["stage"] not in STAGES
        or type(value["fence"]) is not int
        or value["fence"] <= 0
        or type(value["run_revision"]) is not int
        or event.logical_hash != logical_hash(value)
    ):
        raise ValueError("Protocol launch envelope changed.")
    run_id, session_id = uuid.UUID(value["run_id"]), uuid.UUID(value["session_id"])
    acceptance_id, step_id = uuid.UUID(value["acceptance_id"]), uuid.UUID(value["step_row_id"])
    current = runs.get(run_id)
    acceptance = await db.get(ResearchPlanAcceptance, acceptance_id, populate_existing=True)
    step = await db.get(ResearchPlanStep, step_id, populate_existing=True)
    attempt = await db.get(ResearchAttempt, event.entity_id, populate_existing=True)
    if (
        current is None
        or acceptance is None
        or step is None
        or attempt is None
        or acceptance_id not in acceptances
        or controls[acceptance_id]["cancel_requested"]
        or acceptance.owner_id != owner
        or acceptance.session_id != session_id
        or step.acceptance_id != acceptance_id
        or step.step_id != value["step_id"]
        or step.child_run_id != run_id
        or children.get(step_id) != run_id
        or current["session_id"] != str(session_id)
        or current["input_hash"] != value["input_hash"]
        or current["revision"] != value["run_revision"]
        or current["state"] != ("queued" if value["stage"] == "claim" else "running")
        or attempt.run_id != run_id
        or attempt.number != current["attempt_count"]
    ):
        raise ValueError("Protocol launch route, lifecycle or native state changed.")
    saved = saved_plans.get(str(acceptance.plan_version_id))
    if saved is None:
        raise ValueError("Protocol launch has no prior saved Plan.")
    plan = plan_versioning.parse(saved["plan"])
    if not plan_versioning.is_v2(plan) or value["protocol"] != plan_versioning.binding(plan):
        raise ValueError("Protocol launch input reference changed.")
    plan_versioning.as_of(
        plan, saved.get("protocol"), protocol_versions, protocol_current.get(session_id)
    )
    stage, key = value["stage"], (event.entity_id, value["stage"])
    if key in seen or (
        stage != "claim" and (event.entity_id, STAGES[STAGES.index(stage) - 1]) not in seen
    ):
        raise ValueError("Protocol launch stage duplicated or out of order.")
    if stage != "claim" and value["fence"] != seen[(event.entity_id, "claim")]:
        raise ValueError("Protocol launch changed the original claim fence.")
    lease = await db.get(ResearchExecutionLease, event.entity_id, populate_existing=True)
    intent = await db.get(ResearchExecutionIntent, event.entity_id, populate_existing=True)
    dispatch = await db.get(ResearchExecutionDispatch, event.entity_id, populate_existing=True)
    if lease is None or (stage == "claim" and value["fence"] != 1):
        raise ValueError("Protocol claim has no actual lease.")
    if stage in {"intent", "dispatch"} and (
        intent is None or intent.fence != value["fence"] or intent.input_hash != value["input_hash"]
    ):
        raise ValueError("Protocol intention has no matching independent row.")
    if stage == "dispatch" and (
        dispatch is None
        or dispatch.fence != value["fence"]
        or intent is None
        or dispatch.intent_hash != intent.intent_hash
    ):
        raise ValueError("Protocol dispatch has no matching independent row.")
    seen[key] = value["fence"]
    return ContextSnapshot.model_validate(saved["context"])


async def verify_sets(db, saved_plans, acceptance_rows, steps, seen, recovered_fences) -> None:
    """A native row cannot silently appear without its ordered P4 authorization event."""
    by_id = {row.id: row for row in acceptance_rows}
    expected = set()
    for step in steps:
        if step.child_run_id is None:
            continue
        acceptance = by_id[step.acceptance_id]
        saved = saved_plans.get(str(acceptance.plan_version_id))
        if saved is None or not plan_versioning.is_v2(plan_versioning.parse(saved["plan"])):
            continue
        attempts = (
            await db.scalars(
                select(ResearchAttempt).where(
                    ResearchAttempt.run_id == step.child_run_id,
                )
            )
        ).all()
        for attempt in attempts:
            lease = await db.get(ResearchExecutionLease, attempt.id, populate_existing=True)
            if lease is not None:
                claim = seen.get((attempt.id, "claim"))
                if (
                    claim is None
                    or (not lease.result_only and lease.fence != claim)
                    or (
                        lease.result_only
                        and (
                            lease.fence != recovered_fences.get(attempt.id) or lease.fence <= claim
                        )
                    )
                ):
                    raise ValueError("Protocol original claim and final lease fence differ.")
            for stage, model in zip(
                STAGES,
                (ResearchExecutionLease, ResearchExecutionIntent, ResearchExecutionDispatch),
                strict=True,
            ):
                if await db.get(model, attempt.id, populate_existing=True) is not None:
                    expected.add((attempt.id, stage))
    if expected != set(seen):
        raise ValueError("Protocol launch event and native row sets differ.")
