"""Lifecycle-owned, bounded Reader work; no model authority from a lease alone."""

import asyncio
import logging
from contextlib import suppress

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchRun, ResearchSession, ResearchWorkspace
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlanningLease
from app.research import (
    reader_admission,
    reader_control,
    reader_death,
    reader_death_stop,
    reader_finish,
    reader_lease,
    reader_recovery,
    reader_supervision,
    reader_transport,
)
from app.research.contracts import State
from app.research.execution_identity import WorkerIdentity, producer_dead
from app.research.reader_contracts import ReaderToken
from app.research.supervisor import _await_terminal
from app.services import research_service as ledger

logger = logging.getLogger("statforge.research.reader")


class ReaderSupervisor:
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
        self.sdk_failed = set()

    def start(self):
        if self.task is not None:
            raise RuntimeError("Reader supervisor already started.")
        self.task = asyncio.create_task(self._loop(), name="reader-supervisor")

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
                item
                for item in results
                if isinstance(item, BaseException) and not isinstance(item, asyncio.CancelledError)
            )
            if errors:
                raise BaseExceptionGroup("Reader shutdown failures", errors)

        await _await_terminal(asyncio.create_task(drain(), name="reader-shutdown-drain"))

    async def _loop(self):
        while not self.stopping.is_set():
            for attempt, task in list(self.work.items()):
                if task.done():
                    with suppress(asyncio.CancelledError, Exception):
                        task.result()
                    self.work.pop(attempt)
            try:
                if len(self.work) < 2:
                    token = await self._select()
                    if token is not None:
                        self.work[token.attempt_id] = asyncio.create_task(
                            self.execute(token), name="reader-owned-work"
                        )
            except Exception as error:
                logger.warning("READER_ADMISSION_REFUSED: %s", type(error).__name__)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=0.1)
            except TimeoutError:
                pass

    def _owns(self, lease):
        return (lease.worker_id, lease.producer_host, lease.producer_pid, lease.producer_birth) == (
            self.worker.worker_id,
            self.worker.host,
            self.worker.pid,
            self.worker.birth,
        ) and self.worker.validate_current()

    async def _select(self):
        async with self.factory() as db:
            candidate_query = (
                select(ResearchRun, ResearchPlanningLease)
                .join(ResearchAttempt, ResearchAttempt.run_id == ResearchRun.id)
                .join(
                    ResearchPlanningLease,
                    ResearchPlanningLease.attempt_id == ResearchAttempt.id,
                )
                .outerjoin(
                    ResearchHarnessThread,
                    ResearchHarnessThread.attempt_id == ResearchAttempt.id,
                )
                .where(
                    ResearchRun.kind == "reader",
                    ResearchAttempt.number == ResearchRun.attempt_count,
                    (
                        ResearchPlanningLease.released_at_ms.is_(None)
                        | (
                            (ResearchRun.state == "completed")
                            & ResearchHarnessThread.finish_hash.is_(None)
                        )
                    ),
                )
            )
            candidates = (
                await db.execute(
                    candidate_query.with_only_columns(
                        ResearchRun.id, ResearchPlanningLease.attempt_id
                    )
                    .order_by(ResearchRun.created_at, ResearchRun.id)
                    .limit(16)
                )
            ).all()
            for run_id, attempt_id in candidates:
                try:
                    # Keep only identifiers across rollback. A refused candidate
                    # must not leave expired ORM objects or poison the next one.
                    current = (
                        await db.execute(
                            candidate_query.where(
                                ResearchRun.id == run_id,
                                ResearchPlanningLease.attempt_id == attempt_id,
                            )
                        )
                    ).one_or_none()
                    if current is None:
                        continue
                    run, lease = current
                    if lease.attempt_id in self.work or lease.attempt_id in self.sdk_failed:
                        continue
                    if self._owns(lease):
                        token = ReaderToken(
                            lease.owner_id, run.id, lease.attempt_id, lease.worker_id, lease.fence
                        )
                        if run.state == "completed":
                            await reader_finish.terminal_claim(db, token)
                        else:
                            await reader_lease.validate(
                                db, token, running=False, current_source=False
                            )
                        return token
                    if not producer_dead(
                        lease.producer_host, lease.producer_pid, lease.producer_birth
                    ):
                        continue
                    # Final confirm/recover still uses the original owner fence.
                    owner = lease.owner_id
                    await db.rollback()
                    observation = await reader_death.observe(
                        self.factory, owner, run_id, self.worker
                    )
                    if await reader_lease.answer_event(db, attempt_id) is None:
                        await reader_death_stop.stop(db, observation, self.worker)
                        await db.commit()
                        return None
                    token = await reader_recovery.recover(db, observation, self.worker)
                    await db.commit()
                    return token
                except Exception as error:
                    await db.rollback()
                    logger.warning("READER_CANDIDATE_REFUSED: %s", type(error).__name__)
            queued = (
                await db.execute(
                    select(ResearchRun.id, ResearchWorkspace.owner_id)
                    .join(ResearchSession, ResearchSession.id == ResearchRun.session_id)
                    .join(ResearchWorkspace, ResearchWorkspace.id == ResearchSession.workspace_id)
                    .where(
                        ResearchRun.kind == "reader",
                        ResearchRun.state == "queued",
                        ResearchSession.is_archived.is_(False),
                        ResearchSession.is_deleted.is_(False),
                        ResearchWorkspace.is_deleted.is_(False),
                    )
                    .order_by(ResearchRun.created_at, ResearchRun.id)
                    .limit(64)
                )
            ).all()
            for run_id, owner in queued:
                try:
                    run = await db.get(ResearchRun, run_id, populate_existing=True)
                    if run is None or run.state != "queued":
                        continue
                    attempt = await db.scalar(
                        select(ResearchAttempt).where(
                            ResearchAttempt.run_id == run.id,
                            ResearchAttempt.number == run.attempt_count,
                        )
                    )
                    if await reader_admission.load(db, owner, run, attempt) is None:
                        continue
                    if (
                        await db.scalar(
                            select(ResearchPlanningLease.attempt_id).where(
                                ResearchPlanningLease.active_owner_id == owner
                            )
                        )
                        is not None
                    ):
                        continue
                    token = await reader_lease.claim(db, owner, run.id, run.revision, self.worker)
                    await db.commit()
                    return token
                except Exception as error:
                    await db.rollback()
                    logger.warning("READER_QUEUED_REFUSED: %s", type(error).__name__)
        return None

    async def _heartbeat(self, token, done):
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=2)
            except TimeoutError:
                await reader_lease.renew(
                    self.factory, token, allow_completed=True, allow_stopped=True
                )

    async def execute(self, token):
        done = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(token, done), name="reader-heartbeat")
        work = asyncio.create_task(
            reader_supervision.run(
                self.factory, token, self.gateway_factory(), stopping=self.stopping
            ),
            name="reader-sdk-work",
        )
        failure = None
        try:
            finished, _ = await asyncio.wait({heartbeat, work}, return_when=asyncio.FIRST_COMPLETED)
            if work not in finished:
                heartbeat.result()
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
                        # Do not turn an SDK/financial drain failure into an ACK.
                        outcome = (await asyncio.gather(work, return_exceptions=True))[0]
                        if (
                            isinstance(outcome, BaseException)
                            and not isinstance(outcome, asyncio.CancelledError)
                            and outcome is not failure
                        ):
                            raise outcome
                        reader_transport.drained(token)
                        async with self.factory() as db:
                            run, _ = await ledger.run(db, token.run_id, token.owner_id)
                            if run.state in {"completed", "cancelled", "failed", "interrupted"}:
                                return
                            attempt = await db.get(
                                ResearchAttempt, token.attempt_id, populate_existing=True
                            )
                            if await reader_control.cancelled(db, token.owner_id, run, attempt):
                                await reader_control.acknowledge(db, token)
                            elif await reader_lease.answer_event(db, token.attempt_id) is not None:
                                return  # Never finish while pending SDK tool work is unproved.
                            else:
                                after = (
                                    State.INTERRUPTED
                                    if (
                                        isinstance(failure, asyncio.CancelledError)
                                        or run.state in {"waiting_approval", "paused"}
                                    )
                                    else State.FAILED
                                )
                                await reader_lease.stop_owned(db, token, after)
                            await db.commit()
                finally:
                    done.set()
                    await heartbeat

            await _await_terminal(asyncio.create_task(cleanup(), name="reader-terminal-drain"))
