"""Lifecycle-owned planner work, with separate bounded admission and real heartbeat."""

import asyncio
import logging
from contextlib import suppress

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchRun, ResearchSession, ResearchWorkspace
from app.models.research_planning import (
    ResearchPlannerSDKFinish,
    ResearchPlannerThread,
    ResearchPlanningLease,
    ResearchPlanReceipt,
)
from app.research import (
    execution_lease,
    plan_receipt,
    planner_admission,
    planner_supervision,
    planner_unresolved,
    planning_control,
    planning_lease,
)
from app.research.contracts import State
from app.research.execution_identity import WorkerIdentity, producer_dead
from app.research.supervisor import _await_terminal
from app.services import research_service as ledger

logger = logging.getLogger("statforge.research.planner")


class PlannerSupervisor:
    def __init__(self, factory, *, gateway_factory=None):
        self.factory = factory
        if gateway_factory is None:
            from app.services.research_gateway_service import get_gateway

            gateway_factory = get_gateway
        self.gateway_factory = gateway_factory
        self.worker = WorkerIdentity.current()
        self.stopping = asyncio.Event()
        self.task = None
        self.work = {}
        self.sdk_failed = (
            set()
        )  # One bounded recovery per unchanged owned process, never a tight retry.

    def start(self):
        if self.task is not None:
            raise RuntimeError("Planner supervisor already started.")
        self.task = asyncio.create_task(self._loop(), name="planner-supervisor")

    async def close(self):
        async def drain():
            self.stopping.set()
            errors = []
            if self.task is not None:
                try:
                    await self.task
                except BaseException as error:
                    errors.append(error)
            tasks = list(self.work.values())
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            errors.extend(
                result
                for result in results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            )
            if errors:
                raise BaseExceptionGroup("Planner shutdown failures", errors)

        await _await_terminal(asyncio.create_task(drain(), name="planner-shutdown-drain"))

    async def _loop(self):
        while not self.stopping.is_set():
            for attempt_id, task in list(self.work.items()):
                if task.done():
                    with suppress(asyncio.CancelledError, Exception):
                        task.result()
                    self.work.pop(attempt_id)
            try:
                if len(self.work) < 2:
                    token = await self._select()
                    if token is not None:
                        self.work[token.attempt_id] = asyncio.create_task(
                            self.execute(token), name="planner-owned-work"
                        )
            except Exception as error:
                logger.warning("PLANNER_ADMISSION_REFUSED: %s", type(error).__name__)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=0.1)
            except TimeoutError:
                pass

    async def _select(self):
        async with self.factory() as db:
            active = (
                await db.scalars(
                    select(ResearchPlanningLease)
                    .join(ResearchAttempt, ResearchAttempt.id == ResearchPlanningLease.attempt_id)
                    .join(ResearchRun, ResearchRun.id == ResearchAttempt.run_id)
                    .where(
                        ResearchPlanningLease.released_at_ms.is_(None),
                        ResearchRun.kind == "planner",
                    )
                    .limit(16)
                )
            ).all()
            for lease in active:
                if lease.attempt_id in self.work or lease.attempt_id in self.sdk_failed:
                    continue
                attempt = await db.get(ResearchAttempt, lease.attempt_id, populate_existing=True)
                if attempt is None:
                    raise ledger.error("STATE_INCONSISTENT")
                run = await db.get(ResearchRun, attempt.run_id, populate_existing=True)
                if lease.worker_id == self.worker.worker_id and self.worker.validate_current():
                    return planning_lease.PlanningToken(
                        lease.owner_id, run.id, attempt.id, lease.worker_id, lease.fence
                    )
                if not producer_dead(lease.producer_host, lease.producer_pid, lease.producer_birth):
                    continue
                receipt = await db.scalar(
                    select(ResearchPlanReceipt.id).where(
                        ResearchPlanReceipt.attempt_id == attempt.id
                    )
                )
                if receipt is not None:
                    token = await planning_lease.recover_receipt(
                        db, lease.owner_id, run.id, self.worker
                    )
                    await plan_receipt.finish(db, token)
                    await db.commit()
                    return token  # Only existing receipt / cached graph bookkeeping is permitted.
                await planning_lease.reconcile_dead(db, lease.owner_id, run.id)
                await db.commit()
            unfinished = (
                await db.execute(
                    select(ResearchPlannerThread, ResearchPlanningLease)
                    .join(ResearchAttempt, ResearchAttempt.id == ResearchPlannerThread.attempt_id)
                    .join(ResearchRun, ResearchRun.id == ResearchAttempt.run_id)
                    .join(
                        ResearchPlanningLease,
                        ResearchPlanningLease.attempt_id == ResearchAttempt.id,
                    )
                    .outerjoin(
                        ResearchPlannerSDKFinish,
                        ResearchPlannerSDKFinish.attempt_id == ResearchAttempt.id,
                    )
                    .where(
                        ResearchRun.state == "completed",
                        ResearchAttempt.number == ResearchRun.attempt_count,
                        ResearchPlannerSDKFinish.id.is_(None),
                    )
                    .limit(16)
                )
            ).all()
            for thread, lease in unfinished:
                if thread.attempt_id in self.work or thread.attempt_id in self.sdk_failed:
                    continue
                if lease.worker_id == self.worker.worker_id or producer_dead(
                    lease.producer_host, lease.producer_pid, lease.producer_birth
                ):
                    return planning_lease.PlanningToken(
                        thread.owner_id,
                        thread.run_id,
                        thread.attempt_id,
                        lease.worker_id,
                        lease.fence,
                    )
            candidates = (
                await db.execute(
                    select(ResearchRun, ResearchWorkspace.owner_id)
                    .join(ResearchSession, ResearchSession.id == ResearchRun.session_id)
                    .join(ResearchWorkspace, ResearchWorkspace.id == ResearchSession.workspace_id)
                    .where(
                        ResearchRun.kind == "planner",
                        ResearchRun.state == "queued",
                        ResearchSession.is_archived.is_(False),
                        ResearchSession.is_deleted.is_(False),
                        ResearchWorkspace.is_deleted.is_(False),
                    )
                    .order_by(ResearchRun.created_at, ResearchRun.id)
                    .limit(64)
                )
            ).all()
            for run, owner in candidates:
                if await planner_admission.load(db, owner, run) is None:
                    continue
                busy = await db.scalar(
                    select(ResearchPlanningLease.attempt_id).where(
                        ResearchPlanningLease.active_owner_id == owner
                    )
                )
                if busy is not None:
                    continue
                token = await planning_lease.claim(db, owner, run.id, run.revision, self.worker)
                await db.commit()
                return token
        return None

    async def _heartbeat(self, token, done):
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=2)
            except TimeoutError:
                async with self.factory() as db:
                    await execution_lease._boundary(db, token.owner_id)
                    run, _ = await ledger.run(db, token.run_id, token.owner_id)
                    attempt = await db.get(
                        ResearchAttempt, token.attempt_id, populate_existing=True
                    )
                    if await planner_unresolved.prove(db, token.owner_id, run, attempt) is not None:
                        return  # A proved released wait grants no new execution lease.
                    if run.state in {"completed", "cancelled", "failed", "interrupted"}:
                        lease = await db.get(
                            ResearchPlanningLease, token.attempt_id, populate_existing=True
                        )
                        if (
                            lease is None
                            or lease.fence != token.fence
                            or lease.worker_id != token.worker_id
                            or lease.released_at_ms is None
                        ):
                            raise ledger.error("EXECUTION_FENCE_LOST")
                    else:
                        _, _, lease, now = await planning_lease.validate(
                            db, token, running=False, current_source=False
                        )
                        # This renews ownership only. It cannot grant new model/source rights.
                        lease.heartbeat_at_ms, lease.lease_until_ms = (
                            now,
                            now + planning_lease.LEASE_MS,
                        )
                        await db.commit()

    async def execute(self, token):
        done = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(token, done), name="planner-heartbeat")
        work = asyncio.create_task(
            planner_supervision.run(
                self.factory, token, self.gateway_factory(), stopping=self.stopping
            ),
            name="planner-sdk-work",
        )
        failure = None
        try:
            finished, _ = await asyncio.wait({heartbeat, work}, return_when=asyncio.FIRST_COMPLETED)
            if work not in finished:
                heartbeat.result()
                async with self.factory() as db:
                    run, _ = await ledger.run(db, token.run_id, token.owner_id)
                    attempt = await db.get(
                        ResearchAttempt, token.attempt_id, populate_existing=True
                    )
                    if await planner_unresolved.prove(db, token.owner_id, run, attempt) is None:
                        raise ledger.error("EXECUTION_FENCE_LOST")
            return await work
        except BaseException as error:
            failure = error
            self.sdk_failed.add(token.attempt_id)
            raise
        finally:

            async def cleanup():
                try:
                    if failure is not None:
                        work.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await work  # Financial cancellation/SDK writes must be terminal.
                        async with self.factory() as db:
                            run, _ = await ledger.run(db, token.run_id, token.owner_id)
                            attempt = await db.get(
                                ResearchAttempt, token.attempt_id, populate_existing=True
                            )
                            if (
                                await planner_unresolved.prove(db, token.owner_id, run, attempt)
                                is not None
                            ):
                                return
                            if run.state in {"completed", "cancelled", "failed", "interrupted"}:
                                return
                            receipt = await db.scalar(
                                select(ResearchPlanReceipt.id).where(
                                    ResearchPlanReceipt.attempt_id == token.attempt_id
                                )
                            )
                            if receipt is not None:
                                await plan_receipt.finish(db, token)
                            else:
                                attempt = await db.get(
                                    ResearchAttempt, token.attempt_id, populate_existing=True
                                )
                                cancel = await planning_control.cancelled(
                                    db, token.owner_id, run, attempt
                                )
                                if cancel:
                                    await planning_control.acknowledge(db, token)
                                else:
                                    after = (
                                        State.INTERRUPTED
                                        if isinstance(failure, asyncio.CancelledError)
                                        or run.state in {"waiting_approval", "paused"}
                                        else State.FAILED
                                    )
                                    await planning_lease.stop_owned(db, token, after)
                            await db.commit()
                finally:
                    done.set()
                    with suppress(Exception):
                        await heartbeat

            await _await_terminal(asyncio.create_task(cleanup(), name="planner-terminal-drain"))
