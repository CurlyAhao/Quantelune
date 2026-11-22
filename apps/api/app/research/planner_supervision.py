"""Official planner graph under actual lease, approval and durable receipt authority."""

import asyncio
import uuid

from langgraph.types import Command
from langsmith import tracing_context
from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.research import ResearchAttempt
from app.models.research_planning import ResearchPlanReceipt
from app.research import (
    advisory_receipt,
    harness_binding,
    plan_receipt,
    planner_finish,
    planner_invocation,
    planner_unresolved,
    planning_control,
    planning_lease,
)
from app.research.contracts import State
from app.research.egress import DisclosureSelection, failure
from app.research.harness_runtime import open_saver
from app.research.planner_harness import PlannerHarnessAdapter
from app.research.planner_model import PlannerModel
from app.research.planner_tools import PlannerTools, build_tools
from app.services import research_security_service as security
from app.services import research_service as ledger


async def boundary(factory, token, stopping):
    while True:
        if stopping.is_set():
            raise asyncio.CancelledError
        async with factory() as db:
            run, _, lease, _ = await planning_lease.validate(
                db, token, running=False, current_source=False
            )
            attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
            if await planning_control.cancelled(db, token.owner_id, run, attempt):
                await planning_control.acknowledge(db, token)
                await db.commit()
                raise asyncio.CancelledError
            if lease.pause_requested:
                await planning_control.acknowledge(db, token)
                await db.commit()
            else:
                requested = await planning_control.latest(db, token.owner_id, run, attempt)
                if requested is not None and requested["action"] == "resume":
                    await planning_control.acknowledge(db, token)
                    await db.commit()
                return
        await asyncio.sleep(0.1)


async def run(factory, token, gateway, *, stopping):
    from app.research.planner_admission import load

    binding = await harness_binding.existing(factory, token, "planner")
    if binding is None:
        binding = await harness_binding.bind(factory, token, "planner")
    async with factory() as db:
        current, _ = await ledger.run(db, token.run_id, token.owner_id)
        completed = current.state == State.COMPLETED.value
        invocation = await planner_invocation.load(db, token.owner_id, current, token.attempt_id)
        if invocation is None:
            admission = await load(db, token.owner_id, current)
            if admission is None:
                raise failure("INVOCATION_REQUIRED")
            await planner_invocation.register(
                db, token, binding, admission["selection"], admission["tools"], gateway
            )
            await db.commit()
            invocation = await planner_invocation.load(
                db, token.owner_id, current, token.attempt_id
            )
    from app.research import planner_v2
    from app.research.contracts import logical_hash
    from app.research.planner_tools import build_tools_v2

    v2 = binding.profile_hash == logical_hash(planner_v2.PROFILE)
    actual_tools = PlannerTools(factory, token, version=2 if v2 else 1)
    tools = (build_tools_v2 if v2 else build_tools)(actual_tools.safe_call)
    model = PlannerModel(
        factory,
        token,
        binding,
        gateway,
        DisclosureSelection.model_validate(invocation["selection"]),
        invocation["tools"],
    )
    adapter = PlannerHarnessAdapter()
    config = dict(configurable=dict(thread_id=binding.thread_id), callbacks=[], recursion_limit=12)

    async def invoke(command, *, initial=False, replay_reference=None):
        if not completed:
            await boundary(factory, token, stopping)
        with tracing_context(enabled=False):
            async with open_saver(factory, binding, token) as saver:
                graph = await adapter.abuild(model.bridge(), tools, saver)
                with model._replaying(replay_reference):
                    return await graph.ainvoke(command, config, durability="sync")

    async with open_saver(factory, binding, token) as saver:
        graph = await adapter.abuild(model.bridge(), tools, saver)
        snapshot = await graph.aget_state(config)
        interrupts = snapshot.interrupts or tuple(
            item for task in snapshot.tasks for item in task.interrupts
        )
        if snapshot.values and not snapshot.next and not interrupts:
            result = snapshot.values
        elif interrupts:
            result = {"__interrupt__": interrupts}
        else:
            result = None
    if result is None:
        result = await invoke(
            None
            if snapshot.next
            else dict(messages=[dict(role="user", content=model.selection.question)])
        )
    while result.get("__interrupt__"):
        interrupts = result["__interrupt__"]
        if len(interrupts) != 1:
            raise failure("MODEL_RESUME_REFUSED")
        reference = interrupts[0].value
        if (
            not isinstance(reference, dict)
            or set(reference)
            != {"kind", "approval_id", "request_hash", "call_hash", "binding_hash"}
            or reference["kind"] != "research_approval"
            or reference["binding_hash"] != binding.binding_hash
        ):
            raise failure("MODEL_RESUME_REFUSED")
        while True:
            if not completed:
                await boundary(factory, token, stopping)
            async with factory() as db:
                if not completed:
                    current, _, lease, _ = await planning_lease.validate(db, token, running=False)
                approval = await security.approval(
                    db, token.owner_id, uuid.UUID(reference["approval_id"])
                )
                if (
                    approval.run_id != token.run_id
                    or approval.request_hash != reference["request_hash"]
                ):
                    raise failure("MODEL_BINDING_CHANGED")
                approved, charge = await model._rows(db, reference["call_hash"])
                if approved is None or approved.id != approval.id:
                    raise failure("MODEL_BINDING_CHANGED")
                if charge is not None:
                    if await advisory_receipt.read(db, token.owner_id, approval, charge) is None:
                        raise failure("REPLY_UNRECOVERABLE")
                    ready = True
                else:
                    if completed:
                        raise failure("REPLY_UNRECOVERABLE")
                    if approval.state in {"denied", "revoked"}:
                        raise failure("APPROVAL_REQUIRED")
                    if security.clock_ms() >= approval.expires_at_ms:
                        raise failure("APPROVAL_EXPIRED")
                    ready = approval.state == "approved"
            if ready:
                break
            await asyncio.sleep(0.1)
        result = await invoke(Command(resume=reference), replay_reference=reference)
    async with factory() as db:
        receipt = await db.scalar(
            select(ResearchPlanReceipt).where(ResearchPlanReceipt.attempt_id == token.attempt_id)
        )
    if receipt is not None:
        await model.verify_state(result)
    async with factory() as db:
        current, _ = await ledger.run(db, token.run_id, token.owner_id)
        receipt = await db.scalar(
            select(ResearchPlanReceipt).where(ResearchPlanReceipt.attempt_id == token.attempt_id)
        )
        if receipt is not None and current.state != State.COMPLETED.value:
            await plan_receipt.finish(db, token)
            await db.commit()
    async with open_saver(factory, binding, token) as saver:
        graph = await adapter.abuild(model.bridge(), tools, saver)
        if receipt is None:
            while True:
                await boundary(factory, token, stopping)
                try:
                    return await planner_unresolved.record(
                        factory, token, binding, graph, saver, model
                    )
                except StatForgeError as error:
                    if error.error_code not in {
                        "RESEARCH_MODEL_PAUSED",
                        "RESEARCH_EGRESS_MODEL_PAUSED",
                    }:
                        raise
                    # A concurrent human pause retains its original boundary;
                    # only re-prove the fixed state after resume, never invoke.
        return await planner_finish.record(factory, token, binding, graph, saver, model)
