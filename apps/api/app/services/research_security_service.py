"""Actor-fenced security transactions. Caller commits; these methods never send."""

import time
import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.research import ResearchEvent, ResearchRun, ResearchSession, ResearchWorkspace
from app.models.research_security import (
    ResearchApproval,
    ResearchBudgetAccount,
    ResearchCharge,
    ResearchPolicy,
)
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document
from app.research.security_contracts import (
    APPROVAL_TTL_MS,
    DEFAULT_LIMITS,
    MAX_APPROVAL_TTL_MS,
    SINGLE_CALL_UNITS,
    CallSpecification,
    cost_units,
    estimate,
    policy_document,
)
from app.services import research_service as ledger


def clock_ms() -> int:
    return time.time_ns() // 1_000_000


def reject(code: str, status: int = 409) -> StatForgeError:
    return StatForgeError(
        status, "RESEARCH_SECURITY_" + code, "Security scope or budget unavailable."
    )


def _key(value: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        raise reject("INVALID_KEY", 422)


def unpriced(row: ResearchCharge) -> bool:
    return parse_document(row.specification_json)["price"].get("pricing_mode") == "unpriced"


def snapshot(row: ResearchPolicy | ResearchApproval | ResearchCharge) -> dict[str, Any]:
    if isinstance(row, ResearchPolicy):
        return {
            "id": str(row.id),
            "owner_id": str(row.owner_id),
            "workspace_id": str(row.workspace_id),
            "document": parse_document(row.document_json),
            "fingerprint": row.fingerprint,
        }
    if isinstance(row, ResearchApproval):
        return {
            "id": str(row.id),
            "owner_id": str(row.owner_id),
            "policy_id": str(row.policy_id),
            "run_id": str(row.run_id),
            "proposal_key": row.proposal_key,
            "request_hash": row.request_hash,
            "snapshot": parse_document(row.snapshot_json),
            "expires_at_ms": row.expires_at_ms,
            "state": row.state,
            "consumed_charge_id": str(row.consumed_charge_id) if row.consumed_charge_id else None,
        }
    return {
        "id": str(row.id),
        "owner_id": str(row.owner_id),
        "run_id": str(row.run_id),
        "approval_id": str(row.approval_id),
        "call_key": row.call_key,
        "request_hash": row.request_hash,
        "accounts": parse_document(row.account_ids_json),
        "specification": parse_document(row.specification_json),
        "reserved_cost": None if unpriced(row) else row.reserved_cost,
        "input_token_limit": row.input_token_limit,
        "output_token_limit": row.output_token_limit,
        "time_limit_ms": row.time_limit_ms,
        "used_cost": None if unpriced(row) else row.used_cost,
        "used_tokens": row.used_tokens,
        "used_ms": row.used_ms,
        "state": row.state,
        "settlement_hash": row.settlement_hash,
    }


async def _record(
    db: AsyncSession,
    owner: uuid.UUID,
    scope: ResearchWorkspace,
    row: ResearchPolicy | ResearchApproval | ResearchCharge,
) -> None:
    kind = (
        "policy"
        if isinstance(row, ResearchPolicy)
        else ("approval" if isinstance(row, ResearchApproval) else "charge")
    )
    await ledger._append(
        db, scope, owner, "security_" + kind, row.id, "security." + kind, snapshot(row)
    )


async def _policy(db: AsyncSession, owner: uuid.UUID, scope: ResearchWorkspace) -> ResearchPolicy:
    row = await db.scalar(
        select(ResearchPolicy)
        .where(ResearchPolicy.workspace_id == scope.id)
        .execution_options(populate_existing=True)
    )
    document = policy_document()
    digest = logical_hash(document)
    if row is None:
        row = ResearchPolicy(
            id=uuid.uuid4(),
            owner_id=owner,
            workspace_id=scope.id,
            document_json=canonical_bytes(document).decode(),
            fingerprint=digest,
        )
        db.add(row)
        await _record(db, owner, scope, row)
    if (
        row.owner_id != owner
        or row.fingerprint != digest
        or parse_document(row.document_json) != document
    ):
        raise reject("POLICY_CHANGED")
    return row


async def _context(
    db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID
) -> tuple[ResearchRun, ResearchWorkspace, ResearchPolicy, dict[str, Any]]:
    item, scope = await ledger.run(db, run_id, owner)
    if item.state not in {State.QUEUED.value, State.RUNNING.value, State.WAITING_APPROVAL.value}:
        raise reject("RUN_INACTIVE")
    conversation, _ = await ledger.session(db, item.session_id, owner, mode="live")
    current = await ledger.bindings(db, owner, item.session_id, mode="live")
    original = parse_document(item.input_json)
    if logical_hash(original) != item.input_hash or original["attachments"] != current:
        raise reject("BINDING_CHANGED")
    policy = await _policy(db, owner, scope)
    return (
        item,
        scope,
        policy,
        {
            "owner_id": str(owner),
            "run_id": str(item.id),
            "workspace_id": str(scope.id),
            "session_id": str(item.session_id),
            "input_hash": item.input_hash,
            "revision": item.revision,
            "attempt": item.attempt_count,
            "attachments": current,
            "policy_id": str(policy.id),
            "policy_hash": policy.fingerprint,
            "workspace_epoch": scope.lifecycle_epoch,
            "session_epoch": conversation.lifecycle_epoch,
        },
    )


async def propose(
    db: AsyncSession,
    owner: uuid.UUID,
    run_id: uuid.UUID,
    proposal_key: str,
    specification: CallSpecification,
    ttl_ms: int = APPROVAL_TTL_MS,
) -> ResearchApproval:
    await acquire_write_fence(db, owner)
    _key(proposal_key)
    if type(ttl_ms) is not int or not 1 <= ttl_ms <= MAX_APPROVAL_TTL_MS:
        raise reject("INVALID_EXPIRY", 422)
    spec = CallSpecification.model_validate(specification.model_dump(mode="json"))
    _, scope, policy, bound = await _context(db, owner, run_id)
    document = {"binding": bound, "specification": spec.model_dump(mode="json"), "ttl_ms": ttl_ms}
    row = await db.scalar(
        select(ResearchApproval)
        .where(ResearchApproval.run_id == run_id, ResearchApproval.proposal_key == proposal_key)
        .execution_options(populate_existing=True)
    )
    if row is not None:
        old = parse_document(row.snapshot_json)
        if row.owner_id != owner or any(old.get(key) != value for key, value in document.items()):
            raise reject("PROPOSAL_CONFLICT")
        await _registered(db, owner, scope, row)
        return row
    document["expires_at_ms"] = clock_ms() + ttl_ms
    digest = logical_hash(document)
    row = ResearchApproval(
        id=uuid.uuid4(),
        owner_id=owner,
        policy_id=policy.id,
        run_id=run_id,
        proposal_key=proposal_key,
        request_hash=digest,
        snapshot_json=canonical_bytes(document).decode(),
        expires_at_ms=document["expires_at_ms"],
        state="pending",
    )
    db.add(row)
    await _record(db, owner, scope, row)
    return row


async def approval(db: AsyncSession, owner: uuid.UUID, approval_id: uuid.UUID) -> ResearchApproval:
    row = await db.scalar(
        select(ResearchApproval)
        .where(ResearchApproval.id == approval_id, ResearchApproval.owner_id == owner)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise reject("ACCESS_DENIED", 404)
    return row


async def _validate(
    db: AsyncSession, owner: uuid.UUID, row: ResearchApproval, request_hash: str
) -> tuple[ResearchWorkspace, CallSpecification]:
    if row.owner_id != owner or row.request_hash != request_hash:
        raise reject("APPROVAL_HASH")
    if row.expires_at_ms <= clock_ms():
        raise reject("APPROVAL_EXPIRED")
    _, scope, policy, current = await _context(db, owner, row.run_id)
    await _registered(db, owner, scope, policy)
    await _registered(db, owner, scope, row)
    document = parse_document(row.snapshot_json)
    # Previously issued immutable approvals predate lifecycle epochs. They are
    # valid only on the unchanged zero-epoch objects; never rewrite their hash.
    comparison = dict(current)
    if not {"workspace_epoch", "session_epoch"} & set(document["binding"]):
        if current["workspace_epoch"] == current["session_epoch"] == 0:
            comparison.pop("workspace_epoch")
            comparison.pop("session_epoch")
    if (
        document["expires_at_ms"] != row.expires_at_ms
        or logical_hash(document) != row.request_hash
        or document["binding"] != comparison
        or policy.id != row.policy_id
    ):
        raise reject("APPROVAL_STALE")
    return scope, CallSpecification.model_validate(document["specification"])


async def decide(
    db: AsyncSession,
    owner: uuid.UUID,
    approval_id: uuid.UUID,
    request_hash: str,
    decision: Literal["approve", "deny", "revoke"],
) -> ResearchApproval:
    await acquire_write_fence(db, owner)
    row = await approval(db, owner, approval_id)
    if decision == "revoke":
        # Revocation must remain possible after expiry/source deletion.
        if row.request_hash != request_hash or row.state not in {"pending", "approved"}:
            raise reject("APPROVAL_STATE")
        scope = await _owned_scope(db, owner, row.run_id)
        await _registered(db, owner, scope, row)
        row.state = "revoked"
    else:
        scope, _ = await _validate(db, owner, row, request_hash)
        if decision not in {"approve", "deny"} or row.state != "pending":
            raise reject("APPROVAL_STATE")
        row.state = "approved" if decision == "approve" else "denied"
    await _record(db, owner, scope, row)
    return row


def _scope_keys(
    owner: uuid.UUID, scope: ResearchWorkspace, run_id: uuid.UUID
) -> list[tuple[str, uuid.UUID]]:
    keys = [("owner", owner), ("workspace", scope.id), ("run", run_id)]
    if scope.project_id is not None:
        keys.insert(1, ("project", scope.project_id))
    return keys


async def _accounts(
    db: AsyncSession, owner: uuid.UUID, scope: ResearchWorkspace, run_id: uuid.UUID
) -> list[ResearchBudgetAccount]:
    result = []
    for kind, identifier in _scope_keys(owner, scope, run_id):
        limits = DEFAULT_LIMITS[kind]
        row = await db.scalar(
            select(ResearchBudgetAccount)
            .where(
                ResearchBudgetAccount.owner_id == owner,
                ResearchBudgetAccount.scope_kind == kind,
                ResearchBudgetAccount.scope_id == identifier,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            row = ResearchBudgetAccount(
                id=uuid.uuid4(),
                owner_id=owner,
                scope_kind=kind,
                scope_id=identifier,
                cost_limit=limits.cost,
                attempt_limit=limits.attempts,
                token_limit=limits.tokens,
                time_limit_ms=limits.milliseconds,
                reserved_cost=0,
                consumed_cost=0,
                attempts=0,
                reserved_tokens=0,
                consumed_tokens=0,
                reserved_ms=0,
                consumed_ms=0,
            )
            db.add(row)
        if (row.cost_limit, row.attempt_limit, row.token_limit, row.time_limit_ms) != (
            limits.cost,
            limits.attempts,
            limits.tokens,
            limits.milliseconds,
        ):
            raise reject("ACCOUNT_POLICY_CHANGED")
        result.append(row)
    return result


async def charge(db: AsyncSession, owner: uuid.UUID, charge_id: uuid.UUID) -> ResearchCharge:
    row = await db.scalar(
        select(ResearchCharge)
        .where(ResearchCharge.id == charge_id, ResearchCharge.owner_id == owner)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise reject("ACCESS_DENIED", 404)
    return row


async def reserve(
    db: AsyncSession, owner: uuid.UUID, approval_id: uuid.UUID, request_hash: str, call_key: str
) -> ResearchCharge:
    await acquire_write_fence(db, owner)
    _key(call_key)
    permitted = await approval(db, owner, approval_id)
    existing = await db.scalar(
        select(ResearchCharge)
        .where(ResearchCharge.run_id == permitted.run_id, ResearchCharge.call_key == call_key)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if (existing.owner_id, existing.approval_id, existing.request_hash) != (
            owner,
            approval_id,
            request_hash,
        ):
            raise reject("CALL_CONFLICT")
        return existing  # Record reuse never authorizes redispatch.
    scope, spec = await _validate(db, owner, permitted, request_hash)
    if permitted.state != "approved":
        raise reject("APPROVAL_REQUIRED")
    amount = estimate(spec)
    priced = spec.price.pricing_mode != "unpriced"
    if amount.cost <= 0 or amount.cost > SINGLE_CALL_UNITS:
        raise reject("CALL_COST_LIMIT")
    accounts = await _accounts(db, owner, scope, permitted.run_id)
    await _verify_accounts(db, owner)
    # Inspect all caps before mutating any counter.
    for account in accounts:
        if (
            (
                priced
                and account.reserved_cost + account.consumed_cost + amount.cost > account.cost_limit
            )
            or account.attempts + 1 > account.attempt_limit
            or account.reserved_tokens + account.consumed_tokens + amount.tokens
            > account.token_limit
            or account.reserved_ms + account.consumed_ms + amount.milliseconds
            > account.time_limit_ms
        ):
            raise reject("BUDGET_EXHAUSTED")
    identifier = uuid.uuid4()
    permitted.state, permitted.consumed_charge_id = "consumed", identifier
    for account in accounts:
        account.reserved_cost += amount.cost if priced else 0
        account.attempts += 1
        account.reserved_tokens += amount.tokens
        account.reserved_ms += amount.milliseconds
    row = ResearchCharge(
        id=identifier,
        owner_id=owner,
        run_id=permitted.run_id,
        approval_id=permitted.id,
        call_key=call_key,
        request_hash=request_hash,
        account_ids_json=canonical_bytes([str(item.id) for item in accounts]).decode(),
        specification_json=canonical_bytes(spec.model_dump(mode="json")).decode(),
        reserved_cost=amount.cost,
        input_token_limit=amount.input_tokens,
        output_token_limit=amount.output_tokens,
        time_limit_ms=amount.milliseconds,
        used_cost=None,
        used_tokens=None,
        used_ms=None,
        state="reserved",
    )
    db.add(row)
    await _record(db, owner, scope, permitted)
    await _record(db, owner, scope, row)
    return row


async def dispatch(db: AsyncSession, owner: uuid.UUID, charge_id: uuid.UUID) -> ResearchCharge:
    await acquire_write_fence(db, owner)
    row = await charge(db, owner, charge_id)
    if row.state != "reserved":
        raise reject("DISPATCH_ALREADY_CLAIMED")
    scope = await _charge_scope(db, owner, row)
    await _registered(db, owner, scope, row)
    permitted = await approval(db, owner, row.approval_id)
    if permitted.state != "consumed" or permitted.consumed_charge_id != row.id:
        raise reject("APPROVAL_STATE")
    scope, spec = await _validate(db, owner, permitted, row.request_hash)
    if canonical_bytes(spec.model_dump(mode="json")).decode() != row.specification_json:
        raise reject("CALL_CONFLICT")
    row.state = "dispatched"
    await _record(db, owner, scope, row)
    return row  # Gateway must commit this claim before any transport call.


async def _charge_scope(
    db: AsyncSession, owner: uuid.UUID, row: ResearchCharge
) -> ResearchWorkspace:
    return await _owned_scope(db, owner, row.run_id)


async def settle(
    db: AsyncSession,
    owner: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    outcome: Literal["not_dispatched", "usage", "unknown"],
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    elapsed_ms: int | None = None,
) -> ResearchCharge:
    """Trusted gateway-only settlement. No public caller can report provider usage."""
    await acquire_write_fence(db, owner)
    row = await charge(db, owner, charge_id)
    document = {
        "outcome": outcome,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "elapsed_ms": elapsed_ms,
    }
    digest = logical_hash(document)
    if row.settlement_hash == digest:
        return row
    if row.state in {"settled", "released"}:
        raise reject("SETTLEMENT_CONFLICT")
    scope = await _charge_scope(db, owner, row)
    await _registered(db, owner, scope, row)
    await _verify_accounts(db, owner)
    is_unpriced = unpriced(row)
    if outcome == "unknown":
        if row.state not in {"dispatched", "unknown"} or any(
            value is not None for value in (input_tokens, output_tokens, elapsed_ms)
        ):
            raise reject("INVALID_SETTLEMENT")
        row.state, row.settlement_hash = "unknown", digest
        await _record(db, owner, scope, row)
        return row  # All reservations remain; missing usage is never zero cost.
    if outcome == "not_dispatched":
        if row.state != "reserved" or any(
            value is not None for value in (input_tokens, output_tokens, elapsed_ms)
        ):
            raise reject("INVALID_SETTLEMENT")
        used_cost = used_tokens = used_ms = 0
        state = "released"
    elif outcome == "usage":
        if row.state not in {"dispatched", "unknown"} or any(
            type(value) is not int for value in (input_tokens, output_tokens, elapsed_ms)
        ):
            raise reject("INVALID_USAGE")
        assert input_tokens is not None and output_tokens is not None and elapsed_ms is not None
        spec = CallSpecification.model_validate(parse_document(row.specification_json))
        automatic_output = spec.max_output_tokens is None
        if not (
            0 <= input_tokens <= row.input_token_limit
            and 0 <= output_tokens
            and (automatic_output or output_tokens <= row.output_token_limit)
            and 0 <= elapsed_ms <= row.time_limit_ms
        ):
            raise reject("INVALID_USAGE")
        calculated_cost = cost_units(input_tokens, output_tokens, spec.price)
        # The legacy NOT NULL settlement field stores the known-cost component;
        # unpriced specifications explicitly mean unknown, exposed as null by
        # snapshots/APIs and excluded from monetary budget totals.
        used_cost = calculated_cost if calculated_cost is not None else 0
        if used_cost > row.reserved_cost and not automatic_output:
            raise reject("INVALID_USAGE")
        used_tokens, used_ms, state = input_tokens + output_tokens, elapsed_ms, "settled"
    else:
        raise reject("INVALID_SETTLEMENT")
    identifiers = [uuid.UUID(item) for item in parse_document(row.account_ids_json)]
    if len(identifiers) != len(set(identifiers)):
        raise reject("ACCOUNT_INCONSISTENT")
    accounts = (
        await db.scalars(
            select(ResearchBudgetAccount)
            .where(
                ResearchBudgetAccount.id.in_(identifiers), ResearchBudgetAccount.owner_id == owner
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(accounts) != len(identifiers):
        raise reject("ACCOUNT_INCONSISTENT")
    for account in accounts:
        if (
            account.reserved_cost < (0 if is_unpriced else row.reserved_cost)
            or account.reserved_tokens < row.input_token_limit + row.output_token_limit
            or account.reserved_ms < row.time_limit_ms
        ):
            raise reject("ACCOUNT_INCONSISTENT")
    if outcome == "usage" and spec.max_output_tokens is None:
        # This is accounting for already observed provider usage, not an output
        # parameter sent to the model. Preserve existing independent budgets.
        extra_cost = max(0, used_cost - row.reserved_cost)
        extra_tokens = max(0, output_tokens - row.output_token_limit)
        if used_cost > SINGLE_CALL_UNITS or any(
            account.reserved_cost + account.consumed_cost + extra_cost > account.cost_limit
            or account.reserved_tokens + account.consumed_tokens + extra_tokens
            > account.token_limit
            for account in accounts
        ):
            raise reject("BUDGET_EXHAUSTED")
        for account in accounts:
            account.reserved_cost += extra_cost
            account.reserved_tokens += extra_tokens
        row.reserved_cost += extra_cost
        row.output_token_limit += extra_tokens
    for account in accounts:
        account.reserved_cost -= 0 if is_unpriced else row.reserved_cost
        account.consumed_cost += used_cost
        account.reserved_tokens -= row.input_token_limit + row.output_token_limit
        account.consumed_tokens += used_tokens
        account.reserved_ms -= row.time_limit_ms
        account.consumed_ms += used_ms
    row.state, row.used_cost, row.used_tokens, row.used_ms = state, used_cost, used_tokens, used_ms
    row.settlement_hash = digest
    await _record(db, owner, scope, row)
    return row


async def verify_scope(
    db: AsyncSession, owner: uuid.UUID, scope: ResearchWorkspace, events: list[ResearchEvent]
) -> None:
    """Exact latest business snapshots; orphan or edited security rows fail closed."""
    expected: dict[str, dict[uuid.UUID, dict[str, Any]]] = {
        "security.policy": {},
        "security.approval": {},
        "security.charge": {},
    }
    for item in events:
        if item.event_type not in expected or item.entity_kind != item.event_type.replace(".", "_"):
            raise ValueError("Unsupported security event.")
        expected[item.event_type][item.entity_id] = parse_document(item.payload_json)
    policies = (
        await db.scalars(
            select(ResearchPolicy).where(ResearchPolicy.workspace_id == scope.id),
            execution_options={"populate_existing": True},
        )
    ).all()
    run_ids = select(ResearchRun.id).where(
        ResearchRun.session_id.in_(
            select(ResearchSession.id).where(ResearchSession.workspace_id == scope.id)
        )
    )
    approvals = (
        await db.scalars(
            select(ResearchApproval).where(ResearchApproval.run_id.in_(run_ids)),
            execution_options={"populate_existing": True},
        )
    ).all()
    charges = (
        await db.scalars(
            select(ResearchCharge).where(ResearchCharge.run_id.in_(run_ids)),
            execution_options={"populate_existing": True},
        )
    ).all()
    for kind, rows in (
        ("security.policy", policies),
        ("security.approval", approvals),
        ("security.charge", charges),
    ):
        if {row.id for row in rows} != set(expected[kind]):
            raise ValueError("Security row set mismatch.")
        for row in rows:
            if row.owner_id != owner or snapshot(row) != expected[kind][row.id]:
                raise ValueError("Security business snapshot mismatch.")
    await _verify_accounts(db, owner)


async def _owned_scope(db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID) -> ResearchWorkspace:
    # Bookkeeping survives deleted inputs/project/workspace; only this original
    # owner's immutable charge may be reconciled. This does not authorize egress.
    scope = await db.scalar(
        select(ResearchWorkspace)
        .join(ResearchSession, ResearchSession.workspace_id == ResearchWorkspace.id)
        .join(ResearchRun, ResearchRun.session_id == ResearchSession.id)
        .where(ResearchRun.id == run_id, ResearchWorkspace.owner_id == owner)
        .execution_options(populate_existing=True)
    )
    if scope is None:
        raise reject("ACCESS_DENIED", 404)
    return scope


async def _registered(
    db: AsyncSession,
    owner: uuid.UUID,
    scope: ResearchWorkspace,
    row: ResearchPolicy | ResearchApproval | ResearchCharge,
) -> None:
    if scope.event_sequence > 10000:
        raise reject("VERIFY_LIMIT")
    await ledger._verify_events_snapshot(db, owner, scope.id, include_deleted=True)
    kind = (
        "policy"
        if isinstance(row, ResearchPolicy)
        else ("approval" if isinstance(row, ResearchApproval) else "charge")
    )
    recorded = await db.scalar(
        select(ResearchEvent)
        .where(
            ResearchEvent.workspace_id == scope.id,
            ResearchEvent.entity_id == row.id,
            ResearchEvent.entity_kind == "security_" + kind,
            ResearchEvent.event_type == "security." + kind,
        )
        .order_by(ResearchEvent.sequence.desc())
        .limit(1),
        execution_options={"populate_existing": True},
    )
    if recorded is None or parse_document(recorded.payload_json) != snapshot(row):
        raise reject("BUSINESS_EVENT_INCONSISTENT")


async def _verify_accounts(db: AsyncSession, owner: uuid.UUID) -> None:
    all_charges = (
        await db.scalars(
            select(ResearchCharge).where(ResearchCharge.owner_id == owner),
            execution_options={"populate_existing": True},
        )
    ).all()
    if len(all_charges) > DEFAULT_LIMITS["owner"].attempts:
        raise reject("VERIFY_LIMIT")
    if any(
        unpriced(item) and (item.reserved_cost != 1 or item.used_cost not in (None, 0))
        for item in all_charges
    ):
        raise reject("ACCOUNT_INCONSISTENT")
    accounts = (
        await db.scalars(
            select(ResearchBudgetAccount).where(ResearchBudgetAccount.owner_id == owner),
            execution_options={"populate_existing": True},
        )
    ).all()
    by_id = {item.id: item for item in accounts}
    for row in all_charges:
        scope = await _charge_scope(db, owner, row)
        await _registered(db, owner, scope, row)
        identifiers = [uuid.UUID(value) for value in parse_document(row.account_ids_json)]
        if len(identifiers) != len(set(identifiers)) or any(
            value not in by_id for value in identifiers
        ):
            raise reject("ACCOUNT_INCONSISTENT")
        if {(by_id[value].scope_kind, by_id[value].scope_id) for value in identifiers} != set(
            _scope_keys(owner, scope, row.run_id)
        ):
            raise reject("ACCOUNT_INCONSISTENT")
    for account in accounts:
        amounts = [
            item for item in all_charges if str(account.id) in parse_document(item.account_ids_json)
        ]
        reserved = [item for item in amounts if item.state not in {"settled", "released"}]
        limits = DEFAULT_LIMITS[account.scope_kind]
        actual = (
            account.reserved_cost,
            account.consumed_cost,
            account.attempts,
            account.reserved_tokens,
            account.consumed_tokens,
            account.reserved_ms,
            account.consumed_ms,
        )
        derived = (
            sum(item.reserved_cost for item in reserved if not unpriced(item)),
            sum(item.used_cost or 0 for item in amounts if not unpriced(item)),
            len(amounts),
            sum(item.input_token_limit + item.output_token_limit for item in reserved),
            sum(item.used_tokens or 0 for item in amounts),
            sum(item.time_limit_ms for item in reserved),
            sum(item.used_ms or 0 for item in amounts),
        )
        if actual != derived or (
            account.cost_limit,
            account.attempt_limit,
            account.token_limit,
            account.time_limit_ms,
        ) != (limits.cost, limits.attempts, limits.tokens, limits.milliseconds):
            raise reject("ACCOUNT_INCONSISTENT")
