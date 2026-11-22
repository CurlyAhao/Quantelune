"""Official Reader graph under durable approval, control and saved Answer authority."""

import asyncio
import uuid

from langgraph.types import Command
from langsmith import tracing_context

from app.exceptions import StatForgeError
from app.models.research import ResearchAttempt
from app.research import (
    harness_binding,
    reader_admission,
    reader_control,
    reader_finish,
    reader_lease,
    reader_sdk_finish,
    reader_transport,
)
from app.research.egress import DisclosureSelection, failure
from app.research.harness_runtime import open_saver
from app.research.reader_contracts import PAUSED_CODES
from app.research.reader_harness import ReaderHarnessAdapter
from app.research.reader_model import ReaderModel
from app.research.reader_runtime import ReaderRuntime
from app.research.supervisor import _await_terminal
from app.services import research_security_service as security
from app.services import research_service as ledger


async def boundary(factory, token, stopping):
    """Only a drained SDK boundary can acknowledge a control request."""
    reader_transport.drained(token)
    while True:
        if stopping.is_set():
            raise asyncio.CancelledError
        async with factory() as db:
            run, _ = await ledger.run(db, token.run_id, token.owner_id)
            if run.state == "completed":
                await reader_finish.terminal_claim(db, token)
                return
            run, _, lease, _ = await reader_lease.validate(
                db, token, running=False, current_source=False
            )
            attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
            if await reader_control.cancelled(db, token.owner_id, run, attempt):
                await reader_control.acknowledge(db, token)
                await db.commit()
                raise asyncio.CancelledError
            requested = await reader_control.latest(db, token.owner_id, run, attempt)
            if lease.pause_requested:
                await reader_control.acknowledge(db, token)
                await db.commit()
            else:
                if requested is not None and requested["action"] == "resume":
                    await reader_control.acknowledge(db, token)
                    await db.commit()
                return
        await asyncio.sleep(0.1)


async def controlled(factory, token, stopping, operation):
    """The awaited task includes graph execution, Gateway cleanup and saver close."""
    await boundary(factory, token, stopping)
    task = asyncio.create_task(operation(), name="reader-sdk-invocation")
    failure = None
    try:
        while not task.done():
            if stopping.is_set():
                raise asyncio.CancelledError
            async with factory() as db:
                run, _ = await ledger.run(db, token.run_id, token.owner_id)
                attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
                if await reader_control.cancelled(db, token.owner_id, run, attempt):
                    raise asyncio.CancelledError
            await asyncio.wait({task}, timeout=0.1)
        return task.result()
    except BaseException as error:
        failure = error
        raise
    finally:

        async def drain():
            if not task.done():
                task.cancel()
            # A second cancellation must not detach SDK/financial cleanup.
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            reader_transport.drained(token)
            if (
                isinstance(outcome, BaseException)
                and not isinstance(outcome, asyncio.CancelledError)
                and outcome is not failure
            ):
                raise outcome

        await _await_terminal(asyncio.create_task(drain(), name="reader-sdk-drain"))


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


async def run(factory, token, gateway, *, stopping):
    binding = await harness_binding.existing(factory, token, "assistant")
    if binding is None:
        binding = await harness_binding.bind(factory, token, "assistant")
    async with factory() as db:
        current, _ = await ledger.run(db, token.run_id, token.owner_id)
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        admission = await reader_admission.load(db, token.owner_id, current, attempt)
        if admission is None:
            raise failure("INVOCATION_REQUIRED")
    model = ReaderModel(
        factory,
        token,
        binding,
        gateway,
        DisclosureSelection.model_validate(admission["selection"]),
        admission["tools"],
    )
    backend = ReaderRuntime(factory, token, binding)
    adapter = ReaderHarnessAdapter()
    config = dict(configurable=dict(thread_id=binding.thread_id), callbacks=[], recursion_limit=12)

    async def snapshot():
        with tracing_context(enabled=False):
            async with open_saver(factory, binding, token) as saver:
                graph = await adapter.abuild(model.bridge(), backend.tools(), saver)
                return await graph.aget_state(config)

    async def invoke(value):
        resumed_controls = set()

        async def operation():
            with tracing_context(enabled=False):
                async with open_saver(factory, binding, token) as saver:
                    graph = await adapter.abuild(model.bridge(), backend.tools(), saver)
                    return await graph.ainvoke(value, config, durability="sync")

        while True:
            try:
                return await controlled(factory, token, stopping, operation)
            except StatForgeError as error:
                if error.error_code not in PAUSED_CODES:
                    raise
                # controlled has already awaited the original SDK/saver and
                # financial tasks. Only this real boundary may ACK the pause.
                await boundary(factory, token, stopping)
                async with factory() as db:
                    run, _ = await ledger.run(db, token.run_id, token.owner_id)
                    attempt = await db.get(
                        ResearchAttempt, token.attempt_id, populate_existing=True
                    )
                    requested = await reader_control.latest(db, token.owner_id, run, attempt)
                    if (
                        requested is None
                        or requested["action"] != "resume"
                        or requested["control_index"] in resumed_controls
                        or len(resumed_controls) >= reader_control.LIMIT
                    ):
                        raise failure("MODEL_RESUME_REFUSED") from None
                    resumed_controls.add(requested["control_index"])
                value = None  # Existing failed/pending node only; never reinject the question.

    await boundary(factory, token, stopping)
    state = await controlled(factory, token, stopping, snapshot)
    interrupts = state.interrupts or tuple(item for task in state.tasks for item in task.interrupts)
    if interrupts:
        result = {"__interrupt__": interrupts}
    elif state.values and not state.next:
        result = state.values
    else:
        async with factory() as db:
            receipted = await reader_lease.answer_event(db, token.attempt_id) is not None
        if receipted and not state.next:
            raise failure("READER_CHECKPOINT_UNPROVEN")
        result = await invoke(
            None
            if state.next
            else dict(messages=[dict(role="user", content=model.selection.question)])
        )
    while result.get("__interrupt__"):
        value = reference(result["__interrupt__"], binding)
        while True:
            await boundary(factory, token, stopping)
            async with factory() as db:
                current, _ = await ledger.run(db, token.run_id, token.owner_id)
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
                    # Original historical proof accepts only an actual saved Reply.
                    await model._actual(db, value["call_hash"])
                    ready = True
                else:
                    if current.state == "completed" or token.fence > 1:
                        raise failure("REPLY_UNRECOVERABLE")
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
        await reader_finish.finish(db, token)
        await db.commit()

    async def closeout():
        with tracing_context(enabled=False):
            async with open_saver(factory, binding, token) as saver:
                graph = await adapter.abuild(model.bridge(), backend.tools(), saver)
                return await reader_sdk_finish.record(factory, token, binding, graph, saver, model)

    return await controlled(factory, token, stopping, closeout)
