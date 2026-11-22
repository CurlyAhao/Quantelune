"""Read-only proof of a previously resumed SDK reply; never authorizes a send."""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent, ResearchWorkspace
from app.models.research_harness import ResearchAdvisoryReceipt, ResearchHarnessThread
from app.models.research_planning import ResearchPlannerThread
from app.models.research_security import ResearchPolicy
from app.models.user import User
from app.research import harness_binding, model_request
from app.research.contracts import (
    HASH_VERSION,
    ZERO_HASH,
    canonical_bytes,
    event_hash,
    logical_hash,
    parse_document,
)
from app.research.egress import MAX_RESPONSE_BYTES, credentials, failure
from app.research.harness_serde import SafeSerializer
from app.research.security_contracts import CallSpecification, estimate
from app.services import research_gateway_service as gateways
from app.services import research_security_service as security
from app.services import research_service as ledger


@dataclass(frozen=True)
class HistoricalReplyProof:
    document_json: str
    request_json: str
    recorded_sequence: int
    resumed_sequence: int


async def _events(db, owner, workspace_id):
    # One SQL snapshot, including its original stored head. A second SELECT of
    # events after a separate chain check can race a legitimate appended event.
    snapshot = (
        await db.execute(
            select(
                ResearchEvent, ResearchWorkspace.event_sequence, ResearchWorkspace.event_head_hash
            )
            .select_from(ResearchWorkspace)
            .outerjoin(ResearchEvent, ResearchEvent.workspace_id == ResearchWorkspace.id)
            .where(ResearchWorkspace.id == workspace_id, ResearchWorkspace.owner_id == owner)
            .order_by(ResearchEvent.sequence)
            .limit(10_001)
            .execution_options(populate_existing=True)
        )
    ).all()
    if not snapshot or snapshot[0][1] > 10_000:
        raise failure("REPLY_UNPROVEN")
    rows = [item[0] for item in snapshot if item[0] is not None]
    previous = ZERO_HASH
    for sequence, item in enumerate(rows, 1):
        expected = event_hash(
            str(workspace_id),
            sequence,
            previous,
            item.entity_kind,
            str(item.entity_id),
            item.event_type,
            item.logical_hash,
            str(item.actor_id),
            item.occurred_at,
        )
        if (
            item.sequence != sequence
            or item.previous_hash != previous
            or item.hash_version != HASH_VERSION
            or item.actor_id != owner
            or item.logical_hash != logical_hash(parse_document(item.payload_json))
            or item.chain_hash != expected
        ):
            raise failure("REPLY_UNPROVEN")
        previous = expected
    if len(rows) != snapshot[0][1] or previous != snapshot[0][2]:
        raise failure("REPLY_UNPROVEN")
    return rows


def _only(events, kind, entity_kind, identifier):
    selected = [x for x in events if x.event_type == kind and x.entity_id == identifier]
    if len(selected) != 1 or selected[0].entity_kind != entity_kind:
        raise failure("REPLY_UNPROVEN")
    return selected[0]


def _document(event, expected):
    if parse_document(event.payload_json) != expected:
        raise failure("REPLY_UNPROVEN")


async def prove(
    db,
    owner: uuid.UUID,
    run_id: uuid.UUID,
    attempt_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    reply_hash: str,
    binding_hash: str,
    call_hash: str,
) -> HistoricalReplyProof:
    """Requires the exact original tuple and active history ACL, not current egress rights.

    This helper neither calls fresh Provider/source gates nor transitions a Run.
    The caller must separately prove an Answer/claim before SDK bookkeeping.
    Original live read/resume/authorize methods keep their current semantics.
    """
    try:
        return await _prove(
            db, owner, run_id, attempt_id, charge_id, reply_hash, binding_hash, call_hash
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure("REPLY_UNPROVEN") from None


async def _prove(db, owner, run_id, attempt_id, charge_id, reply_hash, binding_hash, call_hash):
    if any(
        type(x) is not str or re.fullmatch(r"[0-9a-f]{64}", x) is None
        for x in (reply_hash, binding_hash, call_hash)
    ):
        raise failure("REPLY_UNPROVEN")
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    run, scope = await ledger.run(db, run_id, owner)
    attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
    charge = await security.charge(db, owner, charge_id)
    approval = await security.approval(db, owner, charge.approval_id)
    row = await db.get(ResearchAdvisoryReceipt, charge_id, populate_existing=True)
    if (
        attempt is None
        or attempt.run_id != run.id
        or attempt.number > run.attempt_count
        or charge.run_id != run.id
        or approval.run_id != run.id
        or charge.state != "settled"
        or approval.state != "consumed"
        or approval.consumed_charge_id != charge.id
        or row is None
        or row.owner_id != owner
        or row.run_id != run.id
        or row.attempt_id != attempt.id
        or row.input_hash != run.input_hash
        or row.request_hash != charge.request_hash
        or row.document_hash != reply_hash
        or row.resumed_revision != row.approved_revision + 1
        or run.revision < row.resumed_revision
    ):
        raise failure("REPLY_UNPROVEN")
    captured = parse_document(run.input_json)
    original = parse_document(approval.snapshot_json)
    spec = parse_document(charge.specification_json)
    validated = CallSpecification.model_validate(spec)
    amount = estimate(validated)
    automatic_output = validated.max_output_tokens is None
    bound = original["binding"]
    policy = await db.get(ResearchPolicy, approval.policy_id, populate_existing=True)
    if (
        logical_hash(captured) != run.input_hash
        or captured["kind"] != run.kind
        or logical_hash(original) != approval.request_hash
        or approval.request_hash != charge.request_hash
        or original["specification"] != spec
        or validated.model_dump(mode="json") != spec
        or not validated.source_binding_sha256
        or original["expires_at_ms"] != approval.expires_at_ms
        or bound["owner_id"] != str(owner)
        or bound["run_id"] != str(run.id)
        or bound["workspace_id"] != str(scope.id)
        or bound["session_id"] != str(run.session_id)
        or bound["input_hash"] != run.input_hash
        or bound["attempt"] != attempt.number
        or bound["revision"] != row.approved_revision
        or bound["attachments"] != captured["attachments"]
        or policy is None
        or policy.owner_id != owner
        or policy.workspace_id != scope.id
        or bound["policy_id"] != str(policy.id)
        or bound["policy_hash"] != policy.fingerprint
        or logical_hash(parse_document(policy.document_json)) != policy.fingerprint
        or (
            not automatic_output
            and (
                (
                    charge.reserved_cost,
                    charge.input_token_limit,
                    charge.output_token_limit,
                    charge.time_limit_ms,
                )
                != (amount.cost, amount.input_tokens, amount.output_tokens, amount.milliseconds)
            )
        )
        or (
            automatic_output
            and (
                charge.reserved_cost < amount.cost
                or charge.input_token_limit != amount.input_tokens
                or charge.output_token_limit < amount.output_tokens
                or charge.time_limit_ms != amount.milliseconds
            )
        )
        or charge.used_cost is None
        or not 0 <= charge.used_cost <= charge.reserved_cost
        or charge.used_tokens is None
        or not 0 <= charge.used_tokens <= charge.input_token_limit + charge.output_token_limit
        or charge.used_ms is None
        or not 0 <= charge.used_ms <= charge.time_limit_ms
        or not charge.settlement_hash
    ):
        raise failure("REPLY_UNPROVEN")
    request = await model_request.load(db, owner, approval)
    if (
        request is None
        or request["binding_hash"] != binding_hash
        or request["call_hash"] != call_hash
    ):
        raise failure("REPLY_UNPROVEN")
    prefix = (
        "reviewer-model:"
        if run.kind == "reviewer"
        else "reader-model:"
        if run.kind == "reader"
        else "planner-model:"
        if run.kind == "planner"
        else "h02-model:"
    )
    if (
        request["attempt_id"] != str(attempt.id)
        or approval.proposal_key
        != gateways.private_key(prefix + call_hash, "proposal", owner, run.id)
        or charge.call_key != gateways.private_key(prefix + call_hash, "call", owner, run.id)
    ):
        raise failure("REPLY_UNPROVEN")
    thread = await db.get(
        ResearchPlannerThread if run.kind == "planner" else ResearchHarnessThread,
        uuid.UUID(request["binding_id"]),
        populate_existing=True,
    )
    if (
        thread is None
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or thread.profile_hash
        != logical_hash(harness_binding.profile(thread.purpose, run.kind, captured["parameters"]))
        or uuid.UUID(thread.thread_id).hex != thread.thread_id
        or request["selection"]["purpose"] != validated.purpose
        or request["selection"]["scope"] != validated.scope
    ):
        raise failure("REPLY_UNPROVEN")
    document = parse_document(row.document_json)
    if set(document) != {
        "version",
        "owner_id",
        "run_id",
        "attempt_id",
        "input_hash",
        "charge_id",
        "approval_id",
        "request_hash",
        "call_key",
        "binding",
        "settlement_hash",
        "specification_hash",
        "execution_kind",
        "advisory",
    } or (
        len(row.document_json.encode()) > MAX_RESPONSE_BYTES
        or credentials(document)
        or logical_hash(document) != reply_hash
        or document["version"] != 1
        or document["owner_id"] != str(owner)
        or document["run_id"] != str(run.id)
        or document["attempt_id"] != str(attempt.id)
        or document["input_hash"] != run.input_hash
        or document["charge_id"] != str(charge.id)
        or document["approval_id"] != str(approval.id)
        or document["request_hash"] != charge.request_hash
        or document["call_key"] != charge.call_key
        or document["binding"] != bound
        or document["settlement_hash"] != charge.settlement_hash
        or document["specification_hash"] != logical_hash(spec)
        or document["execution_kind"] not in {"provider", "test_recorder"}
        or (
            document["execution_kind"] == "provider"
            and (
                validated.price.synthetic
                or (
                    validated.price.pricing_mode == "reviewed"
                    and validated.price.verified_at_ms is None
                )
            )
        )
    ):
        raise failure("REPLY_UNPROVEN")
    SafeSerializer().check(document)
    events = await _events(db, owner, scope.id)
    policy_event = _only(events, "security.policy", "security_policy", policy.id)
    _document(policy_event, security.snapshot(policy))
    thread_events = [
        x
        for x in events
        if x.event_type == "harness.thread_bound"
        and x.entity_id == run.id
        and parse_document(x.payload_json).get("attempt_id") == str(attempt.id)
        and parse_document(x.payload_json).get("purpose") == thread.purpose
    ]
    if len(thread_events) != 1 or thread_events[0].entity_kind != "run":
        raise failure("REPLY_UNPROVEN")
    _document(thread_events[0], harness_binding.document(thread))
    model_event = _only(events, "harness.model_request", "model_request", approval.id)
    _document(model_event, request)
    approved = [
        x for x in events if x.event_type == "security.approval" and x.entity_id == approval.id
    ]
    if len(approved) != 3 or any(x.entity_kind != "security_approval" for x in approved):
        raise failure("REPLY_UNPROVEN")
    latest = security.snapshot(approval)
    for event, state in zip(approved, ("pending", "approved", "consumed"), strict=True):
        _document(
            event,
            dict(
                latest,
                state=state,
                consumed_charge_id=str(charge.id) if state == "consumed" else None,
            ),
        )
    charges = [x for x in events if x.event_type == "security.charge" and x.entity_id == charge.id]
    if len(charges) not in {3, 4} or any(x.entity_kind != "security_charge" for x in charges):
        raise failure("REPLY_UNPROVEN")
    states = [parse_document(x.payload_json)["state"] for x in charges]
    if states not in (
        ["reserved", "dispatched", "settled"],
        ["reserved", "dispatched", "unknown", "settled"],
    ):
        raise failure("REPLY_UNPROVEN")
    final = security.snapshot(charge)
    for event in charges[:-1]:
        state = parse_document(event.payload_json)["state"]
        settlement = (
            logical_hash(
                dict(outcome="unknown", input_tokens=None, output_tokens=None, elapsed_ms=None)
            )
            if state == "unknown"
            else None
        )
        _document(
            event,
            dict(
                final,
                **(
                    {
                        "reserved_cost": None
                        if validated.price.pricing_mode == "unpriced"
                        else amount.cost,
                        "output_token_limit": amount.output_tokens,
                    }
                    if automatic_output
                    else {}
                ),
                state=state,
                used_cost=None,
                used_tokens=None,
                used_ms=None,
                settlement_hash=settlement,
            ),
        )
    _document(charges[-1], final)
    recorded = _only(events, "security.reply_recorded", "security_reply", charge.id)
    _document(
        recorded,
        dict(charge_id=str(charge.id), attempt_id=str(attempt.id), document_hash=reply_hash),
    )
    resumed = _only(events, "security.reply_resumed", "security_reply", charge.id)
    _document(
        resumed,
        dict(
            charge_id=str(charge.id),
            document_hash=reply_hash,
            approved_revision=row.approved_revision,
            resumed_revision=row.resumed_revision,
        ),
    )
    ordered = [
        thread_events[0],
        approved[0],
        model_event,
        approved[1],
        approved[2],
        *charges,
        recorded,
        resumed,
    ]
    if policy_event.sequence >= approved[0].sequence or any(
        a.sequence >= b.sequence for a, b in zip(ordered, ordered[1:])
    ):
        raise failure("REPLY_UNPROVEN")
    for event in (approved[0], approved[1], approved[2], charges[0], charges[1]):
        when = datetime.fromisoformat(event.occurred_at)
        if when.tzinfo is None or int(when.timestamp() * 1000) >= approval.expires_at_ms:
            raise failure("REPLY_UNPROVEN")
    # Waiting and resuming really happened on this original Attempt. Current
    # Attempt/config/source are deliberately not substituted for those facts.
    changes = [x for x in events if x.event_type == "run.state_changed" and x.entity_id == run.id]
    for revision, before, after, lower, upper in (
        (
            row.approved_revision,
            "running",
            "waiting_approval",
            thread_events[0].sequence,
            approved[0].sequence,
        ),
        (row.resumed_revision, "waiting_approval", "running", recorded.sequence, resumed.sequence),
    ):
        matching = [
            x for x in changes if parse_document(x.payload_json).get("revision") == revision
        ]
        if len(matching) != 1 or matching[0].entity_kind != "run":
            raise failure("REPLY_UNPROVEN")
        value = parse_document(matching[0].payload_json)
        if (
            value["attempt"] != attempt.number
            or value["before"] != before
            or value["state"] != after
            or not lower < matching[0].sequence < upper
        ):
            raise failure("REPLY_UNPROVEN")
    return HistoricalReplyProof(
        canonical_bytes(document).decode(),
        canonical_bytes(request).decode(),
        recorded.sequence,
        resumed.sequence,
    )
