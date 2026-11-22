"""A genuine final reply without a Plan is waiting for input, never completion."""

import hashlib
import re
import uuid

from sqlalchemy import select

from app.models.research import ResearchArtifact, ResearchAttempt, ResearchEvent
from app.models.research_planning import ResearchPlannerThread, ResearchPlanReceipt
from app.research import harness_binding, planner_finish, planning_lease, saved_replies
from app.research.contracts import State, logical_hash, parse_document
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger

EVENT = "planning.unresolved_recorded"
CANCEL = "planning.unresolved_cancelled"
FIELDS = frozenset(
    "version outcome owner_id workspace_id session_id run_id attempt_id input_hash "
    "source_version binding_id binding_hash profile_hash fence worker_id producer_host "
    "producer_pid producer_birth checkpoint_id state_hash reply_bundle_hash last_reply_hash "
    "reply_count execution_kinds released_at_ms revision_before state_before "
    "workspace_event_sequence workspace_event_head_sha256".split()
)


async def event(db, run, attempt):
    rows = (
        await db.scalars(
            select(ResearchEvent).where(
                ResearchEvent.entity_id == run.id, ResearchEvent.event_type == EVENT
            )
        )
    ).all()
    selected = [
        x for x in rows if parse_document(x.payload_json).get("attempt_id") == str(attempt.id)
    ]
    if len(selected) > 1:
        raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")
    return selected[0] if selected else None


async def _replies(db, owner, run, attempt):
    bundle = await saved_replies.read(db, owner, run.id)
    replies = bundle["replies"]
    if (
        not replies
        or any(x["attempt_id"] != str(attempt.id) for x in replies)
        or not isinstance(replies[-1]["advisory"]["text"], str)
        or not replies[-1]["advisory"]["text"].strip()
        or replies[-1]["advisory"]["tool_calls"]
    ):
        raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")
    return bundle


async def _empty(db, run, attempt):
    if (
        any(
            getattr(run, x) is not None for x in ("domain_kind", "domain_run_id", "completion_hash")
        )
        or any(
            getattr(attempt, x) is not None
            for x in ("domain_kind", "domain_run_id", "completion_hash")
        )
        or await db.scalar(
            select(ResearchPlanReceipt.id).where(ResearchPlanReceipt.run_id == run.id)
        )
        is not None
        or await db.scalar(select(ResearchArtifact.id).where(ResearchArtifact.run_id == run.id))
        is not None
    ):
        raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")


async def prove(db, owner, run, attempt):
    """Historical proof only: no current source, model send, or SDK write authority."""
    if attempt is None or attempt.run_id != run.id:
        raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")
    row = await event(db, run, attempt)
    if row is None:
        return None
    _, scope = await ledger.run(db, run.id, owner)
    from app.models.research_planning import ResearchPlanningLease

    lease = await db.get(ResearchPlanningLease, attempt.id, populate_existing=True)
    value = parse_document(row.payload_json)
    try:
        binding_id = uuid.UUID(value["binding_id"])
    except (ValueError, KeyError, TypeError):
        raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN") from None
    thread = await db.get(ResearchPlannerThread, binding_id, populate_existing=True)
    if thread is None:
        raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")
    bundle = await _replies(db, owner, run, attempt)
    await _empty(db, run, attempt)
    expected = dict(
        version=1,
        outcome="awaiting_input",
        owner_id=str(owner),
        workspace_id=str(scope.id),
        session_id=str(run.session_id),
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        input_hash=run.input_hash,
        source_version=parse_document(run.input_json)["parameters"]["source_version"],
        binding_id=str(thread.id),
        binding_hash=thread.binding_hash,
        profile_hash=thread.profile_hash,
        fence=lease.fence if lease else None,
        worker_id=lease.worker_id if lease else None,
        producer_host=lease.producer_host if lease else None,
        producer_pid=lease.producer_pid if lease else None,
        producer_birth=lease.producer_birth if lease else None,
        checkpoint_id=value.get("checkpoint_id"),
        state_hash=value.get("state_hash"),
        reply_bundle_hash=logical_hash(bundle["replies"]),
        last_reply_hash=bundle["replies"][-1]["reply_receipt_hash"],
        reply_count=len(bundle["replies"]),
        execution_kinds=sorted({x["execution_kind"] for x in bundle["replies"]}),
        released_at_ms=lease.released_at_ms if lease else None,
        revision_before=value.get("revision_before"),
        state_before="running",
        workspace_event_sequence=row.sequence - 1,
        workspace_event_head_sha256=row.previous_hash,
    )
    if (
        set(value) != FIELDS
        or any(
            type(value.get(x)) is not int
            for x in (
                "version",
                "fence",
                "producer_pid",
                "released_at_ms",
                "revision_before",
                "reply_count",
                "workspace_event_sequence",
            )
        )
        or value != expected
        or type(value["version"]) is not int
        or row.logical_hash != logical_hash(value)
        or row.entity_kind != "run"
        or row.workspace_id != scope.id
        or row.actor_id != owner
        or run.kind != "planner"
        or attempt.run_id != run.id
        or attempt.number != run.attempt_count
        or thread.owner_id != owner
        or thread.run_id != run.id
        or thread.attempt_id != attempt.id
        or thread.input_hash != run.input_hash
        or thread.purpose != "planner"
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or thread.profile_hash
        != logical_hash(
            harness_binding.profile(
                "planner", run.kind, parse_document(run.input_json)["parameters"]
            )
        )
        or lease is None
        or lease.owner_id != owner
        or lease.active_owner_id is not None
        or type(value["released_at_ms"]) is not int
        or lease.pause_requested
        or type(value["revision_before"]) is not int
        or value["revision_before"] < 1
        or run.revision <= value["revision_before"]
        or run.state not in {"paused", "cancelled"}
        or attempt.state != run.state
        or not isinstance(value["checkpoint_id"], str)
        or not 1 <= len(value["checkpoint_id"]) <= 128
        or not isinstance(value["state_hash"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["state_hash"]) is None
        or any(
            max(x["recorded_sequence"], x["resumed_sequence"]) >= row.sequence
            for x in bundle["replies"]
        )
    ):
        raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")
    return row


async def record(factory, token, binding, graph, saver, model):
    # Repeat closeout reads the exact existing receipt; no post-release SDK authorization.
    async with factory() as db:
        await planning_lease.execution_lease._boundary(db, token.owner_id)
        run, _ = await ledger.run(db, token.run_id, token.owner_id)
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        old = await prove(db, token.owner_id, run, attempt)
        if old is not None:
            value = parse_document(old.payload_json)
            if any(
                value[key] != expected
                for key, expected in (
                    ("binding_id", str(binding.id)),
                    ("binding_hash", binding.binding_hash),
                    ("fence", token.fence),
                    ("worker_id", token.worker_id),
                )
            ):
                raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")
            return old
    state, checkpoint_id = await planner_finish.fixed_snapshot(graph, saver, binding)
    state_hash = hashlib.sha256(SafeSerializer().dumps_typed(state.values)[1]).hexdigest()
    await model.verify_state(state.values)
    async with factory() as db:
        run, scope, lease, now = await planning_lease.validate(db, token, running=False)
        if lease.pause_requested or run.state == "paused":
            raise ledger.error("MODEL_PAUSED")
        if run.state != "running":
            raise ledger.error("PLANNER_UNRESOLVED_UNPROVEN")
        attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
        await harness_binding._bound_in_session(db, binding, token)
        await _empty(db, run, attempt)
        bundle = await _replies(db, token.owner_id, run, attempt)
        value = dict(
            version=1,
            outcome="awaiting_input",
            owner_id=str(token.owner_id),
            workspace_id=str(scope.id),
            session_id=str(run.session_id),
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            input_hash=run.input_hash,
            source_version=parse_document(run.input_json)["parameters"]["source_version"],
            binding_id=str(binding.id),
            binding_hash=binding.binding_hash,
            profile_hash=binding.profile_hash,
            fence=token.fence,
            worker_id=lease.worker_id,
            producer_host=lease.producer_host,
            producer_pid=lease.producer_pid,
            producer_birth=lease.producer_birth,
            checkpoint_id=checkpoint_id,
            state_hash=state_hash,
            reply_bundle_hash=logical_hash(bundle["replies"]),
            last_reply_hash=bundle["replies"][-1]["reply_receipt_hash"],
            reply_count=len(bundle["replies"]),
            execution_kinds=sorted({x["execution_kind"] for x in bundle["replies"]}),
            released_at_ms=now,
            revision_before=run.revision,
            state_before=run.state,
            workspace_event_sequence=bundle["workspace_event_sequence"],
            workspace_event_head_sha256=bundle["workspace_event_head_sha256"],
        )
        row = await ledger._append(db, scope, token.owner_id, "run", run.id, EVENT, value)
        lease.active_owner_id, lease.released_at_ms, lease.pause_requested = None, now, False
        await ledger.transition(db, token.owner_id, run.id, run.revision, State.PAUSED)
        await db.commit()
        return row


async def read(db, owner, run_id):
    from app.research.replay import verify_business

    run, scope = await ledger.run(db, run_id, owner)
    if run.kind != "planner":
        raise ledger.error("PLANNING_UNSUPPORTED")
    await verify_business(db, owner, scope.id)
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == run.id,
            ResearchAttempt.number == run.attempt_count,
        )
    )
    if attempt is None:
        raise ledger.error("STATE_INCONSISTENT")
    row = await prove(db, owner, run, attempt)
    if row is None:
        return None
    bundle = await _replies(db, owner, run, attempt)
    return dict(
        version=1,
        outcome="awaiting_input",
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        receipt_sha256=row.logical_hash,
        advisory=bundle["replies"][-1]["advisory"]["text"],
        execution_kinds=parse_document(row.payload_json)["execution_kinds"],
        business_completion="NOT_VERIFIED",
        model_quality="NOT_VERIFIED",
    )
