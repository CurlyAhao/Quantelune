"""Lifecycle-owned scheduler for fixed research operations, independent of HTTP."""

import asyncio
import logging
from contextlib import suppress
from typing import Any

from sqlalchemy import or_, select

from app.exceptions import StatForgeError
from app.models.research import ResearchAttempt, ResearchRun, ResearchSession, ResearchWorkspace
from app.models.research_execution import (
    ResearchExecutionDispatch,
    ResearchExecutionIntent,
    ResearchExecutionLease,
    ResearchNoDispatchReceipt,
    ResearchPublicationReceipt,
)
from app.models.research_harness import ResearchHarnessThread
from app.research import (
    assistant_invocation,
    assistant_recovery,
    execution_control,
    execution_intent,
    execution_lease,
    execution_runner,
    publication,
    publication_heartbeat,
    result_recovery,
)
from app.research.contracts import State, parse_document
from app.research.execution_identity import WorkerIdentity, producer_dead
from app.research.execution_lease import ExecutionToken
from app.research.execution_quiescence import native_quiescent, stage_not_created
from app.services import research_service as ledger

logger = logging.getLogger("statforge.research.supervisor")


async def _await_terminal(task):
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                break
            interrupted = True
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result


class Supervisor:
    def __init__(self, factory, *, formal_runtime=None):
        self.factory = factory
        self.formal_runtime = formal_runtime  # Server-only factory, never checkpoint input.
        self.worker = WorkerIdentity.current()
        self.stopping = asyncio.Event()
        self.task = None
        self.business = None
        self.cleaning = set()
        self.pre_dispatch = set()

    def start(self):
        if self.task is not None:
            raise RuntimeError("Supervisor already started.")
        self.task = asyncio.create_task(self._loop(), name="research-supervisor")

    async def close(self):
        first_stop = not self.stopping.is_set()
        self.stopping.set()
        if (
            first_stop
            and self.business is not None
            and not self.business.done()
            and self.business not in self.cleaning
        ):
            self.business.cancel()
        if self.task is not None:
            # Never close the DB while our producer/commit is still active.
            while not self.task.done():
                try:
                    await asyncio.shield(self.task)
                except asyncio.CancelledError:
                    continue
            self.task.result()

    async def _loop(self):
        while not self.stopping.is_set():
            try:
                token = await self._select()
                if token is not None:
                    if self.stopping.is_set():
                        await self._finish_stopped(token, True)
                        return
                    self.business = asyncio.create_task(self.execute(token))
                    try:
                        await self.business
                    except asyncio.CancelledError:
                        pass
                    finally:
                        self.business = None
            except Exception as exc:
                # Only stable error codes, no data/path/secret-bearing exceptions.
                logger.warning("RESEARCH_SUPERVISOR_REFUSED: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=1)
            except TimeoutError:
                pass

    async def _select(self):
        candidate: tuple[Any, ...] | None = None
        async with self.factory() as db:
            active = await db.scalar(
                select(ResearchExecutionLease).where(ResearchExecutionLease.active_slot == 1)
            )
            if active is not None:
                attempt = await db.get(ResearchAttempt, active.attempt_id)
                run = await db.get(ResearchRun, attempt.run_id)
                conversation = await db.get(ResearchSession, run.session_id)
                scope = await db.get(ResearchWorkspace, conversation.workspace_id)
                candidate = (scope.owner_id, run.id, run.revision, run.state)
                if not producer_dead(
                    active.producer_host, active.producer_pid, active.producer_birth
                ):
                    return None
            else:
                pending = (
                    await db.execute(
                        select(
                            ResearchHarnessThread, ResearchExecutionLease, ResearchExecutionIntent
                        )
                        .join(
                            ResearchAttempt, ResearchAttempt.id == ResearchHarnessThread.attempt_id
                        )
                        .join(ResearchRun, ResearchRun.id == ResearchAttempt.run_id)
                        .join(
                            ResearchExecutionLease,
                            ResearchExecutionLease.attempt_id == ResearchAttempt.id,
                        )
                        .join(
                            ResearchExecutionIntent,
                            ResearchExecutionIntent.attempt_id == ResearchAttempt.id,
                        )
                        .join(
                            ResearchPublicationReceipt,
                            ResearchPublicationReceipt.attempt_id == ResearchAttempt.id,
                        )
                        .where(
                            ResearchHarnessThread.purpose.in_(["business", "assistant"]),
                            or_(
                                ResearchHarnessThread.finish_hash.is_(None),
                                ResearchHarnessThread.finish_checkpoint_id.is_(None),
                            ),
                            ResearchAttempt.number == ResearchRun.attempt_count,
                            ResearchRun.state.in_([State.COMPLETED.value, State.FAILED.value]),
                        )
                        .limit(16)
                    )
                ).all()
                for thread, lease, intent in pending:
                    if thread.purpose == "assistant" and self.formal_runtime is None:
                        continue
                    if producer_dead(lease.producer_host, lease.producer_pid, lease.producer_birth):
                        return ExecutionToken(
                            thread.owner_id,
                            thread.run_id,
                            thread.attempt_id,
                            lease.worker_id,
                            lease.fence,
                            intent.fence if lease.result_only else None,
                        )
                candidates = (
                    await db.execute(
                        select(
                            ResearchWorkspace.owner_id,
                            ResearchRun.id,
                            ResearchRun.revision,
                            ResearchRun.input_json,
                        )
                        .join(ResearchSession, ResearchSession.id == ResearchRun.session_id)
                        .join(
                            ResearchWorkspace, ResearchWorkspace.id == ResearchSession.workspace_id
                        )
                        .where(
                            ResearchRun.state == State.QUEUED.value,
                            ResearchSession.is_deleted.is_(False),
                            ResearchSession.is_archived.is_(False),
                            ResearchWorkspace.is_deleted.is_(False),
                        )
                        .order_by(ResearchRun.created_at, ResearchRun.id)
                        .limit(64)
                    )
                ).all()
                for row in candidates:
                    if parse_document(row[3])["parameters"].get("operation") != "execute":
                        continue
                    run, _ = await ledger.run(db, row[1], row[0])
                    from app.research.plan_execution import authorize_child

                    try:
                        await authorize_child(db, row[0], run)
                    except StatForgeError:
                        continue  # A revoked/stale plan child cannot starve unrelated admission.
                    attempt = await db.scalar(
                        select(ResearchAttempt).where(
                            ResearchAttempt.run_id == run.id,
                            ResearchAttempt.number == run.attempt_count,
                        )
                    )
                    invocation = await assistant_invocation.load(db, row[0], run, attempt)
                    if invocation is not None and self.formal_runtime is None:
                        continue  # Never execute a formal Run via the legacy fallback.
                    candidate = row[:3]
                    break
        if candidate is None:
            return None
        owner, run_id, revision = candidate[:3]
        if len(candidate) == 4:
            if self.formal_runtime is not None:
                async with self.factory() as db:
                    run, _ = await ledger.run(db, run_id, owner)
                    attempt = await db.scalar(
                        select(ResearchAttempt).where(
                            ResearchAttempt.run_id == run.id,
                            ResearchAttempt.number == run.attempt_count,
                        )
                    )
                    invocation = await assistant_invocation.load(db, owner, run, attempt)
                    native_phase = any(
                        [
                            await db.get(ResearchExecutionIntent, attempt.id) is not None,
                            await db.get(ResearchExecutionDispatch, attempt.id) is not None,
                            await db.get(ResearchPublicationReceipt, attempt.id) is not None,
                        ]
                    )
                if (
                    invocation is not None
                    and not native_phase
                    and candidate[3]
                    in {State.RUNNING.value, State.WAITING_APPROVAL.value, State.PAUSED.value}
                ):
                    try:
                        recovered = await assistant_recovery.acquire(
                            self.factory, owner, run_id, revision, self.worker
                        )
                    except StatForgeError as exc:
                        if exc.error_code == "RESEARCH_ASSISTANT_RECOVERY_REPLY_UNPROVEN":
                            await self._dead_without_result(owner, run_id, revision)
                            return None
                        raise
                    self.pre_dispatch.add(recovered.attempt_id)
                    return recovered
            if candidate[3] != State.RUNNING.value:
                return None  # No automatic pause/approval/cancel override.
            try:
                return await result_recovery.acquire(
                    self.factory, owner, run_id, revision, self.worker
                )
            except (StatForgeError, OSError, ValueError):
                await self._dead_without_result(owner, run_id, revision)
                return None
        async with self.factory() as db:
            token = await execution_lease.claim(db, owner, run_id, revision, self.worker)
            await db.commit()
            self.pre_dispatch.add(token.attempt_id)
            return token

    async def _dead_without_result(self, owner, run_id, revision):
        async with self.factory() as db:
            run, _scope = await ledger.run(db, run_id, owner)
            attempt = await db.scalar(
                select(ResearchAttempt).where(
                    ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
                )
            )
            lease = await db.get(ResearchExecutionLease, attempt.id)
            intent = await db.get(ResearchExecutionIntent, attempt.id)
            dispatch = await db.get(ResearchExecutionDispatch, attempt.id)
            if (
                run.revision != revision
                or await db.get(ResearchPublicationReceipt, attempt.id) is not None
            ):
                return
            token = ExecutionToken(
                owner,
                run_id,
                attempt.id,
                lease.worker_id,
                lease.fence,
                intent.fence if lease.result_only and intent else None,
            )
            host = lease.producer_host
            captured_hash = run.input_hash
            if dispatch is not None:
                # Sealed results, half-written output, and Popen-before-launch are
                # all effect-bearing phases. None is proof of no computation.
                return
        # Unknown dispatch/launch cannot be converted into a new attempt.
        if not native_quiescent(intent, dispatch, token, host, captured_hash):
            return
        async with self.factory() as db:
            await execution_lease.reconcile_dead(db, owner, run_id)
            await db.commit()

    async def _published_boundary(self, token):
        # Metadata observation of the actual atomic publication boundary only.
        # The owned work remains responsible for full byte and SDK-finish proof.
        from app.models.user import User

        async with self.factory() as db:
            owner = await db.get(User, token.owner_id)
            if owner is None or not owner.is_active:
                raise ledger.error("ACCESS_DENIED", 404)
            receipt = await db.get(ResearchPublicationReceipt, token.attempt_id)
            lease = await db.get(ResearchExecutionLease, token.attempt_id)
            attempt = await db.get(ResearchAttempt, token.attempt_id)
            run, _ = await ledger.run(db, token.run_id, token.owner_id)
            return (
                receipt is not None
                and lease is not None
                and attempt is not None
                and attempt.run_id == run.id
                and attempt.number == run.attempt_count
                and attempt.state == receipt.state == run.state
                and run.state in {"completed", "failed"}
                and receipt.publication_fence == token.fence
                and lease.worker_id == token.worker_id
                and lease.fence == token.fence
                and lease.active_slot is None
                and lease.released_at_ms is not None
            )

    async def _heartbeat(self, token, done):
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=2)
            except TimeoutError:
                # The publisher may have committed/released its lease while it
                # still holds exclusive files for its SDK return. A separate
                # heartbeat cannot reopen those bytes. Observe only that atomic
                # boundary here; the owned work/SDK finish retains full proof.
                published = await self._published_boundary(token)
                if published:
                    await done.wait()  # SDK finish may still write after business release.
                    return
                try:
                    with publication_heartbeat.heartbeat_scope(token):
                        async with self.factory() as db:
                            await execution_lease.heartbeat(db, token)
                            await db.commit()
                except StatForgeError as exc:
                    if exc.error_code != "RESEARCH_EXECUTION_FENCE_LOST":
                        raise
                    if not await self._published_boundary(token):
                        raise
                    await done.wait()
                    return

    async def _pause_boundary(self, token):
        async with self.factory() as db:
            run, _scope = await ledger.run(db, token.run_id, token.owner_id)
            if run.state == State.RUNNING.value:
                await execution_control.acknowledge_pause(db, token)
                await db.commit()
        while True:
            async with self.factory() as db:
                run, _scope = await ledger.run(db, token.run_id, token.owner_id)
                state = run.state
            if state == State.RUNNING.value:
                return
            if state != State.PAUSED.value or self.stopping.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(0.1)

    async def execute(self, token, *, formal_context=None):
        with publication_heartbeat.execution_scope(token):
            return await self._execute(token, formal_context=formal_context)

    async def _execute(self, token, *, formal_context=None):
        done = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(token, done))
        publication_heartbeat.register_heartbeat(token, heartbeat)
        work = asyncio.create_task(
            self._work(token)
            if formal_context is None
            else self._work(token, formal_context=formal_context)
        )
        failure = None
        try:
            finished, _pending = await asyncio.wait(
                {work, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if work not in finished:
                heartbeat.result()
                raise ledger.error("EXECUTION_FENCE_LOST")
            return await work
        except BaseException as exc:
            failure = exc
            raise
        finally:
            executing = asyncio.current_task()
            self.cleaning.add(executing)
            try:
                cleanup = asyncio.create_task(
                    self._terminal_cleanup(token, work, heartbeat, done, failure),
                    name="research-terminal-cleanup",
                )
                await _await_terminal(cleanup)
            finally:
                self.cleaning.discard(executing)

    async def _terminal_cleanup(self, token, work, heartbeat, done, failure):
        try:
            if failure is not None:
                work.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await work  # Native Job cleanup/publication commit must be terminal.
                if getattr(failure, "code", None) not in {
                    "ML_CLEANUP_FAILED",
                    "ML_TERMINATION_FAILED",
                }:
                    await self._finish_stopped(token, isinstance(failure, asyncio.CancelledError))
        finally:
            done.set()
            with suppress(Exception):
                await heartbeat

    async def _work(self, token, *, formal_context=None):
        from app.research import assistant_invocation, assistant_supervision
        from app.research.harness_runtime import business

        async with self.factory() as db:
            run, _ = await ledger.run(db, token.run_id, token.owner_id)
            attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
            invocation = await assistant_invocation.load(db, token.owner_id, run, attempt)
        if invocation is not None:
            from app.research import harness_binding

            # A registered tool may publish and release this same lease before
            # the outer business graph starts. Allocate its immutable binding
            # now, while this actual attempt is still owned and running.
            if await harness_binding.existing(self.factory, token, "business") is None:
                await harness_binding.bind(self.factory, token, "business")
            if formal_context is None:
                if self.formal_runtime is None:
                    raise ledger.error("INVOCATION_RUNTIME_REQUIRED")
                formal_context = await self.formal_runtime(token)
            gateway, tools = formal_context
            await assistant_supervision.run(
                self.factory, token, gateway, tools, stopping=self.stopping
            )

        actual = await business(self.factory, token, lambda: self._business_work(token))
        if invocation is not None:
            from app.research.formal_finish import finish

            await finish(self.factory, token, gateway, tools, invocation)
        return actual

    async def _business_work(self, token):
        if token.origin_fence is None:
            await self._pause_boundary(token)
            async with self.factory() as db:
                await execution_intent.prepare(db, token)
                await db.commit()
            while True:
                await self._pause_boundary(token)
                try:
                    await execution_runner.produce(
                        self.factory,
                        token,
                        on_dispatch_committed=lambda: self.pre_dispatch.discard(token.attempt_id),
                    )
                    break
                except StatForgeError as exc:
                    if exc.error_code != "RESEARCH_EXECUTION_PAUSE_REQUESTED":
                        raise
        while True:
            await self._pause_boundary(token)
            try:
                return await publication.publish(self.factory, token)
            except StatForgeError as exc:
                if exc.error_code != "RESEARCH_PUBLICATION_PAUSE_REQUESTED":
                    raise

    async def _finish_stopped(self, token, cancelled):
        if await publication._confirmed(self.factory, token) is not None:
            return  # Never mask SDK errors by validating an already released lease.
        async with self.factory() as db:
            run, _scope, lease, _now = await execution_lease.validate(db, token, running=False)
            intent = await db.get(ResearchExecutionIntent, token.attempt_id)
            dispatch = await db.get(ResearchExecutionDispatch, token.attempt_id)
            host, captured_hash = lease.producer_host, run.input_hash
            if dispatch is not None and run.state != State.CANCEL_REQUESTED.value:
                # Retain the original result/unknown phase and active epoch.
                # A genuine later parent death may recover complete bytes; never
                # force an already computed result into an unrecoverable retry.
                return
        known_pre_dispatch = (
            token.attempt_id in self.pre_dispatch
            and dispatch is None
            and intent is not None
            and stage_not_created(intent, token, captured_hash)
        )
        if not known_pre_dispatch and not native_quiescent(
            intent, dispatch, token, host, captured_hash
        ):
            return
        async with self.factory() as db:
            run, _scope, lease, now = await execution_lease.validate(db, token, running=False)
            if known_pre_dispatch:
                current_intent = await db.get(ResearchExecutionIntent, token.attempt_id)
                if (
                    await db.get(ResearchExecutionDispatch, token.attempt_id) is not None
                    or current_intent is None
                    or current_intent.document_json != intent.document_json
                    or not stage_not_created(current_intent, token, run.input_hash)
                ):
                    return
            if run.state not in {
                State.RUNNING.value,
                State.PAUSED.value,
                State.CANCEL_REQUESTED.value,
                State.WAITING_APPROVAL.value,
                State.CANCELLED.value,
            }:
                return
            from app.models.research_security import ResearchCharge

            attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
            invocation = await assistant_invocation.load(db, token.owner_id, run, attempt)
            if invocation is not None:
                pending_financial = any(
                    charge.state in {"reserved", "dispatched"}
                    for _, charge in await assistant_recovery.charges_for_attempt(
                        db, token.owner_id, run, attempt, _scope
                    )
                )
            else:
                pending_financial = (
                    await db.scalar(
                        select(ResearchCharge.id)
                        .where(
                            ResearchCharge.run_id == run.id,
                            ResearchCharge.state.in_(["reserved", "dispatched"]),
                        )
                        .limit(1)
                    )
                    is not None
                )
            if pending_financial:
                return  # Current financial transport/cleanup is not terminal yet.
            after = (
                State.CANCELLED
                if run.state in {State.CANCEL_REQUESTED.value, State.CANCELLED.value}
                else State.INTERRUPTED
            )
            if run.state != after.value:
                await ledger.transition(db, token.owner_id, run.id, run.revision, after)
            attempt = await db.get(ResearchAttempt, token.attempt_id)
            attempt.error_code = "EXECUTION_STOPPED" if cancelled else "EXECUTION_UNKNOWN"
            lease.active_slot, lease.released_at_ms = None, now
            if known_pre_dispatch:
                db.add(
                    ResearchNoDispatchReceipt(
                        attempt_id=token.attempt_id,
                        owner_id=token.owner_id,
                        input_hash=run.input_hash,
                        intent_hash=current_intent.intent_hash,
                        fence=lease.fence,
                        worker_id=lease.worker_id,
                        producer_host=lease.producer_host,
                        producer_pid=lease.producer_pid,
                        producer_birth=lease.producer_birth,
                        released_at_ms=now,
                        state=after.value,
                    )
                )
            await db.commit()
            self.pre_dispatch.discard(token.attempt_id)
