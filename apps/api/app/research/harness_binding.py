"""Immutable server thread identities; business state and permissions stay fresh."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlannerThread
from app.models.user import User
from app.research import execution_lease, publication
from app.research.contracts import State, logical_hash, parse_document
from app.services import research_service as ledger

PROFILES = {
    "business": {
        "version": 1,
        "sdk": "langgraph-functional",
        "domain": "fixed-four",
        "durability": "sync",
    },
    "assistant": {
        "version": 1,
        "sdk": "deepagents-0.7.21",
        "profile": "statforge-formal-v1",
        "durability": "sync",
        "tools": "registered-domain-only",
    },
    "planner": {
        "version": 1,
        "sdk": "deepagents-0.7.21",
        "profile": "statforge:planner-v1",
        "durability": "sync",
        "tools": "inspect/profile/propose_plan/write_todos",
    },
}


def profile(purpose, run_kind, parameters=None):
    if run_kind == "reviewer":
        from app.research.reviewer_contracts import PROFILE

        if purpose != "assistant":
            raise ledger.error("HARNESS_PROFILE_REFUSED")
        return PROFILE
    if run_kind == "reader":
        from app.research import reader_contracts

        if purpose != "assistant":
            raise ledger.error("HARNESS_PROFILE_REFUSED")
        return reader_contracts.view_profile(
            1 if parameters is None else reader_contracts.view_version(parameters)
        )
    if purpose == "planner" and parameters is not None:
        from app.research import planner_v2

        if planner_v2.from_parameters(parameters) is not None:
            return planner_v2.PROFILE
    return PROFILES[purpose]


async def _validate(db, token, *, running=False):
    from app.research.planning_lease import PlanningToken
    from app.research.reader_contracts import ReaderToken
    from app.research.reviewer_contracts import ReviewerToken

    if type(token) is ReviewerToken:
        from app.research import reviewer_lease

        return await reviewer_lease.validate(db, token, running=running)

    if type(token) is ReaderToken:
        from app.research import reader_lease

        return await reader_lease.validate(db, token, running=running)

    if isinstance(token, PlanningToken):
        from app.research import planning_lease

        return await planning_lease.validate(db, token, running=running)
    return await execution_lease.validate(db, token, running=running)


async def _source(db, token, run):
    from app.research.planning_lease import PlanningToken
    from app.research.reader_contracts import ReaderToken
    from app.research.reviewer_contracts import ReviewerToken

    if type(token) is ReviewerToken:
        from app.research import reviewer_lease

        await reviewer_lease.source(db, token.owner_id, run)
    elif type(token) is ReaderToken:
        from app.research import reader_lease

        await reader_lease.source(db, token.owner_id, run)
    elif isinstance(token, PlanningToken):
        from app.research import planning_lease

        await planning_lease.source(db, token.owner_id, run)
    else:
        await execution_lease._source(db, token.owner_id, run)


def document(row):
    return {
        "version": 1,
        "owner_id": str(row.owner_id),
        "run_id": str(row.run_id),
        "attempt_id": str(row.attempt_id),
        "thread_id": row.thread_id,
        "purpose": row.purpose,
        "input_hash": row.input_hash,
        "profile_hash": row.profile_hash,
    }


@dataclass(frozen=True)
class Binding:
    id: uuid.UUID
    owner_id: uuid.UUID
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    thread_id: str
    input_hash: str
    profile_hash: str
    purpose: str
    binding_hash: str


async def bind(factory, token, purpose="business") -> Binding:
    if purpose not in PROFILES:
        raise ledger.error("HARNESS_PROFILE_REFUSED")
    from app.research.planning_lease import PlanningToken
    from app.research.reader_contracts import ReaderToken
    from app.research.reviewer_contracts import ReviewerToken

    if (purpose == "planner") != isinstance(token, PlanningToken):
        raise ledger.error("HARNESS_PROFILE_REFUSED")
    if type(token) in {ReaderToken, ReviewerToken} and purpose != "assistant":
        raise ledger.error("HARNESS_PROFILE_REFUSED")
    model = ResearchPlannerThread if purpose == "planner" else ResearchHarnessThread
    async with factory() as db:
        run, scope, _lease, _now = await _validate(db, token, running=False)
        if purpose != "planner" and type(token) not in {ReaderToken, ReviewerToken}:
            # Planning validation already proves the full source in this transaction.
            await _source(db, token, run)
        row = await db.scalar(
            select(model)
            .where(
                model.attempt_id == token.attempt_id,
                model.purpose == purpose,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            row = model(
                id=uuid.uuid4(),
                owner_id=token.owner_id,
                run_id=run.id,
                attempt_id=token.attempt_id,
                purpose=purpose,
                thread_id=uuid.uuid4().hex,
                input_hash=run.input_hash,
                profile_hash=logical_hash(
                    profile(purpose, run.kind, parse_document(run.input_json)["parameters"])
                ),
            )
            row.binding_hash = logical_hash(document(row))
            db.add(row)
            await ledger._append(
                db, scope, token.owner_id, "run", run.id, "harness.thread_bound", document(row)
            )
        _verify(row, token, run)
        result = Binding(**{field: getattr(row, field) for field in Binding.__dataclass_fields__})
        await db.commit()
    return result


async def existing(factory, token, purpose="business") -> Binding | None:
    model = ResearchPlannerThread if purpose == "planner" else ResearchHarnessThread
    async with factory() as db:
        row = await db.scalar(
            select(model)
            .where(
                model.attempt_id == token.attempt_id,
                model.purpose == purpose,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        run, _ = await ledger.run(db, token.run_id, token.owner_id)
        _verify(row, token, run)
        result = Binding(**{field: getattr(row, field) for field in Binding.__dataclass_fields__})
    await authorize(factory, result, token)
    return result


def _verify(row, token, run=None):
    from app.research.planning_lease import PlanningToken
    from app.research.reader_contracts import ReaderToken
    from app.research.reviewer_contracts import ReviewerToken

    if (row.purpose == "planner") != isinstance(token, PlanningToken):
        raise ledger.error("HARNESS_BINDING_CHANGED")
    if type(token) in {ReaderToken, ReviewerToken} and row.purpose != "assistant":
        raise ledger.error("HARNESS_BINDING_CHANGED")
    expected_kind = (
        "reviewer"
        if type(token) is ReviewerToken
        else "reader"
        if type(token) is ReaderToken
        else "native"
    )
    if expected_kind in {"reader", "reviewer"} and run is not None and run.kind != expected_kind:
        raise ledger.error("HARNESS_BINDING_CHANGED")
    if (row.owner_id, row.run_id, row.attempt_id) != (
        token.owner_id,
        token.run_id,
        token.attempt_id,
    ) or (
        row.purpose not in PROFILES
        or row.profile_hash
        != logical_hash(
            profile(
                row.purpose,
                expected_kind,
                parse_document(run.input_json)["parameters"] if run is not None else None,
            )
        )
        or row.binding_hash != logical_hash(document(row))
        or uuid.UUID(row.thread_id).hex != row.thread_id
    ):
        raise ledger.error("HARNESS_BINDING_CHANGED")


async def _bound_in_session(db, binding: Binding, token):
    """The original binding checks, with no source proof or caller-owned flags."""
    model = ResearchPlannerThread if binding.purpose == "planner" else ResearchHarnessThread
    row = await db.get(model, binding.id, populate_existing=True)
    actor = await db.get(User, token.owner_id, populate_existing=True)
    if row is None or actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    run, _scope = await ledger.run(db, token.run_id, token.owner_id)
    _verify(row, token, run)
    if any(
        getattr(row, field) != getattr(binding, field) for field in Binding.__dataclass_fields__
    ):
        raise ledger.error("HARNESS_BINDING_CHANGED")
    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    if (
        attempt is None
        or attempt.run_id != run.id
        or attempt.number != run.attempt_count
        or run.input_hash != binding.input_hash
        or logical_hash(parse_document(run.input_json)) != run.input_hash
    ):
        raise ledger.error("HARNESS_BINDING_CHANGED")
    return run, attempt


async def authorize(factory, binding: Binding, token, *, checkpoint=False):
    from app.research.reader_contracts import ReaderToken
    from app.research.reviewer_contracts import ReviewerToken

    if checkpoint and type(token) not in {ReaderToken, ReviewerToken}:
        raise ledger.error("HARNESS_BINDING_CHANGED")
    async with factory() as db:
        run, attempt = await _bound_in_session(db, binding, token)
        terminal = run.state in {State.COMPLETED.value, State.FAILED.value}
        if not terminal:
            if binding.purpose == "planner":
                from app.models.research_planning import ResearchPlanReceipt
                from app.research import plan_receipt, planning_lease

                receipt = await db.scalar(
                    select(ResearchPlanReceipt.id).where(
                        ResearchPlanReceipt.attempt_id == token.attempt_id
                    )
                )
                if receipt is not None:
                    await plan_receipt.prove(db, token.owner_id, run, attempt)
                    await planning_lease.validate(db, token, running=False, current_source=False)
                else:
                    await _validate(db, token, running=False)
                    # Planning validation already proves the full live source here.
            elif run.kind == "reviewer":
                from app.research import reviewer_lease, reviewer_receipt

                if checkpoint:
                    await reviewer_lease.validate(db, token, running=False, current_source=False)
                elif await reviewer_lease.review_event(db, attempt.id) is not None:
                    await reviewer_receipt.prove(db, token.owner_id, run, attempt)
                    await reviewer_lease.validate(db, token, running=False, current_source=False)
                else:
                    await _validate(db, token, running=False)
            elif run.kind == "reader":
                from app.research import reader_answer, reader_lease

                if checkpoint:
                    # SDK bookkeeping cannot authorize a new source or tool.
                    await reader_lease.validate(db, token, running=False, current_source=False)
                elif await reader_lease.answer_event(db, attempt.id) is not None:
                    await reader_answer.prove(db, token.owner_id, run, attempt)
                    await reader_lease.validate(db, token, running=False, current_source=False)
                else:
                    await _validate(db, token, running=False)
            else:
                await _validate(db, token, running=False)
                from app.research.reader_contracts import ReaderToken

                if type(token) not in {ReaderToken, ReviewerToken}:
                    await _source(db, token, run)
        if terminal and binding.purpose == "planner":
            from app.research import plan_receipt

            receipt = await plan_receipt.prove(db, token.owner_id, run, attempt)
            if run.state != State.COMPLETED.value or run.completion_hash != receipt.document_hash:
                raise ledger.error("HARNESS_TERMINAL_UNPROVEN")
        elif terminal and run.kind == "reviewer":
            from app.research import reviewer_finish

            await reviewer_finish.terminal_claim(db, token)
        elif terminal and run.kind == "reader":
            from app.research import reader_finish

            await reader_finish.terminal_claim(db, token)
    if (
        terminal
        and binding.purpose != "planner"
        and run.kind not in {"reader", "reviewer"}
        and await publication._confirmed(factory, token) is None
    ):
        raise ledger.error("HARNESS_TERMINAL_UNPROVEN")
