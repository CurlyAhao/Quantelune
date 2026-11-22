"""Bounded lifecycle-owned DAG reconciliation; no native slot or generic agent loop."""

import asyncio
import logging
from collections import OrderedDict

from sqlalchemy import exists, select

from app.models.research import ResearchRun, ResearchWorkspace
from app.models.research_planning import ResearchPlanAcceptance, ResearchPlanStep
from app.research import plan_execution
from app.research.supervisor import _await_terminal

logger = logging.getLogger("statforge.research.plan_coordinator")


class PlanCoordinator:
    def __init__(self, factory):
        self.factory = factory
        self.stopping = asyncio.Event()
        self.task = None
        self.blocked = OrderedDict()
        self.cursor = None

    async def _candidates(self):
        async with self.factory() as db:
            query = select(ResearchPlanAcceptance).where(
                exists(
                    select(ResearchPlanStep.id).where(
                        ResearchPlanStep.acceptance_id == ResearchPlanAcceptance.id,
                        ResearchPlanStep.result_hash.is_(None),
                    )
                )
            )
            if self.cursor is not None:
                query = query.where(ResearchPlanAcceptance.id > self.cursor)
            rows = (await db.scalars(query.order_by(ResearchPlanAcceptance.id).limit(64))).all()
            self.cursor = rows[-1].id if rows else None
            return [(row.id, row.owner_id) for row in rows]

    def _remember(self, identity, signature):
        self.blocked[identity] = signature
        self.blocked.move_to_end(identity)
        while len(self.blocked) > 256:
            self.blocked.popitem(last=False)

    def start(self):
        if self.task is not None:
            raise RuntimeError("Plan coordinator already started.")
        self.task = asyncio.create_task(self._loop(), name="plan-coordinator")

    async def close(self):
        self.stopping.set()
        if self.task is not None:
            await _await_terminal(self.task)

    async def _loop(self):
        while not self.stopping.is_set():
            try:
                identifiers = await self._candidates()
                for identity, owner in identifiers:
                    if self.stopping.is_set():
                        break
                    try:
                        async with self.factory() as db:
                            acceptance, scope, plan, steps = await plan_execution.load(
                                db, owner, identity, replay=False
                            )
                            workspace = await db.get(
                                ResearchWorkspace, scope.id, populate_existing=True
                            )
                            children = (
                                await db.execute(
                                    select(
                                        ResearchRun.id,
                                        ResearchRun.state,
                                        ResearchRun.revision,
                                        ResearchRun.attempt_count,
                                        ResearchRun.completion_hash,
                                    ).where(
                                        ResearchRun.id.in_(
                                            [
                                                step.child_run_id
                                                for step in steps.values()
                                                if step.child_run_id
                                            ]
                                        )
                                    )
                                )
                            ).all()
                            from app.models.profiling import DatasetProfile

                            profile_signatures = []
                            for definition in plan.steps:
                                if (
                                    definition.capability == "profile"
                                    and steps[definition.id].result_hash is None
                                ):
                                    latest = (
                                        await db.execute(
                                            select(
                                                DatasetProfile.id,
                                                DatasetProfile.status,
                                                DatasetProfile.updated_at,
                                            )
                                            .where(
                                                DatasetProfile.dataset_id == definition.dataset_id
                                            )
                                            .order_by(
                                                DatasetProfile.created_at.desc(), DatasetProfile.id
                                            )
                                            .limit(1)
                                        )
                                    ).one_or_none()
                                    profile_signatures.append(tuple(latest) if latest else ())
                            signature = (
                                workspace.event_head_hash,
                                acceptance.revision,
                                tuple(tuple(row) for row in children),
                                tuple(profile_signatures),
                            )
                        if self.blocked.get(identity) == signature:
                            continue  # No repeated unchanged failed reconciliation/compute.
                        try:
                            async with self.factory() as db:
                                await plan_execution.advance(db, owner, identity)
                                await db.commit()
                            self._remember(identity, signature)
                        except Exception as error:
                            self._remember(identity, signature)
                            logger.warning("PLAN_RECONCILIATION_PENDING: %s", type(error).__name__)
                    except Exception as error:
                        logger.warning("PLAN_CANDIDATE_PENDING: %s", type(error).__name__)
            except Exception as error:
                logger.warning("PLAN_SELECTION_PENDING: %s", type(error).__name__)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=1)
            except TimeoutError:
                pass
