"""Read-only accepted-v2 input proof for native signatures and historical closeout."""

from sqlalchemy import select

from app.models.research import ResearchEvent
from app.models.research_planning import ResearchPlanAcceptance, ResearchPlanStep
from app.research import plan_acceptance, plan_versioning, protocol_history
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import logical_hash, parse_document
from app.services import research_capability_service as capabilities
from app.services import research_service as ledger


async def prove(db, owner, run) -> dict:
    captured = parse_document(run.input_json)
    step = await db.scalar(
        select(ResearchPlanStep)
        .where(
            ResearchPlanStep.child_run_id == run.id,
        )
        .execution_options(populate_existing=True)
    )
    acceptance = (
        await db.get(ResearchPlanAcceptance, step.acceptance_id, populate_existing=True)
        if step
        else None
    )
    if (
        step is None
        or acceptance is None
        or acceptance.owner_id != owner
        or acceptance.session_id != run.session_id
    ):
        raise ledger.error("PLAN_CHILD_CHANGED")
    _, _, saved = await plan_acceptance.version(
        db, owner, run.session_id, acceptance.plan_version_id
    )
    plan = plan_versioning.parse(saved["plan"])
    if not plan_versioning.is_v2(plan):
        raise ledger.error("PLAN_CHILD_CHANGED")
    _, scope = await ledger.run(db, run.id, owner)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == acceptance.id,
                ResearchEvent.event_type == plan_acceptance.EVENT,
            )
            .limit(2)
        )
    ).all()
    if len(events) != 1:
        raise ledger.error("PLAN_CHILD_CHANGED")
    await plan_acceptance.verify_event(db, events[0], owner, {saved["version_id"]: saved})
    await protocol_history.before_plan(db, events[0], owner, saved)
    request = capabilities.native_request(
        plan,
        step.step_id,
        ContextSnapshot.model_validate(saved["context"]),
        protocol_snapshot=saved.get("protocol"),
    )
    if (
        run.kind != request["kind"]
        or logical_hash(captured) != run.input_hash
        or captured.get("kind") != run.kind
        or captured.get("parameters") != request["parameters"]
        or captured.get("attachments") != saved["attachments"]
    ):
        raise ledger.error("PLAN_CHILD_CHANGED")
    return {"acceptance": acceptance, "step": step, "saved": saved, "plan": plan}
