"""Official SDK durable task for business work; checkpoint is never completion proof."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.errors import NodeCancelledError
from langgraph.func import entrypoint, task
from langgraph.types import RetryPolicy
from langsmith import tracing_context
from sqlalchemy import select

from app.config import settings
from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_execution import ResearchPublicationReceipt
from app.models.research_harness import ResearchHarnessThread
from app.research import harness_binding, publication
from app.research.contracts import logical_hash
from app.research.execution_lease import _boundary
from app.research.harness_sqlite import open_saver as open_sqlite_saver
from app.services import research_service as ledger


async def business(factory, token, operation):
    binding = await harness_binding.existing(factory, token)
    if binding is None:
        binding = await harness_binding.bind(factory, token)
    refs = {
        "run_id": str(token.run_id),
        "attempt_id": str(token.attempt_id),
        "input_hash": binding.input_hash,
    }
    config: RunnableConfig = {
        "configurable": {"thread_id": binding.thread_id},
        "callbacks": [],
        "recursion_limit": 12,
    }
    async with open_saver(factory, binding, token) as saver:

        async def domain_task_impl(reference: dict[str, str]) -> dict[str, Any]:
            if reference != refs:
                raise ledger.error("HARNESS_BINDING_CHANGED")
            await harness_binding.authorize(factory, binding, token)
            actual = await publication._confirmed(factory, token)
            if actual is not None:
                return actual  # Business commit wins even without SDK pending write.
            return await operation()

        domain_task = cast(
            Callable[[dict[str, str]], Awaitable[dict[str, Any]]],
            cast(Callable[..., Any], task)(
                domain_task_impl, name="domain_task", retry_policy=RetryPolicy(max_attempts=1)
            ),
        )

        @entrypoint(checkpointer=saver)
        async def durable_business(reference):
            return await domain_task(reference)

        with tracing_context(enabled=False):
            state = await durable_business.aget_state(config)
            actual = await publication._confirmed(factory, token)
            if actual is not None and not state.next and state.values:
                # Recheck authority instead of trusting the SDK output itself.
                await _finish(factory, binding, token, saver, state, actual)
                return actual
            try:
                result = await durable_business.ainvoke(
                    None if state.next else refs, config, durability="sync"
                )
            except NodeCancelledError as exc:
                raise asyncio.CancelledError from exc
        actual = await publication._confirmed(factory, token)
        if actual is None or result != actual:
            raise ledger.error("HARNESS_BUSINESS_UNPROVEN")
        state = await durable_business.aget_state(config)
        await _finish(factory, binding, token, saver, state, actual)
        return actual


@asynccontextmanager
async def open_saver(factory, binding, token):
    if settings.db_backend == "sqlite":
        async with open_sqlite_saver(factory, binding, token) as saver:
            yield saver
    elif settings.db_backend == "postgresql":
        from app.research.harness_postgres import open_saver as open_postgres_saver

        async with open_postgres_saver(factory, binding, token) as saver:
            yield saver
    else:
        raise ledger.error("HARNESS_BACKEND_REFUSED")


@asynccontextmanager
async def open_reviewer_snapshot(factory, binding, token, *, lease=None):
    """Owner proof ends before backend exclusion; post-proof starts after close."""
    from app.research.reviewer_contracts import ReviewerToken

    if type(token) is not ReviewerToken or binding.purpose != "assistant":
        raise ledger.error("HARNESS_PROFILE_REFUSED")
    from app.research import reviewer_finish

    async with factory() as db:
        await reviewer_finish.terminal_claim(db, token)
    # Includes actual completed terminal claim, complete replay and exact binding.
    await harness_binding.authorize(factory, binding, token, checkpoint=True)
    if settings.db_backend == "sqlite":
        from app.research.harness_sqlite_snapshot import open_snapshot

        async with open_snapshot(binding) as saver:
            yield saver
    elif settings.db_backend == "postgresql":
        from app.research.harness_postgres_snapshot import open_snapshot as open_pg_snapshot

        async with open_pg_snapshot(factory, binding, lease=lease) as saver:
            yield saver
    else:
        raise ledger.error("HARNESS_BACKEND_REFUSED")
    # No saver lock/connection survives this independent owner transaction.
    await harness_binding.authorize(factory, binding, token, checkpoint=True)


async def _finish(factory, binding, token, saver, state, actual):
    if (
        state.next
        or state.values != actual
        or any(item.interrupts or item.error for item in state.tasks)
    ):
        raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
    checkpoint = await saver.aget_tuple(state.config)
    if checkpoint is None or checkpoint.checkpoint["channel_values"].get("__end__") != actual:
        raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
    checkpoint_id = checkpoint.config["configurable"]["checkpoint_id"]
    proof = {"binding_hash": binding.binding_hash, "checkpoint_id": checkpoint_id, "output": actual}
    digest = logical_hash(proof)
    await harness_binding.authorize(factory, binding, token)
    async with factory() as db:
        await _boundary(db, token.owner_id)
        run, scope = await ledger.run(db, token.run_id, token.owner_id)
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        receipt = await db.get(ResearchPublicationReceipt, token.attempt_id, populate_existing=True)
        thread = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
        if thread is None:
            raise ledger.error("HARNESS_BINDING_CHANGED")
        harness_binding._verify(thread, token)
        if any(
            getattr(thread, field) != getattr(binding, field)
            for field in harness_binding.Binding.__dataclass_fields__
        ):
            raise ledger.error("HARNESS_BINDING_CHANGED")
        if (
            attempt is None
            or attempt.run_id != run.id
            or attempt.number != run.attempt_count
            or receipt is None
            or receipt.state != run.state
            or attempt.state != run.state
            or str(receipt.root_id) != actual["root_id"]
            or receipt.snapshot_hash != actual["snapshot_hash"]
            or thread is None
            or thread.binding_hash != binding.binding_hash
            or thread.input_hash != run.input_hash
        ):
            raise ledger.error("HARNESS_BUSINESS_UNPROVEN")
        if thread.finish_hash is not None or thread.finish_checkpoint_id is not None:
            if (thread.finish_hash, thread.finish_checkpoint_id) != (digest, checkpoint_id):
                raise ledger.error("HARNESS_FINISH_CHANGED")
            recorded = (
                await db.scalars(
                    select(ResearchEvent)
                    .where(
                        ResearchEvent.entity_id == run.id,
                        ResearchEvent.workspace_id == scope.id,
                        ResearchEvent.event_type == "harness.sdk_finished",
                    )
                    .order_by(ResearchEvent.sequence.desc())
                    .limit(16)
                )
            ).all()
            from app.research.contracts import parse_document

            if not any(
                parse_document(event.payload_json) == proof and event.logical_hash == digest
                for event in recorded
            ):
                raise ledger.error("HARNESS_FINISH_UNPROVEN")
            return
        thread.finish_hash, thread.finish_checkpoint_id = digest, checkpoint_id
        await ledger._append(
            db, scope, token.owner_id, "run", run.id, "harness.sdk_finished", proof
        )
        await db.commit()
