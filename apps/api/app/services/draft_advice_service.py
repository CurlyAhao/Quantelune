"""Finite draft requests on the formal model gateway, never a new agent loop."""

import asyncio
import uuid

from sqlalchemy import select

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.models.research import ResearchEvent, ResearchRun, ResearchSession, ResearchWorkspace
from app.models.research_harness import ResearchAdvisoryReceipt
from app.models.research_security import ResearchApproval, ResearchCharge
from app.models.workspace import WorkspaceRecord, WorkspaceRevision
from app.preview.workspace import credential_free, save_record
from app.preview.write_fence import acquire_write_fence
from app.research import advisory_receipt
from app.research import draft_advice_gateway as profile
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document
from app.research.draft_advice import PROFILE, AdviceContext, check_proposal
from app.schemas.draft_advice import CodingAdviceContextRequest
from app.schemas.workspace import PlotDraft, SaveRecord
from app.services import research_security_service as security
from app.services import research_service as ledger

INSTANCE = uuid.uuid4()
JOBS = {}
EVENTS = frozenset(
    {"advice.dispatched", "advice.cancel_requested", "advice.proposed", "advice.applied"}
)


def fail(message="建议暂时无法使用，请重试。", status=409):
    return StatForgeError(status, "DRAFT_ADVICE_UNAVAILABLE", message)


def require_dispatch_task(owner, run_id):
    registered = JOBS.get(run_id)
    if registered is None or registered[0] != owner or registered[1] is not asyncio.current_task():
        raise fail("请从当前建议发送请求。")


async def event(db, run, name):
    return await db.scalar(
        select(ResearchEvent)
        .where(ResearchEvent.entity_id == run.id, ResearchEvent.event_type == name)
        .order_by(ResearchEvent.sequence.desc())
        .limit(1)
    )


async def append(db, owner, run, name, payload):
    _, workspace = await ledger.run(db, run.id, owner)
    await ledger._append(
        db, workspace, owner, "draft_advice", run.id, name, {"run_id": str(run.id), **payload}
    )


async def owned(db, owner, project, identity):
    run, workspace = await ledger.run(db, identity, owner)
    if workspace.project_id != project or run.kind != profile.KIND:
        raise fail("没有找到这条建议。", 404)
    profile.parameters(run)
    return run


async def create(db, user, project, body, gateway):
    await acquire_write_fence(db, user.id)
    await ledger._project(db, project, user.id)
    if not body.question.strip():
        raise fail("请填写想讨论的问题。", 422)
    target_id = body.target.draft_id if body.target.kind == "plot" else project
    key = f"draft-advice:{body.target.kind}:{target_id}:{body.request_id}"
    old = await db.scalar(
        select(ResearchRun)
        .join(ResearchSession, ResearchRun.session_id == ResearchSession.id)
        .join(ResearchWorkspace, ResearchSession.workspace_id == ResearchWorkspace.id)
        .where(
            ResearchWorkspace.owner_id == user.id,
            ResearchWorkspace.project_id == project,
            ResearchRun.kind == profile.KIND,
            ResearchRun.idempotency_key == key,
        )
    )
    target = body.target.model_dump(mode="json", exclude={"kind"})
    if old:
        prior = profile.parameters(old)
        if (
            prior["question"] != body.question
            or prior["target"] != target
            or prior["source_version"] != body.source_version
        ):
            raise fail("请求已变化，请重新生成建议。")
        return await read(db, user, project, old.id, gateway)
    if body.target.kind == "plot":
        from app.research.draft_advice import load_plot_context

        context = await load_plot_context(
            db, project, body.target.draft_id, user, body.target.revision
        )
    elif body.target.kind == "selection":
        from app.services.coding_suggestions import selection_context

        context = await selection_context(db, project, user, body.target)
    else:
        from app.research.draft_advice import load_coding_context

        context = await load_coding_context(
            db, project, user, CodingAdviceContextRequest.model_validate(target)
        )
    if context.source_version != body.source_version:
        raise fail("内容已变化，请保存后重新生成建议。")
    parameters = {
        "version": profile.VERSION,
        "project_id": str(project),
        "request_id": str(body.request_id),
        "question": body.question,
        "target": target,
        "context": context.document,
        "source_version": context.source_version,
        "profile_hash": logical_hash(PROFILE),
    }
    await credential_free(db, user, parameters)
    workspace = await db.scalar(
        select(ResearchWorkspace)
        .where(
            ResearchWorkspace.owner_id == user.id,
            ResearchWorkspace.project_id == project,
            ResearchWorkspace.title == "研究建议",
            ResearchWorkspace.is_deleted.is_(False),
        )
        .order_by(ResearchWorkspace.created_at)
        .limit(1)
    )
    if workspace is None:
        workspace = await ledger.create_workspace(db, user.id, "研究建议", project)
    session = await ledger.create_session(
        db, user.id, workspace.id, "图形建议" if body.target.kind == "plot" else "编码建议"
    )
    run = await ledger.start_run(db, user.id, session.id, profile.KIND, key, parameters)
    await ledger.transition(db, user.id, run.id, run.revision, State.RUNNING)
    await ledger.transition(db, user.id, run.id, run.revision, State.WAITING_APPROVAL)
    chosen = profile.selection(parameters)
    prepared = await gateway.prepare(db, user.id, run.id, chosen)
    gateway.guard(prepared)
    await gateway.propose(db, user.id, run.id, "draft-advice", chosen)
    return await read(db, user, project, run.id, gateway)


async def approval(db, owner, run):
    rows = (
        await db.scalars(
            select(ResearchApproval)
            .where(ResearchApproval.owner_id == owner, ResearchApproval.run_id == run.id)
            .limit(2)
        )
    ).all()
    if len(rows) != 1:
        raise fail()
    return rows[0]


async def recent(db, user, project, kind, draft_id):
    await ledger._project(db, project, user.id)
    if kind == "plot" and draft_id is None:
        raise fail("请先保存图形。", 422)
    target = draft_id if kind == "plot" else project
    rows = (
        await db.scalars(
            select(ResearchRun)
            .join(ResearchSession, ResearchRun.session_id == ResearchSession.id)
            .join(ResearchWorkspace, ResearchSession.workspace_id == ResearchWorkspace.id)
            .where(
                ResearchWorkspace.owner_id == user.id,
                ResearchWorkspace.project_id == project,
                ResearchWorkspace.is_deleted.is_(False),
                ResearchRun.kind == profile.KIND,
                ResearchRun.idempotency_key.startswith(f"draft-advice:{kind}:{target}:"),
            )
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
            .limit(20)
        )
    ).all()
    return [{"id": str(row.id), "question": profile.parameters(row)["question"]} for row in rows]


async def proposed(db, owner, run):
    saved = await event(db, run, "advice.proposed")
    if saved is None:
        return None
    await prove_event(db, owner, saved)
    return parse_document(saved.payload_json)


async def read(db, user, project, identity, gateway):
    run = await owned(db, user.id, project, identity)
    source = profile.parameters(run)
    permitted = await approval(db, user.id, run)
    actual = await proposed(db, user.id, run)
    applied = await event(db, run, "advice.applied")
    if applied:
        await prove_event(db, user.id, applied)
    cancelled = await event(db, run, "advice.cancel_requested") or run.state == "cancel_requested"
    dispatched = await event(db, run, "advice.dispatched")
    if actual:
        state = "applied" if applied else "proposed"
    elif run.state in {"failed", "interrupted", "cancelled"}:
        state = run.state
    elif cancelled:
        state = "cancel_requested"
    elif dispatched:
        state = "running"
    else:
        state = "waiting_approval"
    # A process restart never resends a possibly charged model request.
    if state in {"running", "cancel_requested"} and run.id not in JOBS:
        await acquire_write_fence(db, user.id)
        await db.refresh(run)
        if run.state == "completed":
            return await read(db, user, project, identity, gateway)
        charges = (
            await db.scalars(select(ResearchCharge).where(ResearchCharge.run_id == run.id))
        ).all()
        for charge in charges:
            if charge.state in {"reserved", "dispatched"}:
                await security.settle(
                    db,
                    user.id,
                    charge.id,
                    outcome="not_dispatched" if charge.state == "reserved" else "unknown",
                )
        if cancelled:
            if run.state == "running":
                await ledger.transition(db, user.id, run.id, run.revision, State.CANCEL_REQUESTED)
            run = await ledger.transition(db, user.id, run.id, run.revision, State.CANCELLED)
            state = "cancelled"
        else:
            settled = next((charge for charge in charges if charge.state == "settled"), None)
            if settled:
                try:
                    await collect(db, user.id, run, settled.id)
                    actual = await proposed(db, user.id, run)
                    state = "proposed"
                except (ValueError, StatForgeError):
                    if run.state == "waiting_approval":
                        await ledger.transition(db, user.id, run.id, run.revision, State.RUNNING)
                    run = await ledger.transition(db, user.id, run.id, run.revision, State.FAILED)
                    state = "failed"
            else:
                run = await ledger.transition(db, user.id, run.id, run.revision, State.INTERRUPTED)
                state = "interrupted"
    from app.services.research_gateway_service import approval_summary

    return {
        "id": str(run.id),
        "project_id": str(project),
        "kind": source["context"]["kind"],
        "state": state,
        "source_version": source["source_version"],
        "question": source["question"],
        "context": source["context"],
        "selection": profile.selection(source).model_dump(mode="json"),
        "approval": approval_summary(permitted),
        "proposal": actual["proposal"] if actual else None,
        "applied": parse_document(applied.payload_json) if applied else None,
        "error": ("建议请求未完成，请重新生成。" if state in {"failed", "interrupted"} else None),
    }


async def send(db, user, project, identity, body, gateway):
    await acquire_write_fence(db, user.id)
    run = await owned(db, user.id, project, identity)
    if await event(db, run, "advice.proposed") or run.id in JOBS:
        return await read(db, user, project, identity, gateway)
    if (
        run.state != "waiting_approval"
        or await event(db, run, "advice.dispatched")
        or await event(db, run, "advice.cancel_requested")
    ):
        raise fail("请重新生成建议。")
    permitted = await approval(db, user.id, run)
    if permitted.id != body.approval_id or permitted.request_hash != body.request_hash:
        raise fail("发送内容已变化，请重新生成建议。")
    chosen = profile.selection(profile.parameters(run))
    if permitted.state == "pending":
        await gateway.decide(db, user.id, permitted.id, body.request_hash, "approve", chosen)
    elif permitted.state != "approved":
        raise fail("授权已失效，请重新生成建议。")
    await append(
        db,
        user.id,
        run,
        "advice.dispatched",
        {"instance": str(INSTANCE), "approval_id": str(permitted.id)},
    )
    await db.commit()
    task = asyncio.create_task(
        execute(user.id, project, run.id, gateway), name=f"draft-advice:{run.id}"
    )
    JOBS[run.id] = (user.id, task)

    def finished(completed):
        registered = JOBS.get(run.id)
        if registered and registered[1] is completed:
            JOBS.pop(run.id, None)

    task.add_done_callback(finished)
    return await read(db, user, project, identity, gateway)


async def execute(owner, project, identity, gateway):
    try:
        async with async_session_factory() as db:
            run = await owned(db, owner, project, identity)
            permitted = await approval(db, owner, run)
            result = await gateway.dispatch(
                db,
                owner,
                permitted.id,
                permitted.request_hash,
                "draft-advice",
                profile.selection(profile.parameters(run)),
            )
            if await event(db, run, "advice.cancel_requested"):
                raise asyncio.CancelledError
            await collect(db, owner, run, uuid.UUID(result["charge_id"]))
            await db.commit()
    except BaseException as error:
        async with async_session_factory() as db:
            try:
                await acquire_write_fence(db, owner)
                run = await owned(db, owner, project, identity)
                if run.state in {"waiting_approval", "running", "cancel_requested"}:
                    if (
                        isinstance(error, asyncio.CancelledError)
                        or run.state == "cancel_requested"
                        or await event(db, run, "advice.cancel_requested")
                    ):
                        if run.state == "running":
                            await ledger.transition(
                                db, owner, run.id, run.revision, State.CANCEL_REQUESTED
                            )
                        await ledger.transition(db, owner, run.id, run.revision, State.CANCELLED)
                    else:
                        if run.state == "waiting_approval":
                            await ledger.transition(db, owner, run.id, run.revision, State.RUNNING)
                        await ledger.transition(db, owner, run.id, run.revision, State.FAILED)
                    await db.commit()
            except Exception:
                await db.rollback()
    finally:
        registered = JOBS.get(identity)
        if registered and registered[1] is asyncio.current_task():
            JOBS.pop(identity, None)


async def collect(db, owner, run, charge_id):
    await acquire_write_fence(db, owner)
    row = await db.get(ResearchAdvisoryReceipt, charge_id, populate_existing=True)
    charge = await db.get(ResearchCharge, charge_id, populate_existing=True)
    if row is None or charge is None:
        raise fail()
    permitted = await security.approval(db, owner, charge.approval_id)
    actual = await advisory_receipt.resume(db, owner, permitted, charge)
    if not actual or actual["execution_kind"] != "provider":
        raise fail("没有收到可验证的模型回复。")
    source = profile.parameters(run)
    proposal = checked_reply(source, actual["advisory"])
    await append(
        db,
        owner,
        run,
        "advice.proposed",
        {
            "charge_id": str(charge.id),
            "reply_hash": row.document_hash,
            "source_version": source["source_version"],
            "proposal": proposal,
            "proposal_hash": logical_hash(proposal),
        },
    )
    run.domain_kind = profile.KIND
    run.domain_run_id = charge.id
    await ledger.transition(db, owner, run.id, run.revision, State.COMPLETED)


def checked_reply(source, advisory):
    calls = advisory["tool_calls"]
    expected = "propose_" + source["context"]["kind"]
    if len(calls) != 1 or calls[0]["name"] != expected or set(calls[0]["args"]) != {"spec"}:
        raise fail("模型未返回可编辑建议，请调整问题后重试。")
    context = AdviceContext(canonical_bytes(source["context"]).decode())
    return check_proposal(context, calls[0]["args"]["spec"])


async def cancel(db, user, project, identity, gateway):
    await acquire_write_fence(db, user.id)
    run = await owned(db, user.id, project, identity)
    if run.state not in {"waiting_approval", "running", "cancel_requested"}:
        return await read(db, user, project, identity, gateway)
    if not await event(db, run, "advice.cancel_requested"):
        await append(db, user.id, run, "advice.cancel_requested", {})
    await db.commit()
    registered = JOBS.get(run.id)
    if registered and registered[0] == user.id:
        registered[1].cancel()
        # The job records cancellation only after the gateway's financial cleanup.
    else:
        await acquire_write_fence(db, user.id)
        run = await owned(db, user.id, project, identity)
        if run.state == "running":
            await ledger.transition(db, user.id, run.id, run.revision, State.CANCEL_REQUESTED)
        if run.state in {"waiting_approval", "cancel_requested"}:
            await ledger.transition(db, user.id, run.id, run.revision, State.CANCELLED)
    return await read(db, user, project, identity, gateway)


async def prove_event(db, owner, saved):
    if (
        saved.event_type not in EVENTS
        or saved.entity_kind != "draft_advice"
        or saved.actor_id != owner
    ):
        raise fail()
    value = parse_document(saved.payload_json)
    run, workspace = await ledger.run(db, saved.entity_id, owner)
    source = profile.parameters(run)
    if (
        saved.workspace_id != workspace.id
        or value.get("run_id") != str(run.id)
        or logical_hash(value) != saved.logical_hash
    ):
        raise fail()
    if saved.event_type == "advice.proposed":
        if (
            set(value)
            != {"run_id", "charge_id", "reply_hash", "source_version", "proposal", "proposal_hash"}
            or value["source_version"] != source["source_version"]
            or logical_hash(value["proposal"]) != value["proposal_hash"]
        ):
            raise fail()
        charge = await security.charge(db, owner, uuid.UUID(value["charge_id"]))
        permitted = await security.approval(db, owner, charge.approval_id)
        receipt = await db.get(ResearchAdvisoryReceipt, charge.id, populate_existing=True)
        actual = await advisory_receipt.read(db, owner, permitted, charge, historical=True)
        if (
            receipt is None
            or receipt.run_id != run.id
            or receipt.document_hash != value["reply_hash"]
            or not actual
            or actual["execution_kind"] != "provider"
            or canonical_bytes(checked_reply(source, actual["advisory"]))
            != canonical_bytes(value["proposal"])
        ):
            raise fail()
    elif saved.event_type == "advice.applied":
        if (
            set(value)
            != {
                "run_id",
                "source_version",
                "proposal_hash",
                "accepted",
                "record_id",
                "revision",
                "record_hash",
            }
            or value["source_version"] != source["source_version"]
            or logical_hash(value["accepted"]) != value["proposal_hash"]
        ):
            raise fail()
        revision = await db.scalar(
            select(WorkspaceRevision).where(
                WorkspaceRevision.record_id == uuid.UUID(value["record_id"]),
                WorkspaceRevision.revision == value["revision"],
            )
        )
        record = await db.get(
            WorkspaceRecord, uuid.UUID(value["record_id"]), populate_existing=True
        )
        if (
            record is None
            or record.user_id != owner
            or str(record.project_id) != source["project_id"]
            or record.kind != source["context"]["kind"]
            or revision is None
            or logical_hash(parse_document(revision.payload_json)) != value["record_hash"]
        ):
            raise fail()
        accepted = check_proposal(
            AdviceContext(canonical_bytes(source["context"]).decode()), value["accepted"]
        )
        if source["context"]["kind"] == "plot":
            if str(record.id) != source["target"]["draft_id"]:
                raise fail()
            expected = {
                **source["context"]["draft"],
                "code": accepted["code"],
                "title": accepted["title"],
            }
            if canonical_bytes(expected) != canonical_bytes(parse_document(revision.payload_json)):
                raise fail()
        else:
            from app.services.text_coding import prove_advice

            await prove_advice(db, owner, record, revision, source, accepted, run.id)
    elif saved.event_type == "advice.dispatched":
        if set(value) != {"run_id", "instance", "approval_id"}:
            raise fail()
        uuid.UUID(value["instance"])
        permitted = await security.approval(db, owner, uuid.UUID(value["approval_id"]))
        if permitted.run_id != run.id:
            raise fail()
    elif set(value) != {"run_id"}:
        raise fail()
    return value


async def completion(db, owner, run):
    saved = await event(db, run, "advice.proposed")
    if saved is None:
        raise fail()
    value = await prove_event(db, owner, saved)
    if run.domain_kind != profile.KIND or str(run.domain_run_id) != value["charge_id"]:
        raise fail()
    return logical_hash(value)


async def accept(db, user, project, identity, body, gateway):
    await acquire_write_fence(db, user.id)
    run = await owned(db, user.id, project, identity)
    source = profile.parameters(run)
    if source["context"]["kind"] == "selection":
        raise fail("请在当前选段中采用主题。", 422)
    actual = await proposed(db, user.id, run)
    if (
        run.state != "completed"
        or actual is None
        or body.source_version != source["source_version"]
    ):
        raise fail("建议或来源已变化，请重新生成。")
    proposal = body.proposal.model_dump(mode="json") if body.proposal else actual["proposal"]
    proposal = check_proposal(AdviceContext(canonical_bytes(source["context"]).decode()), proposal)
    previous = await event(db, run, "advice.applied")
    if previous:
        if parse_document(previous.payload_json)["proposal_hash"] != logical_hash(proposal):
            raise fail("这条建议已经采用，请新建建议。")
        return await read(db, user, project, identity, gateway)
    await profile.fresh(db, user.id, run, profile.selection(source))
    if source["context"]["kind"] == "plot":
        payload = PlotDraft.model_validate(source["context"]["draft"])
        payload.code = proposal["code"]
        payload.title = proposal["title"]
        result = await save_record(
            db,
            user,
            SaveRecord(
                project_id=project, expected_revision=source["target"]["revision"], payload=payload
            ),
            uuid.UUID(source["target"]["draft_id"]),
            commit=False,
        )
    else:
        from app.services.text_coding import apply_advice

        result = await apply_advice(db, project, user, source, proposal, run.id)
    await append(
        db,
        user.id,
        run,
        "advice.applied",
        {
            "source_version": source["source_version"],
            "proposal_hash": logical_hash(proposal),
            "accepted": proposal,
            "record_id": result["id"],
            "revision": result["revision"],
            "record_hash": logical_hash(result["payload"]),
        },
    )
    return await read(db, user, project, identity, gateway)
