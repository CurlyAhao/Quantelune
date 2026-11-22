"""Official graph interrupt waits under the existing business execution responsibility."""

import asyncio
import uuid

from langgraph.types import Command
from langsmith import tracing_context

from app.models.research import ResearchAttempt
from app.research import (
    advisory_receipt,
    assistant_invocation,
    execution_lease,
    harness_binding,
    publication,
)
from app.research.assistant_model import AssistantModel
from app.research.egress import DisclosureSelection, failure
from app.research.formal_harness import FormalHarnessAdapter
from app.research.harness_runtime import open_saver
from app.services import research_security_service as security


async def run(factory, token, gateway, tools, *, stopping):
    terminal = await publication._confirmed(factory, token)
    async with factory() as db:
        if terminal is None:
            current, _, _, _ = await execution_lease.validate(db, token, running=False)
        else:
            from app.services import research_service as ledger

            current, _ = await ledger.run(db, token.run_id, token.owner_id)
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        invocation = await assistant_invocation.load(db, token.owner_id, current, attempt)
        if invocation is not None and terminal is None:
            await assistant_invocation.verify_current(
                db, token.owner_id, current, attempt, invocation
            )
    if invocation is None:
        raise failure("INVOCATION_REQUIRED")
    selection = DisclosureSelection.model_validate(invocation["selection"])
    binding = await harness_binding.existing(factory, token, "assistant")
    if binding is None:
        if terminal is not None:
            raise failure("MODEL_TERMINAL_CHECKPOINT_UNPROVEN")
        binding = await harness_binding.bind(factory, token, "assistant")
    runtime = AssistantModel(factory, token, binding, gateway, selection)
    adapter = FormalHarnessAdapter()
    model = runtime.bridge()
    with tracing_context(enabled=False):
        async with open_saver(factory, binding, token) as saver:
            graph = await adapter.abuild(model, tools, saver)
            config = {
                "configurable": {"thread_id": binding.thread_id},
                "callbacks": [],
                "recursion_limit": 12,
            }
            snapshot = await graph.aget_state(config)
            if snapshot.values and not snapshot.next:
                await runtime.verify_state(snapshot.values)
                return snapshot.values  # Advisory state, never a business completion.
            interrupts = [item for task in snapshot.tasks for item in task.interrupts]
            if terminal is not None:
                # An already released business lease cannot be reclaimed just
                # to finish SDK bookkeeping. Authorize actual publication and
                # cached real replies; no missing reply may trigger a send.
                if not snapshot.values:
                    raise failure("MODEL_TERMINAL_CHECKPOINT_UNPROVEN")
                await runtime.verify_state(snapshot.values)
                command = None
                if interrupts:
                    if len(interrupts) != 1:
                        raise failure("MODEL_RESUME_REFUSED")
                    reference = interrupts[0].value
                    if (
                        not isinstance(reference, dict)
                        or reference.get("binding_hash") != binding.binding_hash
                    ):
                        raise failure("MODEL_RESUME_REFUSED")
                    async with factory() as db:
                        approved, charge = await runtime._rows(db, reference["call_hash"])
                        if (
                            approved is None
                            or charge is None
                            or str(approved.id) != reference.get("approval_id")
                            or approved.request_hash != reference.get("request_hash")
                        ):
                            raise failure("REPLY_UNPROVEN")
                        if (
                            await advisory_receipt.read(db, token.owner_id, approved, charge)
                            is None
                        ):
                            raise failure("REPLY_UNPROVEN")
                    command = Command(resume=reference)
                result = await graph.ainvoke(command, config, durability="sync")
                if result.get("__interrupt__"):
                    raise failure("MODEL_TERMINAL_CHECKPOINT_UNPROVEN")
                await runtime.verify_state(result)
                return result
            if interrupts:
                result = {"__interrupt__": interrupts}
            else:
                result = await graph.ainvoke(
                    None
                    if snapshot.next
                    else {"messages": [{"role": "user", "content": selection.question}]},
                    config,
                    durability="sync",
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
            if stopping.is_set():
                raise asyncio.CancelledError
            async with factory() as db:
                current, _, lease, _ = await execution_lease.validate(db, token, running=False)
                if current.state in {"cancelled", "cancel_requested", "interrupted"}:
                    raise asyncio.CancelledError
                await execution_lease._source(db, token.owner_id, current)
                approval = await security.approval(
                    db, token.owner_id, uuid.UUID(reference["approval_id"])
                )
                if (
                    approval.run_id != token.run_id
                    or approval.request_hash != reference["request_hash"]
                ):
                    raise failure("MODEL_BINDING_CHANGED")
                if approval.state in {"denied", "revoked"}:
                    raise failure("APPROVAL_REQUIRED")
                approved, charge = await runtime._rows(db, reference["call_hash"])
                if approved is None or approved.id != approval.id:
                    raise failure("MODEL_BINDING_CHANGED")
                if charge is not None:
                    if await advisory_receipt.read(db, token.owner_id, approval, charge) is None:
                        raise failure("REPLY_UNPROVEN")
                    ready = not lease.pause_requested
                else:
                    if security.clock_ms() >= approval.expires_at_ms:
                        raise failure("APPROVAL_EXPIRED")
                    ready = approval.state == "approved" and not lease.pause_requested
            if ready:
                break
            await asyncio.sleep(0.1)  # Short Sessions; Supervisor owns the heartbeat.
        async with open_saver(factory, binding, token) as saver:
            result = await adapter.invoke(model, tools, saver, binding, resume=reference)
    await runtime.authorize()
    await runtime.verify_state(result)
    return result
