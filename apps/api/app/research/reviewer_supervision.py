"""Owned Reviewer SDK execution; receipt-only phases never re-enter the graph."""

import asyncio
import uuid

from langgraph.types import Command
from langsmith import tracing_context
from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_planning import ResearchPlanningLease
from app.research import (
    harness_binding,
    reviewer_admission,
    reviewer_control,
    reviewer_finish,
    reviewer_lease,
    reviewer_results,
    reviewer_sdk_finish,
    reviewer_sdk_tasks,
    reviewer_transport,
)
from app.research.contracts import parse_document
from app.research.egress import DisclosureSelection, failure
from app.research.harness_runtime import open_saver
from app.research.reviewer_harness import PAUSED_CODES, ReviewerHarnessAdapter
from app.research.reviewer_model import ReviewerModel
from app.research.reviewer_runtime import ReviewerRuntime
from app.research.supervisor import _await_terminal
from app.services import research_security_service as security
from app.services import research_service as ledger


def drained(token):
    reviewer_sdk_tasks.drained(token)
    reviewer_transport.drained(token)


async def boundary(factory, token, stopping):
    """No request becomes an ACK until SDK callbacks, saver and transport drained."""
    drained(token)
    while True:
        if stopping.is_set():
            raise asyncio.CancelledError
        async with factory() as db:
            run, _ = await ledger.run(db, token.run_id, token.owner_id)
            if run.state == "completed":
                await reviewer_finish.terminal_claim(db, token)
                return
            run, _, lease, _ = await reviewer_lease.validate(
                db, token, running=False, current_source=False
            )
            attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
            if await reviewer_control.cancelled(db, token.owner_id, run, attempt):
                await reviewer_control.acknowledge(db, token)
                await db.commit()
                raise asyncio.CancelledError
            requested = await reviewer_control.latest(db, token.owner_id, run, attempt)
            if lease.pause_requested:
                await reviewer_control.acknowledge(db, token)
                await db.commit()
            else:
                if requested is not None and requested["action"] == "resume":
                    await reviewer_control.acknowledge(db, token)
                    await db.commit()
                return
        await asyncio.sleep(0.1)


async def controlled(factory, token, binding, stopping, operation):
    await boundary(factory, token, stopping)
    task = asyncio.create_task(
        reviewer_sdk_tasks.invoke(factory, token, binding, operation),
        name="reviewer-owned-sdk-operation",
    )
    original_error = None
    try:
        while not task.done():
            if stopping.is_set():
                raise asyncio.CancelledError
            async with factory() as db:
                run, _ = await ledger.run(db, token.run_id, token.owner_id)
                attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
                if await reviewer_control.cancelled(db, token.owner_id, run, attempt):
                    raise asyncio.CancelledError
                lease = await db.get(
                    ResearchPlanningLease, token.attempt_id, populate_existing=True
                )
                if lease is None:
                    raise ledger.error("EXECUTION_FENCE_LOST")
                if lease.pause_requested:
                    raise failure("MODEL_PAUSED")
            await asyncio.wait({task}, timeout=0.1)
        return task.result()
    except BaseException as error:
        original_error = error
        raise
    finally:

        async def drain():
            if not task.done():
                task.cancel()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            drained(token)
            if (
                isinstance(outcome, BaseException)
                and not isinstance(outcome, asyncio.CancelledError)
                and outcome is not original_error
            ):
                raise outcome

        await _await_terminal(asyncio.create_task(drain(), name="reviewer-sdk-drain"))


def reference(interrupts, binding):
    if len(interrupts) != 1:
        raise failure("MODEL_RESUME_REFUSED")
    value = interrupts[0].value
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "approval_id", "request_hash", "call_hash", "binding_hash"}
        or value["kind"] != "research_approval"
        or value["binding_hash"] != binding.binding_hash
    ):
        raise failure("MODEL_RESUME_REFUSED")
    return value


async def run(factory, token, *, gateway_factory, stopping):
    async with factory() as db:
        current, _ = await ledger.run(db, token.run_id, token.owner_id)
        _, _, receipt, finished = await reviewer_results.inspect(db, token.owner_id, current)
        completed = current.state == "completed"
    if finished is not None:
        return finished  # History only; no lease takeover or SDK activity.
    if receipt is not None or completed:
        binding = await harness_binding.existing(factory, token, "assistant")
        if binding is None:
            raise failure("REVIEWER_CHECKPOINT_UNPROVEN")
        drained(token)
        await boundary(factory, token, stopping)
        if not completed:
            async with factory() as db:
                await reviewer_finish.finish(db, token)
                await db.commit()
        return await reviewer_sdk_finish.record(factory, token, binding)
    if token.fence != 1:
        raise failure("REVIEWER_RECEIPT_ONLY")
    while True:
        await boundary(factory, token, stopping)
        try:
            binding = await harness_binding.existing(factory, token, "assistant")
            new_binding = binding is None
            if binding is None:
                binding = await harness_binding.bind(factory, token, "assistant")
            break
        except StatForgeError as error:
            if error.error_code not in PAUSED_CODES:
                raise
            # Pause can race any awaited authorize/bind, including preflight.
            await boundary(factory, token, stopping)
    async with factory() as db:
        current, _ = await ledger.run(db, token.run_id, token.owner_id)
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        admission = await reviewer_admission.load(db, token.owner_id, current, attempt)
        if admission is None:
            raise failure("INVOCATION_REQUIRED")
    model = ReviewerModel(
        factory,
        token,
        binding,
        gateway_factory(),
        DisclosureSelection.model_validate(admission["selection"]),
        admission["tools"],
    )
    backend = ReviewerRuntime(factory, token, binding)
    adapter = ReviewerHarnessAdapter()
    config = dict(configurable=dict(thread_id=binding.thread_id), callbacks=[], recursion_limit=12)
    resumed_controls: set[int] = set()

    async def invoke(value):
        needs_input_check = False

        async def input_checkpoint_exists():
            with tracing_context(enabled=False):
                async with open_saver(factory, binding, token) as saver:
                    actual = await saver.aget_tuple(config)
            if actual is not None:
                return True
            # An absent checkpoint cannot erase earlier model/tool work. Only a
            # genuinely unstarted first input may be supplied after the pause.
            async with factory() as db:
                _, scope = await ledger.run(db, token.run_id, token.owner_id)
                rows = (
                    await db.scalars(
                        select(ResearchEvent)
                        .where(
                            ResearchEvent.workspace_id == scope.id,
                            ResearchEvent.event_type.in_(
                                ["harness.model_request", "reviewer.tool_recorded"]
                            ),
                        )
                        .limit(10001)
                    )
                ).all()
                if len(rows) > 10000 or any(
                    parse_document(row.payload_json).get("binding_hash") == binding.binding_hash
                    for row in rows
                ):
                    raise failure("REVIEWER_CHECKPOINT_UNPROVEN")
            return False

        async def operation():
            with tracing_context(enabled=False):
                async with open_saver(factory, binding, token) as saver:
                    graph = await adapter.abuild(model.bridge(), backend.tools(), saver)
                    return await graph.ainvoke(value, config, durability="sync")

        while True:
            try:
                if needs_input_check:
                    if await controlled(factory, token, binding, stopping, input_checkpoint_exists):
                        value = None
                    needs_input_check = False
                return await controlled(factory, token, binding, stopping, operation)
            except StatForgeError as error:
                if error.error_code not in PAUSED_CODES:
                    raise
                await boundary(factory, token, stopping)
                async with factory() as db:
                    current, _ = await ledger.run(db, token.run_id, token.owner_id)
                    attempt = await db.get(
                        ResearchAttempt, token.attempt_id, populate_existing=True
                    )
                    requested = await reviewer_control.latest(db, token.owner_id, current, attempt)
                    if (
                        requested is None
                        or requested["action"] != "resume"
                        or requested["control_index"] in resumed_controls
                        or len(resumed_controls) >= reviewer_control.LIMIT
                    ):
                        raise failure("MODEL_RESUME_REFUSED") from None
                    resumed_controls.add(requested["control_index"])
                if isinstance(value, dict):
                    needs_input_check = True
                else:
                    value = None  # Existing pending node; never a second question.

    if new_binding:
        result = await invoke(dict(messages=[dict(role="user", content=model.selection.question)]))
    else:

        async def snapshot():
            with tracing_context(enabled=False):
                async with open_saver(factory, binding, token) as saver:
                    graph = await adapter.abuild(model.bridge(), backend.tools(), saver)
                    return await graph.aget_state(config)

        state = await controlled(factory, token, binding, stopping, snapshot)
        interrupts = state.interrupts or tuple(i for task in state.tasks for i in task.interrupts)
        if interrupts:
            result = {"__interrupt__": interrupts}
        elif state.values and not state.next:
            result = state.values
        elif state.next:
            result = await invoke(None)
        else:
            # Existing binding without a checkpoint is not permission to repeat input.
            raise failure("REVIEWER_CHECKPOINT_UNPROVEN")
    while result.get("__interrupt__"):
        value = reference(result["__interrupt__"], binding)
        while True:
            await boundary(factory, token, stopping)
            async with factory() as db:
                approval = await security.approval(
                    db, token.owner_id, uuid.UUID(value["approval_id"])
                )
                if (
                    approval.run_id != token.run_id
                    or approval.request_hash != value["request_hash"]
                ):
                    raise failure("MODEL_BINDING_CHANGED")
                approved, charge = await model._rows(db, value["call_hash"])
                if approved is None or approved.id != approval.id:
                    raise failure("MODEL_BINDING_CHANGED")
                if charge is not None:
                    await model._actual(db, value["call_hash"])
                    ready = True
                else:
                    if approval.state in {"denied", "revoked"}:
                        raise failure("APPROVAL_REQUIRED")
                    if security.clock_ms() >= approval.expires_at_ms:
                        raise failure("APPROVAL_EXPIRED")
                    ready = approval.state == "approved"
            if ready:
                break
            await asyncio.sleep(0.1)
        result = await invoke(Command(resume=value))
    await boundary(factory, token, stopping)
    await model.verify_state(result)
    async with factory() as db:
        await reviewer_finish.finish(db, token)
        await db.commit()
    drained(token)
    return await reviewer_sdk_finish.record(factory, token, binding)
