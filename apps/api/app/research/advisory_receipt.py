"""Only filtered real ProviderReply may accompany actual financial settlement."""

from datetime import datetime

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchAdvisoryReceipt
from app.models.user import User
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document
from app.research.egress import MAX_RESPONSE_BYTES, ProviderReply, credentials, failure
from app.services import research_provider_service as providers
from app.services import research_security_service as security
from app.services import research_service as ledger


async def record(db, owner, approval, charge, reply: ProviderReply, *, execution_kind: str):
    if (
        charge.state != "settled"
        or not charge.settlement_hash
        or execution_kind not in {"provider", "test_recorder"}
    ):
        raise failure("REPLY_UNPROVEN")
    run, scope, _policy, current = await security._context(db, owner, charge.run_id)
    original = parse_document(approval.snapshot_json)
    if original["binding"] != current or approval.request_hash != charge.request_hash:
        raise failure("REPLY_BINDING_CHANGED")
    attempt = await db.scalar(
        select(ResearchAttempt)
        .where(
            ResearchAttempt.run_id == run.id,
            ResearchAttempt.number == run.attempt_count,
        )
        .execution_options(populate_existing=True)
    )
    if attempt is None or attempt.state != run.state:
        raise failure("REPLY_BINDING_CHANGED")
    advisory = {"text": reply.text, "tool_calls": reply.tool_calls}
    if credentials(advisory):
        raise failure("REPLY_SECRET")
    document = {
        "version": 1,
        "owner_id": str(owner),
        "run_id": str(run.id),
        "attempt_id": str(attempt.id),
        "input_hash": run.input_hash,
        "charge_id": str(charge.id),
        "approval_id": str(approval.id),
        "request_hash": approval.request_hash,
        "call_key": charge.call_key,
        "binding": current,
        "settlement_hash": charge.settlement_hash,
        "specification_hash": logical_hash(parse_document(charge.specification_json)),
        "execution_kind": execution_kind,
        "advisory": advisory,
    }
    encoded = canonical_bytes(document)
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise failure("REPLY_LIMIT")
    if await db.get(ResearchAdvisoryReceipt, charge.id) is not None:
        raise failure("REPLY_CONFLICT")
    row = ResearchAdvisoryReceipt(
        charge_id=charge.id,
        owner_id=owner,
        run_id=run.id,
        attempt_id=attempt.id,
        input_hash=run.input_hash,
        request_hash=approval.request_hash,
        document_hash=logical_hash(document),
        document_json=encoded.decode(),
        approved_revision=run.revision,
    )
    db.add(row)
    await ledger._append(
        db,
        scope,
        owner,
        "security_reply",
        charge.id,
        "security.reply_recorded",
        {
            "charge_id": str(charge.id),
            "attempt_id": str(attempt.id),
            "document_hash": row.document_hash,
        },
    )
    await db.flush()
    return row


async def read(db, owner, approval, charge, *, historical=False):
    row = await db.get(ResearchAdvisoryReceipt, charge.id, populate_existing=True)
    if row is None:
        return None  # Financial reuse never manufactures or re-dispatches a reply.
    approval = await security.approval(db, owner, approval.id)
    charge = await security.charge(db, owner, charge.id)
    run, scope = await ledger.run(db, charge.run_id, owner)
    if historical and run.kind != "draft_advice":
        raise failure("REPLY_BINDING_CHANGED")
    actor = await db.get(User, owner, populate_existing=True)
    original = parse_document(approval.snapshot_json)
    policy = await security._policy(db, owner, scope)
    attachments = await ledger.bindings(db, owner, run.session_id)
    captured = parse_document(run.input_json)
    if historical:
        attachments = captured["attachments"]
    current = {
        "owner_id": str(owner),
        "run_id": str(run.id),
        "workspace_id": str(scope.id),
        "session_id": str(run.session_id),
        "input_hash": run.input_hash,
        "revision": row.approved_revision,
        "attempt": run.attempt_count,
        "attachments": attachments,
        "policy_id": str(policy.id),
        "policy_hash": policy.fingerprint,
    }
    # A committed reply is historical evidence, not permission to issue a new
    # send. Preserve the approved epochs for readback; resume is live-gated.
    for key in ("workspace_epoch", "session_epoch"):
        if key in original["binding"]:
            current[key] = original["binding"][key]
    await security._registered(db, owner, scope, policy)
    await security._registered(db, owner, scope, approval)
    await security._registered(db, owner, scope, charge)
    spec = parse_document(charge.specification_json)
    current_provider = await providers.public(db, owner)
    if (
        actor is None
        or not actor.is_active
        or logical_hash(captured) != run.input_hash
        or captured["attachments"] != attachments
        or current != original["binding"]
        or original["expires_at_ms"] != approval.expires_at_ms
        or logical_hash(original) != approval.request_hash
        or (not historical and current_provider["configuration_hash"] != spec["config_sha256"])
    ):
        raise failure("REPLY_BINDING_CHANGED")
    if run.revision != row.approved_revision:
        if row.resumed_revision is None or run.revision < row.resumed_revision:
            raise failure("REPLY_BINDING_CHANGED")
        resumed = await db.scalar(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == charge.id,
                ResearchEvent.event_type == "security.reply_resumed",
            )
            .order_by(ResearchEvent.sequence.desc())
            .limit(1)
        )
        resume_proof = {
            "charge_id": str(charge.id),
            "document_hash": row.document_hash,
            "approved_revision": row.approved_revision,
            "resumed_revision": row.resumed_revision,
        }
        if (
            resumed is None
            or parse_document(resumed.payload_json) != resume_proof
            or resumed.logical_hash != logical_hash(resume_proof)
        ):
            raise failure("REPLY_UNPROVEN")
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == charge.id,
                ResearchEvent.event_type == "security.charge",
            )
            .order_by(ResearchEvent.sequence)
        )
    ).all()
    dispatched = next(
        (
            event
            for event in events
            if parse_document(event.payload_json).get("state") == "dispatched"
        ),
        None,
    )
    if (
        dispatched is None
        or int(datetime.fromisoformat(dispatched.occurred_at).timestamp() * 1000)
        >= approval.expires_at_ms
    ):
        raise failure("REPLY_UNPROVEN")
    sent_document = parse_document(dispatched.payload_json)
    if (
        dispatched.logical_hash != logical_hash(sent_document)
        or sent_document.get("specification") != spec
        or sent_document.get("owner_id") != str(owner)
        or sent_document.get("approval_id") != str(approval.id)
        or sent_document.get("request_hash") != charge.request_hash
        or sent_document.get("call_key") != charge.call_key
    ):
        raise failure("REPLY_UNPROVEN")
    document = parse_document(row.document_json)
    attempt = await db.get(ResearchAttempt, row.attempt_id, populate_existing=True)
    if (
        len(row.document_json.encode()) > MAX_RESPONSE_BYTES
        or credentials(document)
        or logical_hash(document) != row.document_hash
        or row.owner_id != owner
        or row.run_id != run.id
        or row.input_hash != run.input_hash
        or row.request_hash != charge.request_hash
        or attempt is None
        or attempt.run_id != run.id
        or attempt.number != run.attempt_count
        or charge.state != "settled"
        or charge.approval_id != approval.id
        or document["owner_id"] != str(owner)
        or document["run_id"] != str(run.id)
        or document["attempt_id"] != str(attempt.id)
        or document["input_hash"] != run.input_hash
        or document["charge_id"] != str(charge.id)
        or document["approval_id"] != str(approval.id)
        or document["request_hash"] != charge.request_hash
        or document["call_key"] != charge.call_key
        or document["binding"] != parse_document(approval.snapshot_json)["binding"]
        or document["settlement_hash"] != charge.settlement_hash
        or document["specification_hash"] != logical_hash(parse_document(charge.specification_json))
        or document["execution_kind"] not in {"provider", "test_recorder"}
    ):
        raise failure("REPLY_BINDING_CHANGED")
    event = await db.scalar(
        select(ResearchEvent)
        .where(
            ResearchEvent.workspace_id == scope.id,
            ResearchEvent.entity_id == charge.id,
            ResearchEvent.entity_kind == "security_reply",
            ResearchEvent.event_type == "security.reply_recorded",
        )
        .order_by(ResearchEvent.sequence.desc())
        .limit(1)
    )
    expected = {
        "charge_id": str(charge.id),
        "attempt_id": str(attempt.id),
        "document_hash": row.document_hash,
    }
    if (
        event is None
        or parse_document(event.payload_json) != expected
        or event.logical_hash != logical_hash(expected)
    ):
        raise failure("REPLY_UNPROVEN")
    return {
        "advisory": document["advisory"],
        "execution_kind": document["execution_kind"],
        "reply_receipt_hash": row.document_hash,
        "run_completed": False,
    }


async def resume(db, owner, approval, charge):
    await acquire_write_fence(db, owner)
    current_run, _ = await ledger.run(db, charge.run_id, owner)
    conversation, scope = await ledger.session(db, current_run.session_id, owner, mode="live")
    binding = parse_document(approval.snapshot_json)["binding"]
    if (
        binding.get("workspace_epoch", 0) != scope.lifecycle_epoch
        or binding.get("session_epoch", 0) != conversation.lifecycle_epoch
    ):
        raise failure("REPLY_BINDING_CHANGED")
    actual = await read(db, owner, approval, charge, historical=current_run.kind == "draft_advice")
    if actual is None:
        raise failure("REPLY_UNRECOVERABLE")
    row = await db.get(ResearchAdvisoryReceipt, charge.id, populate_existing=True)
    run, scope = await ledger.run(db, charge.run_id, owner)
    if run.kind in {"planner", "reader", "reviewer"}:
        from app.models.research_planning import ResearchPlanningLease
        from app.research import planning_control, reader_control, reviewer_control

        control = (
            reviewer_control
            if run.kind == "reviewer"
            else reader_control
            if run.kind == "reader"
            else planning_control
        )
        attempt = await db.get(ResearchAttempt, row.attempt_id, populate_existing=True)
        lease = await db.get(ResearchPlanningLease, row.attempt_id, populate_existing=True)
        if attempt is None or await control.cancelled(db, owner, run, attempt):
            raise failure("MODEL_CANCELLED")
        if lease is None or lease.pause_requested:
            raise failure("MODEL_PAUSED")
    if row.resumed_revision is not None:
        return actual
    if run.state != State.WAITING_APPROVAL.value or run.revision != row.approved_revision:
        raise failure("REPLY_BINDING_CHANGED")
    await ledger.transition(db, owner, run.id, run.revision, State.RUNNING)
    row.resumed_revision = run.revision
    await ledger._append(
        db,
        scope,
        owner,
        "security_reply",
        charge.id,
        "security.reply_resumed",
        {
            "charge_id": str(charge.id),
            "document_hash": row.document_hash,
            "approved_revision": row.approved_revision,
            "resumed_revision": row.resumed_revision,
        },
    )
    await db.flush()
    return actual
