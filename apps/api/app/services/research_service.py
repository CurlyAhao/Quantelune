"""Owner-scoped, transactional formal business ledger.

The caller owns commit/rollback. All mutations acquire the database actor fence
before reads or DML. No harness event or HTTP state assertion completes a Run.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.project import Project
from app.models.research import (
    ResearchArtifact,
    ResearchArtifactEdge,
    ResearchAttachment,
    ResearchAttempt,
    ResearchEvent,
    ResearchMessage,
    ResearchRun,
    ResearchSession,
    ResearchWorkspace,
)
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import (
    HASH_VERSION,
    MAX_ATTEMPTS,
    RETRYABLE,
    ZERO_HASH,
    State,
    canonical_bytes,
    event_hash,
    logical_hash,
    parse_document,
    require_transition,
)
from app.services import storage_service


def error(code: str, status: int = 409) -> StatForgeError:
    return StatForgeError(
        status, f"RESEARCH_{code}", "Research object unavailable or inconsistent."
    )


async def _project(db: AsyncSession, project_id: uuid.UUID, owner: uuid.UUID) -> None:
    with db.no_autoflush:
        row = (
            await db.execute(
                select(Project.owner_id, Project.is_deleted).where(Project.id == project_id)
            )
        ).one_or_none()
    if row is None or row.is_deleted or row.owner_id != owner:
        raise error("ACCESS_DENIED", 404)


async def workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    owner: uuid.UUID,
    *,
    mode: Literal["history", "execution", "live"] = "history",
) -> ResearchWorkspace:
    row = await db.scalar(
        select(ResearchWorkspace)
        .where(
            ResearchWorkspace.id == workspace_id,
            ResearchWorkspace.owner_id == owner,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise error("ACCESS_DENIED", 404)
    if mode == "live" and row.is_deleted:
        raise error("WORKSPACE_INACTIVE")
    if row.project_id is not None:
        await _project(db, row.project_id, owner)
    return row


async def session(
    db: AsyncSession,
    session_id: uuid.UUID,
    owner: uuid.UUID,
    *,
    mode: Literal["history", "execution", "live"] = "history",
) -> tuple[ResearchSession, ResearchWorkspace]:
    row = await db.scalar(
        select(ResearchSession)
        .where(
            ResearchSession.id == session_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise error("ACCESS_DENIED", 404)
    scope = await workspace(db, row.workspace_id, owner, mode=mode)
    if mode == "live" and (row.is_deleted or row.is_archived):
        raise error("SESSION_INACTIVE")
    # Execution callers must still prove their Attempt/token/intent. History
    # access does not grant permission to dispatch, resume or create new work.
    return row, scope


def verify_local_bytes(key: str, byte_size: int, sha256: str) -> None:
    """Caller has already authorized metadata; bounded streaming, no deserialization."""
    if settings.storage_backend != "local" or byte_size <= 0:
        raise error("CONTENT_UNAVAILABLE")
    if settings.max_upload_size_bytes is not None and byte_size > settings.max_upload_size_bytes:
        raise error("CONTENT_LIMIT")
    from app.research.publication_handles import delegated

    if delegated(key, byte_size, sha256):
        return
    try:
        path = storage_service.get_local_object_path(key)
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > byte_size:
                    raise error("CONTENT_CHANGED")
                digest.update(chunk)
        if total != byte_size or digest.hexdigest() != sha256:
            raise error("CONTENT_CHANGED")
    except (OSError, ValueError):
        raise error("CONTENT_UNAVAILABLE") from None


async def _target(
    db: AsyncSession,
    kind: str,
    target_id: uuid.UUID,
    owner: uuid.UUID,
    expected_sha: str | None = None,
    visited: frozenset[uuid.UUID] = frozenset(),
) -> tuple[uuid.UUID, str | None, frozenset[uuid.UUID]]:
    if kind == "project":
        await _project(db, target_id, owner)
        if expected_sha is not None:
            raise error("BINDING_CHANGED")
        return target_id, None, frozenset({target_id})
    if kind == "dataset_version":
        with db.no_autoflush:
            row = (
                await db.execute(
                    select(
                        Dataset.project_id,
                        Dataset.sha256,
                        Dataset.upload_status,
                        Dataset.object_key,
                        Dataset.file_size,
                    ).where(Dataset.id == target_id)
                )
            ).one_or_none()
        if row is None:
            raise error("ACCESS_DENIED", 404)
        await _project(db, row.project_id, owner)
        if row.upload_status != UploadStatus.READY:
            raise error("SOURCE_UNAVAILABLE")
        if expected_sha is not None and row.sha256 != expected_sha:
            raise error("BINDING_CHANGED")
        verify_local_bytes(row.object_key, row.file_size, row.sha256)
        return row.project_id, row.sha256, frozenset({row.project_id})
    if kind == "artifact":
        from app.research.domain_results import verify_artifact

        if target_id in visited or len(visited) >= 64:
            raise error("SOURCE_GRAPH_INVALID")
        visited = visited | {target_id}
        # Authorize project metadata before fetching provenance or object contents.
        project_id = await db.scalar(
            select(ResearchArtifact.project_id).where(ResearchArtifact.id == target_id)
        )
        if project_id is None:
            raise error("ACCESS_DENIED", 404)
        await _project(db, project_id, owner)
        item = await db.get(ResearchArtifact, target_id, populate_existing=True)
        if item is None:
            raise error("ACCESS_DENIED", 404)
        source_run, source_scope = await run(db, item.run_id, owner)
        try:
            provenance = parse_document(item.provenance_json)
            number = provenance.get("attempt_number", 1)
            if type(number) is not int or number <= 0:
                raise ValueError("Invalid attempt identity.")
        except (ValueError, TypeError, AttributeError):
            raise error("SOURCE_UNPROVEN") from None
        source_attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == source_run.id,
                ResearchAttempt.number == number,
            )
        )
        if (
            source_attempt is None
            or source_attempt.domain_kind is None
            or source_attempt.domain_run_id is None
        ):
            raise error("SOURCE_UNPROVEN")
        snapshot = await _snapshot_for_attempt(db, owner, source_run, source_attempt)
        if source_attempt.state != snapshot.state.value or (
            snapshot.state is State.COMPLETED
            and source_attempt.completion_hash != snapshot.logical_hash
        ):
            raise error("SOURCE_UNPROVEN")
        verify_artifact(item, snapshot, owner)
        projects = {project_id}
        if source_scope.project_id is not None:
            projects.add(source_scope.project_id)
        if expected_sha is not None and item.sha256 != expected_sha:
            raise error("BINDING_CHANGED")
        try:
            provenance = parse_document(item.provenance_json)
            captured = parse_document(source_run.input_json)
            if logical_hash(captured) != source_run.input_hash:
                raise ValueError("Source Run input hash changed.")
            references = [
                {**binding, "kind": "dataset_version"} for binding in provenance["datasets"]
            ] + captured["attachments"]
            for binding in references:
                _, _, origins = await _target(
                    db,
                    binding["kind"],
                    uuid.UUID(binding["id"]),
                    owner,
                    binding.get("sha256"),
                    visited,
                )
                projects.update(origins)
        except (ValueError, TypeError, KeyError):
            raise error("SOURCE_GRAPH_INVALID") from None
        verify_local_bytes(item.object_key, item.byte_size, item.sha256)
        return project_id, item.sha256, frozenset(projects)
    raise error("INVALID_ATTACHMENT", 422)


async def _append(
    db: AsyncSession,
    scope: ResearchWorkspace,
    owner: uuid.UUID,
    entity_kind: str,
    entity_id: uuid.UUID,
    event_type: str,
    payload: Any,
) -> ResearchEvent:
    """Internal only. Scope head and new event are flushed in the caller transaction."""
    content = canonical_bytes(payload).decode("utf-8")
    payload_hash = logical_hash(payload)
    occurred = datetime.now(UTC).isoformat(timespec="microseconds")
    sequence = scope.event_sequence + 1
    chain = event_hash(
        str(scope.id),
        sequence,
        scope.event_head_hash,
        entity_kind,
        str(entity_id),
        event_type,
        payload_hash,
        str(owner),
        occurred,
    )
    item = ResearchEvent(
        id=uuid.uuid4(),
        workspace_id=scope.id,
        sequence=sequence,
        entity_kind=entity_kind,
        entity_id=entity_id,
        event_type=event_type,
        actor_id=owner,
        payload_json=content,
        logical_hash=payload_hash,
        previous_hash=scope.event_head_hash,
        chain_hash=chain,
        hash_version=HASH_VERSION,
        occurred_at=occurred,
    )
    db.add(item)
    scope.event_sequence = sequence
    scope.event_head_hash = chain
    await db.flush()
    return item


async def create_workspace(
    db: AsyncSession,
    owner: uuid.UUID,
    title: str,
    project_id: uuid.UUID | None = None,
) -> ResearchWorkspace:
    await acquire_write_fence(db, owner)
    if project_id is not None:
        await _project(db, project_id, owner)
    item = ResearchWorkspace(id=uuid.uuid4(), owner_id=owner, project_id=project_id, title=title)
    db.add(item)
    await db.flush()
    await _append(
        db,
        item,
        owner,
        "workspace",
        item.id,
        "workspace.created",
        {
            "project_id": str(project_id) if project_id else None,
            "title": title,
        },
    )
    return item


async def create_session(
    db: AsyncSession,
    owner: uuid.UUID,
    workspace_id: uuid.UUID,
    title: str,
) -> ResearchSession:
    await acquire_write_fence(db, owner)
    scope = await workspace(db, workspace_id, owner, mode="live")
    item = ResearchSession(id=uuid.uuid4(), workspace_id=workspace_id, title=title)
    db.add(item)
    await db.flush()
    await _append(db, scope, owner, "session", item.id, "session.created", {"title": title})
    return item


async def attach(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    kind: str,
    target_id: uuid.UUID,
    *,
    allow_cross_project: bool = False,
) -> ResearchAttachment:
    await acquire_write_fence(db, owner)
    _, scope = await session(db, session_id, owner, mode="live")
    project_id, sha, source_projects = await _target(db, kind, target_id, owner)
    if (
        scope.project_id is not None
        and source_projects != frozenset({scope.project_id})
        and not allow_cross_project
    ):
        raise error("CROSS_PROJECT_REQUIRES_EXPLICIT_ATTACHMENT")
    item = await db.scalar(
        select(ResearchAttachment).where(
            ResearchAttachment.session_id == session_id,
            ResearchAttachment.target_kind == kind,
            ResearchAttachment.target_id == target_id,
        )
    )
    if item is not None:
        if item.target_sha256 != sha or item.source_project_id != project_id:
            raise error("BINDING_CHANGED")
        if item.active:
            return item
        item.active = True
    else:
        item = ResearchAttachment(
            id=uuid.uuid4(),
            session_id=session_id,
            target_kind=kind,
            target_id=target_id,
            source_project_id=project_id,
            target_sha256=sha,
            active=True,
        )
        db.add(item)
    await db.flush()
    await _append(
        db,
        scope,
        owner,
        "attachment",
        item.id,
        "attachment.added",
        {
            "kind": kind,
            "session_id": str(session_id),
            "target_id": str(target_id),
            "sha256": sha,
            "source_project_id": str(project_id),
            "source_project_ids": sorted(str(origin) for origin in source_projects),
            "cross_project_explicit": allow_cross_project,
        },
    )
    return item


async def detach(db: AsyncSession, owner: uuid.UUID, attachment_id: uuid.UUID) -> None:
    await acquire_write_fence(db, owner)
    item = await db.get(ResearchAttachment, attachment_id, populate_existing=True)
    if item is None:
        raise error("ACCESS_DENIED", 404)
    _, scope = await session(db, item.session_id, owner, mode="live")
    if item.active:
        item.active = False
        await _append(db, scope, owner, "attachment", item.id, "attachment.revoked", {})


async def bindings(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    *,
    mode: Literal["history", "execution", "live"] = "history",
) -> list[dict[str, Any]]:
    await session(db, session_id, owner, mode=mode)
    rows = (
        await db.scalars(
            select(ResearchAttachment)
            .where(
                ResearchAttachment.session_id == session_id,
                ResearchAttachment.active.is_(True),
            )
            .order_by(ResearchAttachment.target_kind, ResearchAttachment.target_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    result = []
    for item in rows:
        if item.target_kind != "project" and item.target_sha256 is None:
            raise error("BINDING_CHANGED")
        project_id, sha, source_projects = await _target(
            db, item.target_kind, item.target_id, owner, item.target_sha256
        )
        if project_id != item.source_project_id:
            raise error("BINDING_CHANGED")
        result.append(
            {
                "kind": item.target_kind,
                "id": str(item.target_id),
                "sha256": sha,
                "source_project_ids": sorted(str(origin) for origin in source_projects),
            }
        )
    return result


async def run(
    db: AsyncSession,
    run_id: uuid.UUID,
    owner: uuid.UUID,
) -> tuple[ResearchRun, ResearchWorkspace]:
    session_id = await db.scalar(select(ResearchRun.session_id).where(ResearchRun.id == run_id))
    if session_id is None:
        raise error("ACCESS_DENIED", 404)
    _, scope = await session(db, session_id, owner)
    item = await db.get(ResearchRun, run_id, populate_existing=True)
    if item is None:
        raise error("ACCESS_DENIED", 404)
    return item, scope


async def start_run(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    kind: str,
    idempotency_key: str,
    parameters: dict[str, Any],
) -> ResearchRun:
    await acquire_write_fence(db, owner)
    _, scope = await session(db, session_id, owner, mode="live")
    document = {
        "kind": kind,
        "parameters": parameters,
        "attachments": await bindings(db, owner, session_id),
    }
    content = canonical_bytes(document).decode("utf-8")
    digest = logical_hash(document)
    item = await db.scalar(
        select(ResearchRun).where(
            ResearchRun.session_id == session_id,
            ResearchRun.idempotency_key == idempotency_key,
        )
    )
    if item is not None:
        if item.input_hash != digest:
            raise error("IDEMPOTENCY_CONFLICT")
        return item
    item = ResearchRun(
        id=uuid.uuid4(),
        session_id=session_id,
        kind=kind,
        idempotency_key=idempotency_key,
        input_hash=digest,
        input_json=content,
        state=State.QUEUED.value,
        attempt_count=1,
    )
    db.add(item)
    await db.flush()
    attempt = ResearchAttempt(id=uuid.uuid4(), run_id=item.id, number=1, state=State.QUEUED.value)
    db.add(attempt)
    await _append(
        db,
        scope,
        owner,
        "run",
        item.id,
        "run.queued",
        {
            "state": State.QUEUED.value,
            "session_id": str(session_id),
            "kind": kind,
            "input_hash": digest,
            "attempt": 1,
        },
    )
    return item


async def transition(
    db: AsyncSession,
    owner: uuid.UUID,
    run_id: uuid.UUID,
    expected_revision: int,
    after: State,
    *,
    retry_key: str | None = None,
) -> ResearchRun:
    """Internal scheduler boundary; completion is exclusively the domain adapter."""
    await acquire_write_fence(db, owner)
    item, scope = await run(db, run_id, owner)
    if item.kind == "draft_advice" and after is State.QUEUED:
        raise error("ADVICE_NEW_REQUEST_REQUIRED")
    if after in {State.QUEUED, State.RUNNING}:
        await session(db, item.session_id, owner, mode="live")
    if retry_key is not None:
        if after is not State.QUEUED or not 1 <= len(retry_key) <= 200:
            raise error("ILLEGAL_TRANSITION")
        prior = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == item.id,
                ResearchAttempt.retry_key == retry_key,
            )
        )
        if prior is not None:
            if prior.base_revision != expected_revision or prior.number != item.attempt_count:
                raise error("IDEMPOTENCY_CONFLICT")
            return item
    if item.revision != expected_revision:
        raise error("STALE_REVISION")
    before = State(item.state)
    if retry_key is not None:
        if before not in RETRYABLE or item.attempt_count >= MAX_ATTEMPTS:
            raise error("RETRY_NOT_ALLOWED")
        captured = parse_document(item.input_json)
        if logical_hash(captured) != item.input_hash or captured["attachments"] != await bindings(
            db, owner, item.session_id
        ):
            raise error("BINDING_CHANGED")
        old = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == item.id,
                ResearchAttempt.number == item.attempt_count,
            )
        )
        if old is None or old.state != before.value:
            raise error("STATE_INCONSISTENT")
        if item.kind == "reader":
            raise error("READER_RETRY_UNPROVEN")
        if item.kind == "reviewer":
            # Admission seals exactly one Attempt. A new review needs a new
            # explicit source-bound admission, never a reused paid/model loop.
            raise error("REVIEWER_RETRY_UNPROVEN")
        if item.kind == "planner":
            from app.research import planner_unresolved

            if await planner_unresolved.event(db, item, old) is not None:
                raise error("PLANNING_NEW_INPUT_REQUIRED")
            from app.models.research_planning import ResearchPlanningLease, ResearchPlanReceipt
            from app.models.research_security import ResearchCharge

            planning = await db.get(ResearchPlanningLease, old.id, populate_existing=True)
            pending = await db.scalar(
                select(ResearchCharge.id).where(
                    ResearchCharge.run_id == item.id,
                    ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
                )
            )
            if (
                (planning is not None and planning.released_at_ms is None)
                or pending is not None
                or await db.scalar(
                    select(ResearchPlanReceipt.id).where(ResearchPlanReceipt.attempt_id == old.id)
                )
                is not None
            ):
                raise error("EXECUTION_RETRY_UNPROVEN")
        from app.models.research_execution import (
            ResearchExecutionDispatch,
            ResearchExecutionIntent,
            ResearchExecutionLease,
            ResearchPublicationReceipt,
        )

        previous_lease = await db.get(ResearchExecutionLease, old.id, populate_existing=True)
        if previous_lease is not None and previous_lease.active_slot == 1:
            raise error("EXECUTION_RETRY_UNPROVEN")
        old_dispatch = await db.get(ResearchExecutionDispatch, old.id, populate_existing=True)
        old_intent = await db.get(ResearchExecutionIntent, old.id, populate_existing=True)
        never_dispatched = False
        if old_dispatch is None and old_intent is not None:
            from app.research.no_dispatch_proof import verified

            never_dispatched = await verified(db, owner, item, old, previous_lease, old_intent)
        if (
            (old_dispatch is not None or old_intent is not None)
            and not never_dispatched
            and await db.get(ResearchPublicationReceipt, old.id, populate_existing=True) is None
        ):
            # Unknown/produced-but-unpublished effects cannot be bypassed by a new Attempt.
            raise error("EXECUTION_RETRY_UNPROVEN")
        item.attempt_count += 1
        item.state = State.QUEUED.value
        item.domain_kind = item.domain_run_id = item.completion_hash = None
        item.revision += 1
        fresh = ResearchAttempt(
            id=uuid.uuid4(),
            run_id=item.id,
            number=item.attempt_count,
            state=State.QUEUED.value,
            retry_key=retry_key,
            base_revision=expected_revision,
        )
        db.add(fresh)
        await _append(
            db,
            scope,
            owner,
            "run",
            item.id,
            "run.retry_queued",
            {
                "before": before.value,
                "state": State.QUEUED.value,
                "revision": item.revision,
                "previous_attempt": old.number,
                "attempt": fresh.number,
                "retry_key": retry_key,
                "base_revision": expected_revision,
            },
        )
        return item
    try:
        # Verify graph legality first, then actual persisted domain/Artifact proof.
        require_transition(before, after, completion_proven=after is State.COMPLETED)
    except ValueError:
        raise error("ILLEGAL_TRANSITION") from None
    if after is State.COMPLETED:
        candidate_attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == item.id, ResearchAttempt.number == item.attempt_count
            )
        )
        if candidate_attempt is None or candidate_attempt.state != before.value:
            raise error("STATE_INCONSISTENT")
        if item.kind == "reviewer":
            from app.research import reviewer_completion

            await reviewer_completion.allow_transition(db, owner, item, candidate_attempt)
        candidate_attempt.domain_kind, candidate_attempt.domain_run_id = (
            item.domain_kind,
            item.domain_run_id,
        )
        await db.flush()
        item.completion_hash = await _prove_completion(db, owner, item)
        if item.kind in {"planner", "reader", "reviewer"}:
            from app.models.research_planning import ResearchPlanningLease
            from app.research.execution_lease import _boundary

            planning = await db.get(
                ResearchPlanningLease, candidate_attempt.id, populate_existing=True
            )
            if planning is None or planning.pause_requested or planning.released_at_ms is not None:
                raise error("PLAN_RECEIPT_UNPROVEN")
            if item.kind in {"reader", "reviewer"}:
                from app.models.research_security import ResearchCharge
                from app.research.execution_identity import WorkerIdentity

                now = await _boundary(db, owner)
                if (
                    planning.active_owner_id != owner
                    or not planning.heartbeat_at_ms <= now < planning.lease_until_ms
                    or not WorkerIdentity(
                        planning.worker_id,
                        planning.producer_host,
                        planning.producer_pid,
                        planning.producer_birth,
                    ).validate_current()
                    or await db.scalar(
                        select(ResearchCharge.id).where(
                            ResearchCharge.run_id == item.id,
                            ResearchCharge.state.in_(["reserved", "dispatched", "unknown"]),
                        )
                    )
                    is not None
                ):
                    raise error(
                        "REVIEWER_COMPLETION_UNPROVEN"
                        if item.kind == "reviewer"
                        else "READER_COMPLETION_UNPROVEN"
                    )
            planning.active_owner_id = None
            planning.released_at_ms = await _boundary(db, owner)
    elif item.kind in {"planner", "reader", "reviewer"} and after in {
        State.FAILED,
        State.CANCELLED,
        State.INTERRUPTED,
    }:
        from app.models.research_planning import ResearchPlanningLease

        current_attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == item.id, ResearchAttempt.number == item.attempt_count
            )
        )
        planning = (
            await db.get(ResearchPlanningLease, current_attempt.id, populate_existing=True)
            if current_attempt
            else None
        )
        if planning is not None and planning.released_at_ms is None:
            raise error("PLANNING_STOP_UNPROVEN")
        if item.kind == "reader" and planning is not None:
            from app.research import reader_lease

            stopped = await reader_lease.prove_stop(db, owner, item, planning)
            if stopped["state"] != after.value:
                raise error("READER_STOP_UNPROVEN")
        if item.kind == "reviewer" and planning is not None:
            from app.research import reviewer_lease

            stopped = await reviewer_lease.prove_stop(db, owner, item, planning)
            if stopped["state"] != after.value:
                raise error("REVIEWER_STOP_UNPROVEN")
    if after == State.RUNNING:
        current = await bindings(db, owner, item.session_id)
        if current != parse_document(item.input_json)["attachments"]:
            raise error("BINDING_CHANGED")
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == item.id,
            ResearchAttempt.number == item.attempt_count,
        )
    )
    if attempt is None or attempt.state != before.value:
        raise error("STATE_INCONSISTENT")
    item.state = attempt.state = after.value
    attempt.domain_kind = item.domain_kind
    attempt.domain_run_id = item.domain_run_id
    attempt.completion_hash = item.completion_hash
    item.revision += 1
    transition_event = await _append(
        db,
        scope,
        owner,
        "run",
        item.id,
        "run.state_changed",
        {
            "before": before.value,
            "state": after.value,
            "revision": item.revision,
            "attempt": item.attempt_count,
            "domain_kind": item.domain_kind,
            "domain_run_id": str(item.domain_run_id) if item.domain_run_id else None,
            "completion_hash": item.completion_hash,
        },
    )
    if item.kind == "reviewer" and after is State.COMPLETED:
        from app.research import reviewer_completion

        await reviewer_completion.record_release(db, owner, item, attempt, transition_event)
    return item


async def _snapshot_for_attempt(db, owner, item, attempt):
    from app.research.domain_results import load_snapshot

    if parse_document(item.input_json)["parameters"].get("operation") != "execute":
        return await load_snapshot(db, owner, attempt.domain_kind, attempt.domain_run_id)
    from types import SimpleNamespace

    from app.research.publication_proof import prove, snapshot

    # Immutable input belongs to Run; domain identity belongs to this actual
    # Attempt, including historical failed attempts after a later retry.
    view = SimpleNamespace(
        id=item.id,
        kind=item.kind,
        input_hash=item.input_hash,
        input_json=item.input_json,
        domain_kind=attempt.domain_kind,
        domain_run_id=attempt.domain_run_id,
    )
    actual = await snapshot(db, owner, view, attempt)
    await prove(db, owner, view, attempt, actual)
    return actual


async def _prove_completion(
    db: AsyncSession,
    owner: uuid.UUID,
    item: ResearchRun,
    *,
    current_context: bool = True,
) -> str:
    if item.kind == "draft_advice":
        from app.services.draft_advice_service import completion

        return await completion(db, owner, item)
    if item.kind == "reviewer":
        from app.research import reviewer_finish

        attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == item.id,
                ResearchAttempt.number == item.attempt_count,
            )
        )
        if attempt is None:
            raise error("REVIEWER_COMPLETION_UNPROVEN")
        return (await reviewer_finish.domain(db, owner, item, attempt)).document_hash
    if item.kind == "reader":
        from app.research import reader_finish

        attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == item.id, ResearchAttempt.number == item.attempt_count
            )
        )
        return (await reader_finish.domain(db, owner, item, attempt)).document_hash
    if item.kind == "planner":
        from app.research import plan_receipt

        attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == item.id, ResearchAttempt.number == item.attempt_count
            )
        )
        if attempt is None:
            raise error("PLAN_RECEIPT_UNPROVEN")
        receipt = await plan_receipt.prove(db, owner, item, attempt)
        if (
            item.domain_kind != "plan"
            or item.domain_run_id != receipt.id
            or attempt.domain_kind != "plan"
            or attempt.domain_run_id != receipt.id
        ):
            raise error("PLAN_RECEIPT_UNPROVEN")
        return receipt.document_hash
    from app.research.domain_results import verify_artifact

    if item.domain_kind is None or item.domain_run_id is None:
        raise error("ILLEGAL_TRANSITION")
    attempt = await db.scalar(
        select(ResearchAttempt).where(
            ResearchAttempt.run_id == item.id, ResearchAttempt.number == item.attempt_count
        )
    )
    if (
        attempt is None
        or attempt.domain_kind != item.domain_kind
        or attempt.domain_run_id != item.domain_run_id
    ):
        raise error("SOURCE_UNPROVEN")
    snapshot = await _snapshot_for_attempt(db, owner, item, attempt)
    document = parse_document(item.input_json)
    expected = {
        "operation": "adopt_saved_result",
        "domain_run_id": str(snapshot.source_id),
        "snapshot_hash": snapshot.logical_hash,
    }
    if (
        snapshot.state is not State.COMPLETED
        or document["kind"] != snapshot.kind
        or (
            document["parameters"].get("operation") != "execute"
            and document["parameters"] != expected
        )
        or logical_hash(document) != item.input_hash
        or (
            current_context
            and document["attachments"] != await bindings(db, owner, item.session_id)
        )
    ):
        raise error("SOURCE_UNPROVEN")
    roots = (
        await db.scalars(
            select(ResearchArtifact).where(
                ResearchArtifact.run_id == item.id,
                ResearchArtifact.kind == "domain_result",
            )
        )
    ).all()
    roots = [
        root
        for root in roots
        if parse_document(root.provenance_json).get("attempt_number", 1) == item.attempt_count
    ]
    if len(roots) != 1:
        raise error("SOURCE_UNPROVEN")
    root = roots[0]
    verify_artifact(root, snapshot, owner)
    children = (
        await db.scalars(
            select(ResearchArtifact)
            .join(
                ResearchArtifactEdge,
                ResearchArtifactEdge.child_id == ResearchArtifact.id,
            )
            .where(
                ResearchArtifactEdge.parent_id == root.id,
                ResearchArtifactEdge.relation == "saved_output",
            )
        )
    ).all()
    if {child.object_key for child in children} != {
        output.object_key for output in snapshot.outputs
    }:
        raise error("SOURCE_UNPROVEN")
    for child in children:
        verify_artifact(child, snapshot, owner)
    return snapshot.logical_hash


async def adopt_saved_result(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    kind: str,
    source_id: uuid.UUID,
    idempotency_key: str,
) -> ResearchRun:
    from app.research.content import publish_json
    from app.research.domain_results import load_snapshot, verify_artifact

    await acquire_write_fence(db, owner)
    _, scope = await session(db, session_id, owner)
    snapshot = await load_snapshot(db, owner, kind, source_id)
    current = await bindings(db, owner, session_id)
    selected = {
        (binding["id"], binding["sha256"])
        for binding in current
        if binding["kind"] == "dataset_version"
    }
    if any((source["id"], source["sha256"]) not in selected for source in snapshot.datasets):
        raise error("SOURCE_NOT_ATTACHED")
    parameters = {
        "operation": "adopt_saved_result",
        "domain_run_id": str(source_id),
        "snapshot_hash": snapshot.logical_hash,
    }
    item = await start_run(db, owner, session_id, kind, idempotency_key, parameters)
    if item.domain_run_id is not None:
        if item.domain_run_id != source_id or item.domain_kind != kind:
            raise error("IDEMPOTENCY_CONFLICT")
        if item.state == State.COMPLETED.value:
            if await _prove_completion(db, owner, item) != item.completion_hash:
                raise error("SOURCE_UNPROVEN")
        return item
    if item.state != State.QUEUED.value:
        raise error("ILLEGAL_TRANSITION")
    await transition(db, owner, item.id, item.revision, State.RUNNING)
    item.domain_kind = kind
    item.domain_run_id = source_id
    provenance = {
        "domain_kind": kind,
        "domain_run_id": str(source_id),
        "snapshot_hash": snapshot.logical_hash,
        "datasets": list(snapshot.datasets),
        "operation": "adopt_saved_result",
        "attempt_number": item.attempt_count,
    }
    provenance_json = canonical_bytes(provenance).decode("utf-8")
    root_id = uuid.uuid4()
    data = canonical_bytes(snapshot.document)
    key, digest = await publish_json(db, owner, root_id, data)
    root = ResearchArtifact(
        id=root_id,
        run_id=item.id,
        project_id=snapshot.project_id,
        kind="domain_result",
        media_type="application/json",
        byte_size=len(data),
        sha256=digest,
        logical_hash=snapshot.logical_hash,
        object_key=key,
        provenance_json=provenance_json,
        created_at=datetime.now(UTC),
    )
    db.add(root)
    await db.flush()
    verify_artifact(root, snapshot, owner)
    for output in snapshot.outputs:
        child = await db.scalar(
            select(ResearchArtifact).where(
                ResearchArtifact.object_key == output.object_key,
            )
        )
        if child is None:
            child = ResearchArtifact(
                id=uuid.uuid4(),
                run_id=item.id,
                project_id=snapshot.project_id,
                kind=output.kind,
                media_type=output.media_type,
                byte_size=output.byte_size,
                sha256=output.sha256,
                logical_hash=logical_hash(output.semantic()),
                object_key=output.object_key,
                provenance_json=provenance_json,
                created_at=datetime.now(UTC),
            )
            db.add(child)
            await db.flush()
        verify_artifact(child, snapshot, owner)
        db.add(
            ResearchArtifactEdge(
                id=uuid.uuid4(),
                parent_id=root.id,
                child_id=child.id,
                relation="saved_output",
            )
        )
    await _append(
        db,
        scope,
        owner,
        "artifact",
        root.id,
        "artifact.registered",
        {
            "run_id": str(item.id),
            "sha256": root.sha256,
            "logical_hash": root.logical_hash,
            "domain_kind": kind,
            "domain_run_id": str(source_id),
            "output_sha256": [output.sha256 for output in snapshot.outputs],
        },
    )
    return await transition(db, owner, item.id, item.revision, snapshot.state)


async def artifact(db: AsyncSession, owner: uuid.UUID, artifact_id: uuid.UUID) -> ResearchArtifact:
    await _target(db, "artifact", artifact_id, owner)
    item = await db.get(ResearchArtifact, artifact_id, populate_existing=True)
    if item is None:
        raise error("ACCESS_DENIED", 404)
    return item


async def artifact_graph(
    db: AsyncSession,
    owner: uuid.UUID,
    artifact_id: uuid.UUID,
    limit: int = 200,
) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise error("GRAPH_LIMIT", 422)
    await artifact(db, owner, artifact_id)
    pending = [artifact_id]
    seen: set[uuid.UUID] = set()
    nodes = []
    edges = {}
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        if len(seen) >= limit:
            raise error("GRAPH_LIMIT")
        node = await artifact(db, owner, identifier)
        seen.add(identifier)
        nodes.append(
            {
                "id": str(node.id),
                "run_id": str(node.run_id),
                "kind": node.kind,
                "sha256": node.sha256,
                "logical_hash": node.logical_hash,
                "bytes": node.byte_size,
                "media_type": node.media_type,
            }
        )
        adjacent = (
            await db.scalars(
                select(ResearchArtifactEdge)
                .where(
                    (ResearchArtifactEdge.parent_id == identifier)
                    | (ResearchArtifactEdge.child_id == identifier)
                )
                .limit(limit + 1)
            )
        ).all()
        if len(adjacent) > limit:
            raise error("GRAPH_LIMIT")
        for edge in adjacent:
            edges[edge.id] = {
                "parent_id": str(edge.parent_id),
                "child_id": str(edge.child_id),
                "relation": edge.relation,
            }
            if len(edges) > limit:
                raise error("GRAPH_LIMIT")
            pending.extend([edge.parent_id, edge.child_id])
    return {
        "nodes": sorted(nodes, key=lambda n: n["id"]),
        "edges": sorted(edges.values(), key=lambda e: (e["parent_id"], e["child_id"])),
    }


async def verify_events(db: AsyncSession, owner: uuid.UUID, workspace_id: uuid.UUID) -> int:
    await workspace(db, workspace_id, owner)
    return await _verify_events_snapshot(db, owner, workspace_id, include_deleted=True)


async def _verify_events_snapshot(
    db: AsyncSession, owner: uuid.UUID, workspace_id: uuid.UUID, *, include_deleted: bool = False
) -> int:
    """Owner-only history; private bookkeeping can verify retained deleted scopes."""
    # One SQL snapshot binds events to the stored head/count. Separate SELECTs
    # can observe a legitimate concurrent commit between header and event reads
    # under SQLite legacy transaction control or PostgreSQL READ COMMITTED.
    snapshot = (
        await db.execute(
            select(
                ResearchEvent, ResearchWorkspace.event_sequence, ResearchWorkspace.event_head_hash
            )
            .select_from(ResearchWorkspace)
            .outerjoin(ResearchEvent, ResearchEvent.workspace_id == ResearchWorkspace.id)
            .where(
                ResearchWorkspace.id == workspace_id,
                ResearchWorkspace.owner_id == owner,
                *([] if include_deleted else [ResearchWorkspace.is_deleted.is_(False)]),
            )
            .order_by(ResearchEvent.sequence)
            .execution_options(populate_existing=True)
        )
    ).all()
    if not snapshot:
        raise error("ACCESS_DENIED", 404)
    head_sequence, head_hash = snapshot[0][1], snapshot[0][2]
    rows = [row[0] for row in snapshot if row[0] is not None]
    previous = ZERO_HASH
    try:
        for sequence, item in enumerate(rows, start=1):
            if (
                item.sequence != sequence
                or item.previous_hash != previous
                or item.hash_version != HASH_VERSION
                or item.actor_id != owner
                or logical_hash(parse_document(item.payload_json)) != item.logical_hash
            ):
                raise ValueError("Event chain invalid.")
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
            if expected != item.chain_hash:
                raise ValueError("Event chain invalid.")
            previous = expected
        if len(rows) != head_sequence or previous != head_hash:
            raise ValueError("Event chain invalid.")
    except (ValueError, TypeError, KeyError):
        raise error("EVENT_CHAIN_INVALID") from None
    return len(rows)


async def add_user_message(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    text: str,
) -> ResearchMessage:
    await acquire_write_fence(db, owner)
    _, scope = await session(db, session_id, owner, mode="live")
    document = {"text": text}
    item = ResearchMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role="user",
        content_json=canonical_bytes(document).decode("utf-8"),
        logical_hash=logical_hash(document),
        created_at=datetime.now(UTC),
    )
    db.add(item)
    await _append(
        db,
        scope,
        owner,
        "message",
        item.id,
        "message.added",
        {
            "role": "user",
            "session_id": str(session_id),
            "run_id": None,
            "logical_hash": item.logical_hash,
        },
    )
    return item
