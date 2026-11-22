"""Verify appended business events against state tables; never repair or execute."""

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import (
    ResearchArtifact,
    ResearchArtifactEdge,
    ResearchAttachment,
    ResearchAttempt,
    ResearchEvent,
    ResearchMessage,
    ResearchRun,
    ResearchSession,
)
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import (
    RETRYABLE,
    State,
    canonical_bytes,
    logical_hash,
    parse_document,
    require_transition,
)


@dataclass(frozen=True, slots=True)
class _PlannerInput:
    """Input data only, protected by the caller transaction's Run row lock."""

    kind: str
    session_id: uuid.UUID
    input_hash: str
    input_json: str


async def verify_business(
    db: AsyncSession,
    owner: uuid.UUID,
    workspace_id: uuid.UUID,
) -> dict[str, int | bool]:
    from app.research import harness_replay, reviewer_replay
    from app.services import research_service as ledger

    await acquire_write_fence(db, owner)
    input_transaction = db.sync_session.get_transaction()
    input_started_nested = db.sync_session.in_nested_transaction()
    planner_inputs: dict[uuid.UUID, _PlannerInput] = {}

    def input_lock_is_current() -> bool:
        return (
            input_transaction is not None
            and input_transaction.is_active
            and db.sync_session.get_transaction() is input_transaction
            and not input_started_nested
            and not db.sync_session.in_nested_transaction()
        )

    scope = await ledger.workspace(db, workspace_id, owner)
    if scope.event_sequence > 10000:
        raise ledger.error("VERIFY_LIMIT")
    count = await ledger.verify_events(db, owner, workspace_id)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == workspace_id,
            )
            .order_by(ResearchEvent.sequence),
            execution_options={"populate_existing": True},
        )
    ).all()
    security_events: list[ResearchEvent] = []
    harness_events: list[ResearchEvent] = []
    sessions: dict[uuid.UUID, str] = {}
    attachments: dict[uuid.UUID, dict[str, Any]] = {}
    messages: dict[uuid.UUID, dict[str, Any]] = {}
    runs: dict[uuid.UUID, dict[str, Any]] = {}
    roots: dict[uuid.UUID, dict[str, Any]] = {}
    recovered_fences: dict[uuid.UUID, int] = {}
    workspace_created = False
    workspace_lifecycle: dict[str, Any] = {
        "project_id": None,
        "revision": 0,
        "lifecycle_epoch": 0,
        "is_deleted": False,
    }
    session_lifecycle: dict[uuid.UUID, dict[str, Any]] = {}
    attachment_revisions: dict[uuid.UUID, int] = {}
    drafts: dict[uuid.UUID, dict] = {}
    saved_plan_versions: dict[str, dict] = {}
    protocol_versions: dict[str, dict] = {}
    protocol_latest: dict[uuid.UUID, dict] = {}
    protocol_current: dict[uuid.UUID, dict] = {}
    protocol_activation_ids: set[uuid.UUID] = set()
    protocol_launches: dict[tuple[uuid.UUID, str], int] = {}
    protocol_keys: dict[tuple[uuid.UUID, str], set[str]] = {}
    plan_acceptances: set[uuid.UUID] = set()
    plan_execution_controls: dict[uuid.UUID, dict] = {}
    plan_children: dict[uuid.UUID, uuid.UUID] = {}
    plan_results: dict[uuid.UUID, dict] = {}
    planner_receipts: set[uuid.UUID] = set()
    planner_stops: dict[uuid.UUID, dict] = {}
    planner_unresolved: dict[uuid.UUID, ResearchEvent] = {}
    planner_unresolved_cancels: dict[uuid.UUID, dict] = {}
    planner_recoveries: dict[uuid.UUID, dict] = {}
    reader_controls: dict[uuid.UUID, list[dict]] = {}
    reader_claims: dict[uuid.UUID, dict] = {}
    reviewer_state = reviewer_replay.ReviewerReplay()
    reader_claim_events: dict[uuid.UUID, ResearchEvent] = {}
    reader_epochs: dict[uuid.UUID, dict] = {}
    reader_recoveries: dict[uuid.UUID, ResearchEvent] = {}
    reader_sdk_done: set[uuid.UUID] = set()
    reader_control_acks: set[tuple[uuid.UUID, int]] = set()
    reader_stops: dict[uuid.UUID, dict] = {}
    reader_deaths: dict[uuid.UUID, ResearchEvent] = {}
    reader_answers: set[uuid.UUID] = set()
    planner_controls: dict[uuid.UUID, list[dict]] = {}
    planner_control_acks: set[tuple[uuid.UUID, int]] = set()
    planner_sdk_finishes: set[uuid.UUID] = set()
    planner_admissions: set[uuid.UUID] = set()
    draft_keys: dict[uuid.UUID, set[str]] = {}

    def require_live_session(identifier: uuid.UUID) -> None:
        lifecycle = session_lifecycle[identifier]
        if (
            not workspace_created
            or workspace_lifecycle["is_deleted"]
            or lifecycle["is_archived"]
            or lifecycle["is_deleted"]
        ):
            raise ValueError("New live event after lifecycle closure.")

    async def require_planner_protocol(run_id: uuid.UUID) -> None:
        from app.research import planner_v2

        locked = input_lock_is_current()
        actual: _PlannerInput | ResearchRun | None = planner_inputs.get(run_id) if locked else None
        if actual is None:
            if locked:
                actual = await db.get(
                    ResearchRun, run_id, populate_existing=True, with_for_update=True
                )
            else:
                actual = await db.get(ResearchRun, run_id, populate_existing=True)
            # Recheck after the database await. Never retain an ORM object or
            # reuse a view after its root transaction or savepoint scope changes.
            if actual is not None and locked and input_lock_is_current():
                actual = _PlannerInput(
                    actual.kind, actual.session_id, actual.input_hash, actual.input_json
                )
                planner_inputs[run_id] = actual
        current_run = runs[run_id]
        session_id = uuid.UUID(current_run["session_id"])
        if actual is None or actual.input_hash != current_run["input_hash"]:
            raise ValueError("Planner Protocol original input changed.")
        captured_context = planner_v2.as_of(
            actual, protocol_versions, protocol_current.get(session_id)
        )
        if captured_context is not None and (
            captured_context.attachment_revision != attachment_revisions.get(session_id, 0)
            or captured_context.workspace_epoch != workspace_lifecycle["lifecycle_epoch"]
            or captured_context.session_epoch != session_lifecycle[session_id]["lifecycle_epoch"]
        ):
            raise ValueError("Planner Protocol temporal source changed.")

    try:
        for event in events:
            payload = parse_document(event.payload_json)
            identifier = event.entity_id
            kind = event.event_type
            if kind == "workspace.created":
                if workspace_created or identifier != scope.id or payload["title"] != scope.title:
                    raise ValueError("Workspace mismatch.")
                workspace_lifecycle["project_id"] = payload["project_id"]
                workspace_created = True
            elif kind in {"workspace.saved", "workspace.deleted"}:
                fields = {"revision", "lifecycle_epoch"}
                if kind == "workspace.saved":
                    fields.add("project_id")
                if (
                    not workspace_created
                    or event.entity_kind != "workspace"
                    or identifier != scope.id
                    or set(payload) != fields
                    or workspace_lifecycle["is_deleted"]
                    or type(payload["revision"]) is not int
                    or type(payload["lifecycle_epoch"]) is not int
                    or payload["revision"] != workspace_lifecycle["revision"] + 1
                    or payload["lifecycle_epoch"] != workspace_lifecycle["lifecycle_epoch"] + 1
                ):
                    raise ValueError("Workspace lifecycle mismatch.")
                if kind == "workspace.saved":
                    if workspace_lifecycle["project_id"] is not None:
                        raise ValueError("Saved workspace cannot be rebound.")
                    uuid.UUID(payload["project_id"])
                    workspace_lifecycle["project_id"] = payload["project_id"]
                else:
                    workspace_lifecycle["is_deleted"] = True
                for field in ("revision", "lifecycle_epoch"):
                    workspace_lifecycle[field] = payload[field]
            elif kind == "session.created":
                if (
                    identifier in sessions
                    or not workspace_created
                    or workspace_lifecycle["is_deleted"]
                ):
                    raise ValueError("Duplicate session.")
                sessions[identifier] = payload["title"]
                session_lifecycle[identifier] = {
                    "revision": 0,
                    "lifecycle_epoch": 0,
                    "is_archived": False,
                    "is_deleted": False,
                }
            elif kind in {"session.archive", "session.restore", "session.delete"}:
                current = session_lifecycle[identifier]
                if (
                    event.entity_kind != "session"
                    or set(payload) != {"revision", "lifecycle_epoch", "is_archived", "is_deleted"}
                    or current["is_deleted"]
                    or type(payload["revision"]) is not int
                    or type(payload["lifecycle_epoch"]) is not int
                    or payload["revision"] != current["revision"] + 1
                    or payload["lifecycle_epoch"] != current["lifecycle_epoch"] + 1
                    or payload["is_archived"] is not (kind != "session.restore")
                    or payload["is_deleted"] is not (kind == "session.delete")
                    or (kind == "session.restore" and not current["is_archived"])
                    or (kind == "session.restore" and workspace_lifecycle["is_deleted"])
                    or (kind == "session.archive" and current["is_archived"])
                ):
                    raise ValueError("Session lifecycle mismatch.")
                current.update(payload)
            elif kind == "attachment.added":
                require_live_session(uuid.UUID(payload["session_id"]))
                attachments[identifier] = {**payload, "active": True}
                attachment_revisions[uuid.UUID(payload["session_id"])] = event.sequence
            elif kind == "attachment.revoked":
                require_live_session(uuid.UUID(attachments[identifier]["session_id"]))
                attachments[identifier]["active"] = False
                attachment_revisions[uuid.UUID(attachments[identifier]["session_id"])] = (
                    event.sequence
                )
            elif kind in {"protocol.version_saved", "protocol.confirmed", "protocol.revoked"}:
                from app.research import protocol_proof

                session_id = uuid.UUID(payload["session_id"])
                require_live_session(session_id)
                selected = [
                    {
                        "kind": item["kind"],
                        "id": item["target_id"],
                        "sha256": item["sha256"],
                        "source_project_ids": item["source_project_ids"],
                    }
                    for item in attachments.values()
                    if item["active"] and item["session_id"] == str(session_id)
                ]
                selected.sort(key=lambda item: (item["kind"], item["id"]))
                if kind == protocol_proof.SAVED:
                    value = await protocol_proof.verify_version(
                        db, event, owner, selected, protocol_latest.get(session_id)
                    )
                    if value["version_id"] in protocol_versions:
                        raise ValueError("Duplicate protocol version event.")
                    protocol_versions[value["version_id"]] = value
                    protocol_latest[session_id] = value
                    protocol_context: dict | None = value["context"]
                else:
                    value = await protocol_proof.verify_activation(
                        db,
                        event,
                        owner,
                        protocol_versions,
                        protocol_latest.get(session_id),
                        protocol_current.get(session_id),
                    )
                    if identifier in protocol_activation_ids:
                        raise ValueError("Duplicate protocol activation event.")
                    protocol_activation_ids.add(identifier)
                    protocol_current[session_id] = value
                    protocol_saved_version = protocol_versions[value["version_id"]]
                    protocol_context = (
                        protocol_saved_version["context"]
                        if kind == protocol_proof.CONFIRMED
                        else None
                    )
                    if (
                        kind == protocol_proof.CONFIRMED
                        and protocol_saved_version["attachments"] != selected
                    ):
                        raise ValueError("Protocol confirmation attachments changed.")
                keys = protocol_keys.setdefault((session_id, event.entity_kind), set())
                if value["key_hash"] in keys:
                    raise ValueError("Repeated protocol idempotency identity.")
                keys.add(value["key_hash"])
                if protocol_context is not None:
                    current_lifecycle = session_lifecycle[session_id]
                    if (
                        protocol_context["workspace_revision"] != workspace_lifecycle["revision"]
                        or protocol_context["workspace_epoch"]
                        != workspace_lifecycle["lifecycle_epoch"]
                        or protocol_context["session_revision"] != current_lifecycle["revision"]
                        or protocol_context["session_epoch"] != current_lifecycle["lifecycle_epoch"]
                        or protocol_context["attachment_revision"]
                        != attachment_revisions.get(session_id, 0)
                    ):
                        raise ValueError("Protocol lifecycle/source generation changed.")
            elif kind == "protocol.launch_authorized":
                from app.research import protocol_launch

                launch_context = await protocol_launch.verify_event(
                    db,
                    owner,
                    event,
                    saved_plan_versions,
                    plan_acceptances,
                    plan_execution_controls,
                    plan_children,
                    runs,
                    protocol_versions,
                    protocol_current,
                    protocol_launches,
                )
                require_live_session(launch_context.session_id)
                lifecycle = session_lifecycle[launch_context.session_id]
                if (
                    launch_context.workspace_revision != workspace_lifecycle["revision"]
                    or launch_context.workspace_epoch != workspace_lifecycle["lifecycle_epoch"]
                    or launch_context.session_revision != lifecycle["revision"]
                    or launch_context.session_epoch != lifecycle["lifecycle_epoch"]
                    or launch_context.attachment_revision
                    != attachment_revisions.get(launch_context.session_id, 0)
                ):
                    raise ValueError("Protocol launch source/lifecycle generation changed.")
            elif kind == "plan.draft_saved":
                from app.services import research_plan_service

                session_id = uuid.UUID(payload["session_id"])
                require_live_session(session_id)
                selected = [
                    {
                        "kind": item["kind"],
                        "id": item["target_id"],
                        "sha256": item["sha256"],
                        "source_project_ids": item["source_project_ids"],
                    }
                    for item in attachments.values()
                    if item["active"] and item["session_id"] == str(session_id)
                ]
                selected.sort(key=lambda item: (item["kind"], item["id"]))
                value = await research_plan_service.verify_event(
                    db, event, owner, selected, drafts.get(identifier)
                )
                from app.research import plan_versioning

                plan_versioning.as_of(
                    plan_versioning.parse(value["plan"]),
                    value.get("protocol"),
                    protocol_versions,
                    protocol_current.get(session_id),
                )
                current_lifecycle = session_lifecycle[session_id]
                if (
                    value["context"]["workspace_revision"] != workspace_lifecycle["revision"]
                    or value["context"]["workspace_epoch"] != workspace_lifecycle["lifecycle_epoch"]
                    or value["context"]["session_revision"] != current_lifecycle["revision"]
                    or value["context"]["session_epoch"] != current_lifecycle["lifecycle_epoch"]
                    or value["context"]["attachment_revision"]
                    != attachment_revisions.get(session_id, 0)
                    or value["idempotency_key"] in draft_keys.setdefault(identifier, set())
                ):
                    raise ValueError("Plan draft lifecycle/version identity changed.")
                draft_keys[identifier].add(value["idempotency_key"])
                drafts[identifier] = value
                saved_plan_versions[value["version_id"]] = value
            elif kind == "plan.accepted":
                from app.research import plan_acceptance

                value, context = await plan_acceptance.verify_event(
                    db, event, owner, saved_plan_versions
                )
                session_id = context.session_id
                require_live_session(session_id)
                saved_acceptance_plan = saved_plan_versions[value["request"]["version_id"]]
                from app.research import plan_versioning

                plan_versioning.as_of(
                    plan_versioning.parse(saved_acceptance_plan["plan"]),
                    saved_acceptance_plan.get("protocol"),
                    protocol_versions,
                    protocol_current.get(session_id),
                )
                lifecycle = session_lifecycle[session_id]
                if (
                    identifier in plan_acceptances
                    or context.workspace_revision != workspace_lifecycle["revision"]
                    or context.workspace_epoch != workspace_lifecycle["lifecycle_epoch"]
                    or context.session_revision != lifecycle["revision"]
                    or context.session_epoch != lifecycle["lifecycle_epoch"]
                    or context.attachment_revision != attachment_revisions.get(session_id, 0)
                ):
                    raise ValueError("Plan acceptance lifecycle/attachment source changed.")
                plan_acceptances.add(identifier)
                plan_execution_controls[identifier] = dict(revision=0, cancel_requested=False)
            elif kind in {"plan.step_bound", "plan.step_proven", "plan.execution_control"}:
                from app.research import plan_execution

                await plan_execution.verify_event(
                    db,
                    owner,
                    event,
                    saved_plan_versions,
                    plan_acceptances,
                    plan_execution_controls,
                    plan_children,
                    plan_results,
                    runs,
                )
                if kind == "plan.step_bound" or (
                    kind == "plan.step_proven"
                    and payload["citation"].get("kind") != "native_result"
                ):
                    from app.models.research_planning import ResearchPlanAcceptance

                    accepted_id = uuid.UUID(payload["acceptance_id"])
                    acceptance_row = await db.get(
                        ResearchPlanAcceptance, accepted_id, populate_existing=True
                    )
                    if acceptance_row is None:
                        raise ValueError("Plan acceptance unavailable at execution event.")
                    require_live_session(acceptance_row.session_id)
                    saved_execution_plan = saved_plan_versions[str(acceptance_row.plan_version_id)]
                    from app.research import plan_versioning

                    plan_versioning.as_of(
                        plan_versioning.parse(saved_execution_plan["plan"]),
                        saved_execution_plan.get("protocol"),
                        protocol_versions,
                        protocol_current.get(acceptance_row.session_id),
                    )
            elif kind == "message.added":
                require_live_session(uuid.UUID(payload["session_id"]))
                if identifier in messages:
                    raise ValueError("Duplicate message.")
                messages[identifier] = payload
            elif kind == "run.queued":
                require_live_session(uuid.UUID(payload["session_id"]))
                if identifier in runs or payload["attempt"] != 1 or payload["state"] != "queued":
                    raise ValueError("Invalid initial Run.")
                attempt = {
                    "state": "queued",
                    "pause_requested": False,
                    "domain_kind": None,
                    "domain_run_id": None,
                    "completion_hash": None,
                    "retry_key": None,
                    "base_revision": None,
                }
                runs[identifier] = {
                    "state": "queued",
                    "revision": 0,
                    "attempt_count": 1,
                    "input_hash": payload["input_hash"],
                    "attempts": {1: attempt},
                    "session_id": payload["session_id"],
                    "kind": payload["kind"],
                }
            elif kind == "run.state_changed":
                current = runs[identifier]
                if payload["state"] == "running":
                    require_live_session(uuid.UUID(current["session_id"]))
                    if current["kind"] == "planner":
                        await require_planner_protocol(identifier)
                number = current["attempt_count"]
                unresolved_id = next(
                    (key for key, row in planner_unresolved.items() if row.entity_id == identifier),
                    None,
                )
                if unresolved_id is not None and payload["state"] not in {
                    "paused",
                    "cancel_requested",
                    "cancelled",
                }:
                    raise ValueError("Unresolved Planner requires new input/admission.")
                if (
                    unresolved_id is not None
                    and payload["state"] == "cancelled"
                    and unresolved_id not in planner_unresolved_cancels
                ):
                    raise ValueError("Unresolved cancellation lacks its receipt.")
                if (
                    payload["before"] != current["state"]
                    or payload["attempt"] != number
                    or payload["revision"] != current["revision"] + 1
                ):
                    raise ValueError("Run event order mismatch.")
                require_transition(
                    State(current["state"]), State(payload["state"]), completion_proven=True
                )
                attempt = current["attempts"][number]
                if current["kind"] == "reviewer":
                    await reviewer_state.transition(db, identifier, current, payload, event)
                if current["kind"] == "reader" and payload["state"] in {"paused", "completed"}:
                    reader_attempt_id = await db.scalar(
                        select(ResearchAttempt.id).where(
                            ResearchAttempt.run_id == identifier, ResearchAttempt.number == number
                        )
                    )
                    if reader_attempt_id is None:
                        raise ValueError("Reader transition has no actual Attempt.")
                    if (payload["state"] == "paused" and not attempt["pause_requested"]) or (
                        payload["state"] == "completed"
                        and any(
                            item["action"] == "cancel"
                            for item in reader_controls.get(reader_attempt_id, [])
                        )
                    ):
                        raise ValueError("Reader transition crossed a control boundary.")
                if payload["state"] == "paused" and not attempt["pause_requested"]:
                    from app.models.research_execution import ResearchExecutionLease

                    actual_attempt = await db.scalar(
                        select(ResearchAttempt).where(
                            ResearchAttempt.run_id == identifier,
                            ResearchAttempt.number == number,
                        )
                    )
                    if (
                        actual_attempt is not None
                        and await db.get(ResearchExecutionLease, actual_attempt.id) is not None
                    ):
                        raise ValueError("Pause acknowledgement lacks request.")
                if (payload["before"] == "paused" and payload["state"] == "running") or payload[
                    "state"
                ] in {"cancel_requested", "cancelled"}:
                    attempt["pause_requested"] = False
                if payload["state"] == "completed" and attempt["pause_requested"]:
                    raise ValueError("Completion while pause requested.")
                current["state"], current["revision"] = payload["state"], payload["revision"]
                attempt["state"] = current["state"]
                for name in ("domain_kind", "domain_run_id"):
                    if name in payload:
                        attempt[name] = payload[name]
                attempt["completion_hash"] = (
                    payload.get("completion_hash", attempt.get("registered_hash"))
                    if current["state"] == "completed"
                    else None
                )
            elif kind == "run.retry_queued":
                current = runs[identifier]
                require_live_session(uuid.UUID(current["session_id"]))
                if (
                    State(current["state"]) not in RETRYABLE
                    or payload["before"] != current["state"]
                    or payload["previous_attempt"] != current["attempt_count"]
                    or payload["attempt"] != current["attempt_count"] + 1
                    or payload["base_revision"] != current["revision"]
                    or payload["revision"] != current["revision"] + 1
                    or payload["state"] != "queued"
                ):
                    raise ValueError("Invalid retry.")
                current.update(
                    state="queued", revision=payload["revision"], attempt_count=payload["attempt"]
                )
                current["attempts"][payload["attempt"]] = {
                    "state": "queued",
                    "pause_requested": False,
                    "domain_kind": None,
                    "domain_run_id": None,
                    "completion_hash": None,
                    "retry_key": payload["retry_key"],
                    "base_revision": payload["base_revision"],
                }
            elif kind in {"execution.pause_requested", "harness.wait_control"}:
                from app.models.research_execution import ResearchExecutionLease

                waiting = kind == "harness.wait_control"
                fields = {"attempt_id", "revision"} | ({"action"} if waiting else set())
                if event.entity_kind != "run" or set(payload) != fields:
                    raise ValueError("Pause control event route.")
                current = runs[identifier]
                attempt_id = uuid.UUID(payload["attempt_id"])
                actual_attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
                lease = await db.get(ResearchExecutionLease, attempt_id, populate_existing=True)
                projected = current["attempts"][current["attempt_count"]]
                if (
                    current["state"] != ("waiting_approval" if waiting else "running")
                    or actual_attempt is None
                    or actual_attempt.run_id != identifier
                    or actual_attempt.number != current["attempt_count"]
                    or lease is None
                    or type(payload["revision"]) is not int
                    or payload["revision"] != current["revision"] + (0 if waiting else 1)
                    or (waiting and payload["action"] not in {"pause", "resume"})
                ):
                    raise ValueError("Pause control event state mismatch.")
                requested = not waiting or payload["action"] == "pause"
                if requested == projected["pause_requested"]:
                    raise ValueError("Duplicate pause control event.")
                projected["pause_requested"] = requested
                if waiting:
                    harness_events.append(event)
                else:
                    current["revision"] = payload["revision"]
            elif kind == "execution.producer_exited":
                from app.models.research_execution import ResearchExecutionLease

                if event.entity_kind != "run" or set(payload) != {"attempt_id", "fence"}:
                    raise ValueError("Producer exit event route.")
                current = runs[identifier]
                attempt_id = uuid.UUID(payload["attempt_id"])
                actual_attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
                lease = await db.get(ResearchExecutionLease, attempt_id, populate_existing=True)
                if (
                    current["state"] != "interrupted"
                    or actual_attempt is None
                    or actual_attempt.run_id != identifier
                    or actual_attempt.number != current["attempt_count"]
                    or actual_attempt.state != "interrupted"
                    or actual_attempt.error_code != "EXECUTION_PRODUCER_EXITED"
                    or lease is None
                    or type(payload["fence"]) is not int
                    or payload["fence"] <= 0
                    or payload["fence"] != lease.fence
                    or lease.active_slot is not None
                    or lease.released_at_ms is None
                ):
                    raise ValueError("Producer exit event mismatch.")
            elif kind == "execution.result_recovered":
                from app.models.research_execution import (
                    ResearchExecutionDispatch,
                    ResearchExecutionIntent,
                    ResearchExecutionLease,
                    ResearchExecutionReceipt,
                )

                if event.entity_kind != "run" or set(payload) != {
                    "attempt_id",
                    "origin_fence",
                    "old_fence",
                    "fence",
                    "old_producer_pid",
                    "old_producer_birth",
                    "native_pid",
                    "native_birth",
                    "manifest_sha256",
                }:
                    raise ValueError("Result recovery event route.")
                current = runs[identifier]
                attempt_id = uuid.UUID(payload["attempt_id"])
                actual_attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
                intent = await db.get(ResearchExecutionIntent, attempt_id, populate_existing=True)
                dispatch = await db.get(
                    ResearchExecutionDispatch, attempt_id, populate_existing=True
                )
                receipt = await db.get(ResearchExecutionReceipt, attempt_id, populate_existing=True)
                lease = await db.get(ResearchExecutionLease, attempt_id, populate_existing=True)
                if (
                    current["state"] != "running"
                    or actual_attempt is None
                    or actual_attempt.run_id != identifier
                    or actual_attempt.number != current["attempt_count"]
                    or intent is None
                    or dispatch is None
                    or receipt is None
                    or lease is None
                    or not lease.result_only
                    or any(
                        type(payload[key]) is not int or payload[key] <= 0
                        for key in (
                            "origin_fence",
                            "old_fence",
                            "fence",
                            "old_producer_pid",
                            "native_pid",
                        )
                    )
                    or payload["origin_fence"] != intent.fence
                    or payload["old_fence"] != recovered_fences.get(attempt_id, intent.fence)
                    or payload["fence"] != payload["old_fence"] + 1
                    or payload["fence"] > lease.fence
                    or dispatch.fence != intent.fence
                    or dispatch.intent_hash != intent.intent_hash
                    or intent.input_hash != current["input_hash"]
                    or intent.intent_hash != logical_hash(parse_document(intent.document_json))
                    or receipt.manifest_sha256 != payload["manifest_sha256"]
                ):
                    raise ValueError("Result recovery event epoch mismatch.")
                manifest = parse_document(receipt.document_json)
                if (
                    hashlib.sha256(canonical_bytes(manifest)).hexdigest() != receipt.manifest_sha256
                    or manifest["intent_hash"] != intent.intent_hash
                    or manifest["producer"]
                    != {"pid": payload["native_pid"], "birth": payload["native_birth"]}
                    or manifest["result"]["sha256"] != receipt.result_sha256
                    or manifest["result"]["bytes"] != receipt.result_bytes
                    or type(payload["old_producer_birth"]) is not str
                    or not re.fullmatch(
                        r"win:[1-9][0-9]*|linux:[0-9a-f-]{36}:[0-9]+",
                        payload["old_producer_birth"],
                    )
                ):
                    raise ValueError("Result recovery event receipt mismatch.")
                recovered_fences[attempt_id] = payload["fence"]
            elif kind == "planning.admitted":
                from app.research import planner_admission

                current = runs[identifier]
                require_live_session(uuid.UUID(current["session_id"]))
                admission_run = await db.get(ResearchRun, identifier)
                if (
                    identifier in planner_admissions
                    or current["kind"] != "planner"
                    or current["state"] != "queued"
                    or current["attempt_count"] != 1
                    or await planner_admission.load(db, owner, admission_run) != payload
                ):
                    raise ValueError("Planner admission temporal source changed.")
                await require_planner_protocol(identifier)
                planner_admissions.add(identifier)
            elif kind == "planning.sdk_finished":
                from app.models.research_planning import ResearchPlannerSDKFinish
                from app.research import planner_finish

                sdk_run_id = uuid.UUID(payload["run_id"])
                sdk_run = await db.get(ResearchRun, sdk_run_id)
                sdk_finish = await db.get(
                    ResearchPlannerSDKFinish, identifier, populate_existing=True
                )
                if (
                    event.entity_kind != "planner_sdk_finish"
                    or identifier in planner_sdk_finishes
                    or sdk_finish is None
                    or sdk_run is None
                    or runs[sdk_run_id]["state"] != "completed"
                    or uuid.UUID(payload["receipt_id"]) not in planner_receipts
                    or await planner_finish.verify(db, owner, sdk_run, sdk_finish) != payload
                ):
                    raise ValueError("Planner SDK finish temporal binding changed.")
                planner_sdk_finishes.add(identifier)
            elif kind in reviewer_replay.KINDS:
                await reviewer_state.handle(db, owner, event, payload, runs, require_live_session)
            elif kind == "reader.claimed":
                from app.research.reader_identity import ReaderClaim

                reader_claim_value = ReaderClaim.model_validate(payload)
                claim_attempt_id = uuid.UUID(reader_claim_value.attempt_id)
                claim_attempt = await db.get(ResearchAttempt, claim_attempt_id)
                current = runs[identifier]
                if (
                    event.entity_kind != "run"
                    or event.actor_id != owner
                    or current["kind"] != "reader"
                    or current["state"] != "queued"
                    or reader_claim_value.owner_id != str(owner)
                    or reader_claim_value.run_id != str(identifier)
                    or reader_claim_value.input_hash != current["input_hash"]
                    or claim_attempt is None
                    or claim_attempt.run_id != identifier
                    or claim_attempt.number != current["attempt_count"]
                    or claim_attempt_id in reader_claims
                ):
                    raise ValueError("Reader original claim temporal identity changed.")
                reader_claims[claim_attempt_id] = payload
                reader_claim_events[claim_attempt_id] = event
                from app.research.reader_recovery_contracts import producer as reader_epoch_producer

                reader_epochs[claim_attempt_id] = reader_epoch_producer(
                    reader_claim_value
                ).model_dump(mode="json")
            elif kind == "reader.recovered":
                from app.models.research_harness import ResearchHarnessThread
                from app.research import reader_answer, reader_recovery
                from app.research.reader_identity import ReaderClaim
                from app.research.reader_recovery_contracts import Producer, RecoveryRecord

                reader_recovered = RecoveryRecord.model_validate(payload)
                recovered_attempt_id = uuid.UUID(reader_recovered.observation.claim.attempt_id)
                recovered_attempt = await db.get(ResearchAttempt, recovered_attempt_id)
                current = runs[identifier]
                if (
                    event.entity_kind != "run"
                    or event.actor_id != owner
                    or current["kind"] != "reader"
                    or reader_recovered.observation.claim.run_id != str(identifier)
                    or reader_recovered.observation.claim.owner_id != str(owner)
                    or recovered_attempt_id not in reader_claims
                    or recovered_attempt_id not in reader_answers
                    or recovered_attempt_id in reader_sdk_done
                    or recovered_attempt is None
                    or recovered_attempt.run_id != identifier
                    or recovered_attempt.number != current["attempt_count"]
                    or current["revision"] != reader_recovered.run_revision
                    or current["state"]
                    != ("running" if reader_recovered.phase == "answer_only" else "completed")
                    or current["attempts"][current["attempt_count"]]["pause_requested"]
                    or any(
                        item["action"] == "cancel"
                        for item in reader_controls.get(recovered_attempt_id, [])
                    )
                ):
                    raise ValueError("Reader recovery temporal authority changed.")
                projected = reader_recovery.successor(
                    reader_recovered,
                    reader_claim_events[recovered_attempt_id],
                    ReaderClaim.model_validate(reader_claims[recovered_attempt_id]),
                    Producer.model_validate(reader_epochs[recovered_attempt_id]),
                    reader_recoveries.get(recovered_attempt_id),
                )
                actual_run = await db.get(ResearchRun, identifier)
                receipt = await reader_answer.prove(db, owner, actual_run, recovered_attempt)
                recovered_binding = await db.get(
                    ResearchHarnessThread, uuid.UUID(reader_recovered.binding_id)
                )
                if (
                    reader_recovered.answer_id != str(receipt.id)
                    or reader_recovered.answer_hash != receipt.document_hash
                    or recovered_binding is None
                    or recovered_binding.run_id != identifier
                    or recovered_binding.owner_id != owner
                    or recovered_binding.attempt_id != recovered_attempt_id
                    or recovered_binding.binding_hash != reader_recovered.binding_hash
                ):
                    raise ValueError("Reader recovery historical receipt changed.")
                reader_epochs[recovered_attempt_id] = projected.model_dump(mode="json")
                reader_recoveries[recovered_attempt_id] = event
            elif kind == "reader.death_stopped":
                from app.research import reader_death_stop
                from app.research.reader_identity import ReaderClaim
                from app.research.reader_recovery_contracts import DeathStopRecord, Producer

                death_record = DeathStopRecord.model_validate(payload)
                death_attempt_id = uuid.UUID(death_record.observation.claim.attempt_id)
                death_attempt = await db.get(ResearchAttempt, death_attempt_id)
                current = runs[identifier]
                death_controls = reader_controls.get(death_attempt_id, [])
                has_cancel = any(item["action"] == "cancel" for item in death_controls)
                if (
                    event.entity_kind != "run"
                    or event.actor_id != owner
                    or current["kind"] != "reader"
                    or death_record.observation.claim.owner_id != str(owner)
                    or death_record.observation.claim.run_id != str(identifier)
                    or death_attempt_id in reader_deaths
                    or death_attempt_id in reader_stops
                    or death_attempt_id in reader_answers
                    or death_attempt_id in reader_sdk_done
                    or death_attempt is None
                    or death_attempt.run_id != identifier
                    or death_attempt.number != current["attempt_count"]
                    or current["state"] != death_record.before_state
                    or current["revision"] != death_record.run_revision
                    or has_cancel != (death_record.state == "cancelled")
                    or (
                        has_cancel
                        and (
                            death_controls[-1]["action"] != "cancel"
                            or death_controls[-1]["control_index"] != death_record.control_index
                        )
                    )
                ):
                    raise ValueError("Reader death stop temporal authority changed.")
                reader_death_stop.original(
                    death_record,
                    reader_claim_events[death_attempt_id],
                    ReaderClaim.model_validate(reader_claims[death_attempt_id]),
                    Producer.model_validate(reader_epochs[death_attempt_id]),
                )
                reader_deaths[death_attempt_id] = event
            elif kind == "reader.control_requested":
                control_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                current = runs[identifier]
                action = payload["action"]
                if action != "cancel":
                    require_live_session(uuid.UUID(current["session_id"]))
                previous_controls = reader_controls.setdefault(
                    control_attempt.id if control_attempt else uuid.UUID(payload["attempt_id"]), []
                )
                planned = current["attempts"][current["attempt_count"]]
                before_pause = planned["pause_requested"]
                revision_after = current["revision"] + (
                    1
                    if action == "pause" and current["state"] == "running" and not before_pause
                    else 0
                )
                if (
                    event.entity_kind != "run"
                    or current["kind"] != "reader"
                    or event.actor_id != owner
                    or control_attempt is None
                    or control_attempt.run_id != identifier
                    or control_attempt.number != current["attempt_count"]
                    or set(payload)
                    != {
                        "attempt_id",
                        "action",
                        "control_index",
                        "key_hash",
                        "request_hash",
                        "input_hash",
                        "revision_before",
                        "revision_after",
                        "pause_requested",
                    }
                    or action not in {"pause", "resume", "cancel"}
                    or (action != "cancel" and control_attempt.id in reader_answers)
                    or type(payload["control_index"]) is not int
                    or payload["control_index"] != len(previous_controls) + 1
                    or len(previous_controls) >= 100
                    or any(
                        item["key_hash"] == payload["key_hash"] or item["action"] == "cancel"
                        for item in previous_controls
                    )
                    or payload["input_hash"] != current["input_hash"]
                    or payload["revision_before"] != current["revision"]
                    or payload["revision_after"] != revision_after
                    or payload["pause_requested"] is not (action == "pause")
                    or payload["request_hash"]
                    != logical_hash(
                        dict(
                            run_id=str(identifier),
                            attempt_id=payload["attempt_id"],
                            revision=payload["revision_before"],
                            action=action,
                            key=payload["key_hash"],
                        )
                    )
                    or (
                        action == "pause"
                        and current["state"] not in {"running", "waiting_approval"}
                    )
                    or (
                        action == "resume"
                        and (
                            not before_pause
                            or current["state"] not in {"running", "paused", "waiting_approval"}
                        )
                    )
                    or (
                        action == "cancel"
                        and current["state"]
                        not in {"queued", "running", "paused", "waiting_approval"}
                    )
                ):
                    raise ValueError("Reader control request temporal proof changed.")
                previous_controls.append(payload)
                current["revision"] = revision_after
                planned["pause_requested"] = action == "pause"
            elif kind == "reader.control_acknowledged":
                control_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                current = runs[identifier]
                from app.models.research_planning import ResearchPlanningLease

                control_lease = (
                    await db.get(ResearchPlanningLease, control_attempt.id)
                    if control_attempt
                    else None
                )
                controls = reader_controls.get(uuid.UUID(payload["attempt_id"]), [])
                index = payload["control_index"]
                ack_fields = {
                    "attempt_id",
                    "control_index",
                    "action",
                    "revision",
                    "proof",
                    "fence",
                    "worker_id",
                }
                if payload.get("proof") == "verified_death":
                    ack_fields |= {"death_event_id", "death_hash"}
                if (
                    event.entity_kind != "run"
                    or current["kind"] != "reader"
                    or control_attempt is None
                    or control_attempt.run_id != identifier
                    or control_attempt.number != current["attempt_count"]
                    or set(payload) != ack_fields
                    or not controls
                    or type(index) is not int
                    or index != controls[-1]["control_index"]
                    or (control_attempt.id, index) in reader_control_acks
                    or payload["action"] != controls[-1]["action"]
                    or payload["revision"] != current["revision"]
                    or payload["proof"]
                    not in {"worker_boundary", "never_claimed", "verified_death"}
                    or (
                        payload["proof"] == "worker_boundary"
                        and control_attempt.id in reader_deaths
                    )
                    or event.actor_id != owner
                    or (
                        payload["proof"] == "never_claimed"
                        and (
                            control_lease is not None
                            or payload["fence"] is not None
                            or payload["worker_id"] is not None
                        )
                    )
                    or (
                        payload["proof"] == "worker_boundary"
                        and (
                            control_lease is None
                            or type(payload["fence"]) is not int
                            or payload["fence"]
                            != reader_epochs.get(control_attempt.id, {}).get(
                                "fence", control_lease.fence
                            )
                            or payload["worker_id"]
                            != reader_epochs.get(control_attempt.id, {}).get(
                                "worker_id", control_lease.worker_id
                            )
                        )
                    )
                    or (
                        payload["proof"] == "never_claimed"
                        and (payload["action"] != "cancel" or current["state"] != "cancelled")
                    )
                    or (payload["action"] == "cancel" and current["state"] != "cancelled")
                    or (
                        payload["action"] == "pause"
                        and current["state"] not in {"paused", "waiting_approval"}
                    )
                    or (
                        payload["action"] == "resume"
                        and current["state"] not in {"running", "waiting_approval"}
                    )
                ):
                    raise ValueError("Reader control acknowledgement changed.")
                if payload["proof"] == "verified_death":
                    death_event = reader_deaths.get(control_attempt.id)
                    death_stop = reader_stops.get(control_attempt.id, {})
                    if (
                        death_event is None
                        or control_lease is None
                        or payload["action"] != "cancel"
                        or death_stop.get("proof") != "verified_death"
                        or payload["death_event_id"] != str(death_event.id)
                        or payload["death_hash"] != death_event.logical_hash
                        or event.sequence != death_event.sequence + 3
                        or type(payload["fence"]) is not int
                        or payload["fence"] != reader_epochs[control_attempt.id]["fence"]
                        or payload["worker_id"] != reader_epochs[control_attempt.id]["worker_id"]
                        or payload["death_event_id"] != death_stop.get("death_event_id")
                        or payload["death_hash"] != death_stop.get("death_hash")
                    ):
                        raise ValueError("Reader death acknowledgement changed.")
                reader_control_acks.add((control_attempt.id, index))
            elif kind == "reader.stopped":
                from app.models.research_planning import ResearchPlanningLease

                attempt_id = uuid.UUID(payload["attempt_id"])
                current = runs[identifier]
                stop_attempt = await db.get(ResearchAttempt, attempt_id)
                stop_lease = await db.get(ResearchPlanningLease, attempt_id, populate_existing=True)
                stop_fields = {
                    "attempt_id",
                    "fence",
                    "worker_id",
                    "producer_host",
                    "producer_pid",
                    "producer_birth",
                    "state",
                    "proof",
                    "released_at_ms",
                }
                if payload.get("proof") == "verified_death":
                    stop_fields |= {"death_event_id", "death_hash"}
                if (
                    event.entity_kind != "run"
                    or event.actor_id != owner
                    or current["kind"] != "reader"
                    or attempt_id in reader_stops
                    or stop_attempt is None
                    or stop_lease is None
                    or stop_attempt.run_id != identifier
                    or stop_attempt.number != current["attempt_count"]
                    or set(payload) != stop_fields
                    or payload["proof"] not in {"owned_boundary", "verified_death"}
                    or type(payload["fence"]) is not int
                    or type(payload["producer_pid"]) is not int
                    or type(payload["released_at_ms"]) is not int
                    or payload["state"] not in {"failed", "cancelled", "interrupted"}
                    or stop_lease.active_owner_id is not None
                    or stop_lease.owner_id != owner
                    or stop_lease.released_at_ms != payload["released_at_ms"]
                    or any(
                        getattr(stop_lease, name) != payload[name]
                        for name in (
                            "fence",
                            "worker_id",
                            "producer_host",
                            "producer_pid",
                            "producer_birth",
                        )
                    )
                ):
                    raise ValueError("Reader terminal release proof changed.")
                if payload["proof"] == "verified_death":
                    from app.research import reader_lease

                    death_event = reader_deaths.get(attempt_id)
                    if death_event is None or payload["death_event_id"] != str(death_event.id):
                        raise ValueError("Reader stop lacks its prior death event.")
                    actual_run = await db.get(ResearchRun, identifier)
                    await reader_lease.prove_stop(db, owner, actual_run, stop_lease)
                require_transition(State(current["state"]), State(payload["state"]))
                if payload["state"] != "cancelled":
                    from app.research import reader_lease

                    if await reader_lease.answer_event(db, attempt_id) is not None:
                        raise ValueError("Receipted Reader cannot be interrupted or retried.")
                if payload["state"] == "cancelled" and not any(
                    item["action"] == "cancel" for item in reader_controls.get(attempt_id, [])
                ):
                    raise ValueError("Reader cancel stop lacks its request.")
                current["attempts"][current["attempt_count"]]["pause_requested"] = False
                reader_stops[attempt_id] = payload
            elif kind == "planning.control_requested":
                control_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                current = runs[identifier]
                action = payload["action"]
                if action != "cancel":
                    require_live_session(uuid.UUID(current["session_id"]))
                previous_controls = planner_controls.setdefault(
                    control_attempt.id if control_attempt else uuid.UUID(payload["attempt_id"]), []
                )
                planned = current["attempts"][current["attempt_count"]]
                before_pause = planned["pause_requested"]
                revision_after = current["revision"] + (
                    1
                    if action == "pause" and current["state"] == "running" and not before_pause
                    else 0
                )
                if (
                    event.entity_kind != "run"
                    or current["kind"] != "planner"
                    or control_attempt is None
                    or control_attempt.run_id != identifier
                    or control_attempt.number != current["attempt_count"]
                    or set(payload)
                    != {
                        "attempt_id",
                        "action",
                        "control_index",
                        "key_hash",
                        "request_hash",
                        "input_hash",
                        "revision_before",
                        "revision_after",
                        "pause_requested",
                    }
                    or action not in {"pause", "resume", "cancel"}
                    or type(payload["control_index"]) is not int
                    or payload["control_index"] != len(previous_controls) + 1
                    or len(previous_controls) >= 100
                    or any(
                        item["key_hash"] == payload["key_hash"] or item["action"] == "cancel"
                        for item in previous_controls
                    )
                    or payload["input_hash"] != current["input_hash"]
                    or payload["revision_before"] != current["revision"]
                    or payload["revision_after"] != revision_after
                    or payload["pause_requested"] is not (action == "pause")
                    or payload["request_hash"]
                    != logical_hash(
                        dict(
                            run_id=str(identifier),
                            attempt_id=payload["attempt_id"],
                            revision=payload["revision_before"],
                            action=action,
                            key=payload["key_hash"],
                        )
                    )
                    or (
                        action == "pause"
                        and current["state"] not in {"running", "waiting_approval"}
                    )
                    or (
                        action == "resume"
                        and (
                            not before_pause
                            or current["state"] not in {"running", "paused", "waiting_approval"}
                        )
                    )
                    or (
                        action == "cancel"
                        and current["state"]
                        not in {"queued", "running", "paused", "waiting_approval"}
                    )
                ):
                    raise ValueError("Planner control request temporal proof changed.")
                previous_controls.append(payload)
                current["revision"] = revision_after
                planned["pause_requested"] = action == "pause"
            elif kind == "planning.control_acknowledged":
                control_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                current = runs[identifier]
                controls = planner_controls.get(uuid.UUID(payload["attempt_id"]), [])
                index = payload["control_index"]
                if (
                    event.entity_kind != "run"
                    or current["kind"] != "planner"
                    or control_attempt is None
                    or control_attempt.run_id != identifier
                    or control_attempt.number != current["attempt_count"]
                    or set(payload)
                    != {"attempt_id", "control_index", "action", "revision", "proof"}
                    or not controls
                    or type(index) is not int
                    or index != controls[-1]["control_index"]
                    or (control_attempt.id, index) in planner_control_acks
                    or payload["action"] != controls[-1]["action"]
                    or payload["revision"] != current["revision"]
                    or payload["proof"]
                    not in {"worker_boundary", "never_claimed", "unresolved_boundary"}
                    or (
                        payload["proof"] == "unresolved_boundary"
                        and (
                            payload["action"] != "cancel"
                            or current["state"] != "cancelled"
                            or control_attempt.id not in planner_unresolved_cancels
                            or planner_unresolved_cancels[control_attempt.id]["control_index"]
                            != index
                        )
                    )
                    or (
                        control_attempt.id in planner_unresolved
                        and payload["proof"] != "unresolved_boundary"
                    )
                    or (
                        payload["proof"] == "never_claimed"
                        and (payload["action"] != "cancel" or current["state"] != "cancelled")
                    )
                    or (payload["action"] == "cancel" and current["state"] != "cancelled")
                    or (
                        payload["action"] == "pause"
                        and current["state"] not in {"paused", "waiting_approval"}
                    )
                    or (
                        payload["action"] == "resume"
                        and current["state"] not in {"running", "waiting_approval"}
                    )
                ):
                    raise ValueError("Planner control acknowledgement changed.")
                planner_control_acks.add((control_attempt.id, index))
            elif kind == "planning.unresolved_recorded":
                from app.research import planner_unresolved as unresolved_proof

                attempt_id = uuid.UUID(payload["attempt_id"])
                current = runs[identifier]
                actual_run, _ = await ledger.run(db, identifier, owner)
                actual_attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
                if (
                    current["kind"] != "planner"
                    or current["state"] != "running"
                    or current["revision"] != payload["revision_before"]
                    or attempt_id in planner_unresolved
                    or actual_attempt is None
                    or actual_attempt.run_id != identifier
                    or actual_attempt.number != current["attempt_count"]
                    or current["attempts"][current["attempt_count"]]["pause_requested"]
                    or await unresolved_proof.prove(db, owner, actual_run, actual_attempt)
                    is not event
                ):
                    raise ValueError("Planner unresolved final reply proof changed.")
                planner_unresolved[attempt_id] = event
            elif kind == "planning.unresolved_cancelled":
                from app.research import planner_unresolved as unresolved_proof

                attempt_id = uuid.UUID(payload["attempt_id"])
                current = runs[identifier]
                previous = planner_unresolved.get(attempt_id)
                controls = planner_controls.get(attempt_id, [])
                if (
                    set(payload) != {"version", "attempt_id", "unresolved_hash", "control_index"}
                    or type(payload["version"]) is not int
                    or payload["version"] != 1
                    or type(payload["control_index"]) is not int
                    or previous is None
                    or previous.entity_id != identifier
                    or event.entity_kind != "run"
                    or current["state"] != "cancel_requested"
                    or payload["unresolved_hash"] != previous.logical_hash
                    or attempt_id in planner_unresolved_cancels
                    or not controls
                    or controls[-1]["action"] != "cancel"
                    or controls[-1]["control_index"] != payload["control_index"]
                ):
                    raise ValueError("Planner unresolved cancellation changed.")
                planner_unresolved_cancels[attempt_id] = payload
            elif kind == "planning.receipt_recovered":
                from app.models.research_planning import ResearchPlanningLease, ResearchPlanReceipt

                attempt_id = uuid.UUID(payload["attempt_id"])
                recovery_attempt = await db.get(ResearchAttempt, attempt_id)
                recovery_lease = await db.get(
                    ResearchPlanningLease, attempt_id, populate_existing=True
                )
                recovered_receipt = await db.get(
                    ResearchPlanReceipt, uuid.UUID(payload["receipt_id"])
                )
                previous = planner_recoveries.get(attempt_id)
                if (
                    set(payload)
                    != {
                        "attempt_id",
                        "receipt_id",
                        "receipt_hash",
                        "old_fence",
                        "old_worker_id",
                        "old_producer_host",
                        "old_producer_pid",
                        "old_producer_birth",
                        "fence",
                        "worker_id",
                        "producer_host",
                        "producer_pid",
                        "producer_birth",
                    }
                    or event.entity_kind != "run"
                    or runs[identifier]["kind"] != "planner"
                    or runs[identifier]["state"] != "running"
                    or recovery_attempt is None
                    or recovery_lease is None
                    or recovery_attempt.run_id != identifier
                    or recovery_attempt.number != runs[identifier]["attempt_count"]
                    or recovered_receipt is None
                    or recovered_receipt.id not in planner_receipts
                    or recovered_receipt.attempt_id != attempt_id
                    or recovered_receipt.owner_id != owner
                    or recovered_receipt.document_hash != payload["receipt_hash"]
                    or type(payload["fence"]) is not int
                    or type(payload["old_fence"]) is not int
                    or payload["fence"] != payload["old_fence"] + 1
                    or payload["old_fence"] < recovered_receipt.fence
                    or (previous is None and payload["old_fence"] != recovered_receipt.fence)
                    or (
                        previous is not None
                        and any(
                            payload["old_" + key] != previous[key]
                            for key in (
                                "fence",
                                "worker_id",
                                "producer_host",
                                "producer_pid",
                                "producer_birth",
                            )
                        )
                    )
                    or payload["producer_host"] != payload["old_producer_host"]
                ):
                    raise ValueError("Planner receipt recovery proof changed.")
                planner_recoveries[attempt_id] = payload
            elif kind == "planning.stopped":
                from app.models.research_planning import ResearchPlanningLease

                attempt_id = uuid.UUID(payload["attempt_id"])
                current = runs[identifier]
                stop_attempt = await db.get(ResearchAttempt, attempt_id)
                stop_lease = await db.get(ResearchPlanningLease, attempt_id, populate_existing=True)
                if (
                    event.entity_kind != "run"
                    or current["kind"] != "planner"
                    or attempt_id in planner_stops
                    or stop_attempt is None
                    or stop_lease is None
                    or stop_attempt.run_id != identifier
                    or stop_attempt.number != current["attempt_count"]
                    or set(payload)
                    != {
                        "attempt_id",
                        "fence",
                        "worker_id",
                        "producer_host",
                        "producer_pid",
                        "producer_birth",
                        "state",
                        "proof",
                        "released_at_ms",
                    }
                    or payload["proof"] not in {"producer_dead", "owned_boundary"}
                    or payload["state"] not in {"failed", "cancelled", "interrupted"}
                    or stop_lease.active_owner_id is not None
                    or stop_lease.owner_id != owner
                    or stop_lease.released_at_ms != payload["released_at_ms"]
                    or any(
                        getattr(stop_lease, name) != payload[name]
                        for name in (
                            "fence",
                            "worker_id",
                            "producer_host",
                            "producer_pid",
                            "producer_birth",
                        )
                    )
                ):
                    raise ValueError("Planner terminal release proof changed.")
                require_transition(State(current["state"]), State(payload["state"]))
                planner_stops[attempt_id] = payload
            elif kind == "planning.receipt_recorded":
                from app.models.research_planning import ResearchPlanVersion
                from app.research import plan_receipt

                run_id = uuid.UUID(payload["run_id"])
                current = runs[run_id]
                require_live_session(uuid.UUID(current["session_id"]))
                actual_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                version = await db.get(ResearchPlanVersion, uuid.UUID(payload["plan_version_id"]))
                context = drafts.get(version.draft_id) if version is not None else None
                if (
                    identifier in planner_receipts
                    or event.entity_kind != "plan_receipt"
                    or current["kind"] != "planner"
                    or current["state"] != "running"
                    or actual_attempt is None
                    or actual_attempt.run_id != run_id
                    or actual_attempt.number != current["attempt_count"]
                    or current["attempts"][current["attempt_count"]]["pause_requested"]
                    or version is None
                    or version.draft_id not in drafts
                    or drafts[version.draft_id]["version_id"] != str(version.id)
                    or context is None
                    or context["context"]["attachment_revision"]
                    != attachment_revisions.get(uuid.UUID(current["session_id"]), 0)
                    or context["context"]["workspace_epoch"]
                    != workspace_lifecycle["lifecycle_epoch"]
                    or context["context"]["session_epoch"]
                    != session_lifecycle[uuid.UUID(current["session_id"])]["lifecycle_epoch"]
                ):
                    raise ValueError("Planner receipt temporal binding changed.")
                actual_run = await db.get(ResearchRun, run_id)
                receipt = await plan_receipt.prove(db, owner, actual_run, actual_attempt)
                if receipt.id != identifier:
                    raise ValueError("Planner receipt event identity changed.")
                planner_receipts.add(identifier)
            elif kind == "reader.sdk_finished":
                from app.models.research_harness import ResearchHarnessThread
                from app.research import reader_sdk_finish

                current = runs[uuid.UUID(payload["run_id"])]
                sdk_attempt = current["attempts"][current["attempt_count"]]
                if (
                    current["kind"] != "reader"
                    or current["state"] != "completed"
                    or sdk_attempt["domain_kind"] != "reader_answer"
                    or sdk_attempt["domain_run_id"] != payload["answer_id"]
                    or sdk_attempt["completion_hash"] != payload["answer_hash"]
                ):
                    raise ValueError("Reader SDK finish precedes business completion.")
                actual_run = await db.get(ResearchRun, uuid.UUID(payload["run_id"]))
                thread = await db.get(ResearchHarnessThread, uuid.UUID(payload["binding_id"]))
                if thread is None:
                    raise ValueError("Reader SDK thread missing.")
                proof = await reader_sdk_finish.verify(db, owner, actual_run, thread)
                if proof.id != event.id:
                    raise ValueError("Reader SDK event identity changed.")
                reader_sdk_done.add(uuid.UUID(payload["attempt_id"]))
            elif kind in {"reader.answer_receipted", "reader.tool_recorded"}:
                from app.research import reader_answer, reader_runtime

                run_id = uuid.UUID(payload["run_id"])
                current = runs[run_id]
                require_live_session(uuid.UUID(current["session_id"]))
                actual_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                if (
                    current["kind"] != "reader"
                    or current["state"] != "running"
                    or actual_attempt is None
                    or actual_attempt.run_id != run_id
                    or actual_attempt.number != current["attempt_count"]
                    or current["attempts"][current["attempt_count"]]["pause_requested"]
                    or any(
                        item["action"] == "cancel"
                        for item in reader_controls.get(actual_attempt.id, [])
                    )
                ):
                    raise ValueError("Reader result temporal binding changed.")
                actual_run = await db.get(ResearchRun, run_id)
                if kind == reader_answer.EVENT:
                    receipt = await reader_answer.prove(db, owner, actual_run, actual_attempt)
                    if receipt.id != event.id or payload["run_revision"] != current["revision"]:
                        raise ValueError("Reader answer event identity changed.")
                    reader_answers.add(actual_attempt.id)
                else:
                    await reader_runtime.prove_event(db, owner, event)
            elif kind.startswith("advice."):
                from app.services import draft_advice_service

                value = await draft_advice_service.prove_event(db, owner, event)
                current = runs[identifier]
                if current["kind"] != "draft_advice" or value["run_id"] != str(identifier):
                    raise ValueError("Advice event kind changed.")
                if kind == "advice.applied" and current["state"] != "completed":
                    raise ValueError("Advice was applied before a proved model reply.")
                if kind == "advice.proposed" and current["state"] != "running":
                    raise ValueError("Advice reply is not resumed.")
                if kind in {"advice.dispatched", "advice.cancel_requested"} and current[
                    "state"
                ] not in {"running", "waiting_approval", "cancel_requested"}:
                    raise ValueError("Advice request is not active.")
            elif kind == "artifact.registered":
                if identifier in roots:
                    raise ValueError("Duplicate artifact registration.")
                roots[identifier] = payload
                current = runs[uuid.UUID(payload["run_id"])]
                attempt = current["attempts"][current["attempt_count"]]
                attempt["domain_kind"] = payload["domain_kind"]
                attempt["domain_run_id"] = payload["domain_run_id"]
                attempt["registered_hash"] = payload["logical_hash"]
            elif kind in {"security.policy", "security.approval", "security.charge"}:
                if kind in {"security.approval", "security.charge"} and payload["state"] in {
                    "proposed",
                    "pending",
                    "approved",
                    "consumed",
                    "reserved",
                    "dispatched",
                }:
                    secured_run = uuid.UUID(payload["run_id"])
                    if runs[secured_run]["kind"] == "planner":
                        await require_planner_protocol(secured_run)
                security_events.append(event)
            elif kind in harness_replay.KINDS:
                if kind in {"reader.admitted", "reviewer.admitted"}:
                    current = runs[uuid.UUID(payload["run_id"])]
                    require_live_session(uuid.UUID(current["session_id"]))
                    actual_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                    if (
                        current["kind"] != ("reviewer" if kind == "reviewer.admitted" else "reader")
                        or current["state"] != "queued"
                        or current["attempt_count"] != 1
                        or actual_attempt is None
                        or actual_attempt.number != 1
                        or actual_attempt.run_id != uuid.UUID(payload["run_id"])
                    ):
                        raise ValueError("Reader admission temporal identity changed.")
                    if kind == "reviewer.admitted":
                        from app.research import reviewer_admission

                        actual_run = await db.get(ResearchRun, uuid.UUID(payload["run_id"]))
                        await reviewer_admission.before_event(
                            db, owner, actual_run, event, plan_results
                        )
                        reviewer_state.admissions.add(actual_attempt.id)
                if kind == "planning.invocation_registered":
                    current = runs[uuid.UUID(payload["run_id"])]
                    require_live_session(uuid.UUID(current["session_id"]))
                    actual_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
                    if (
                        current["kind"] != "planner"
                        or current["state"] != "running"
                        or actual_attempt is None
                        or actual_attempt.number != current["attempt_count"]
                        or actual_attempt.run_id != uuid.UUID(payload["run_id"])
                    ):
                        raise ValueError("Planner invocation temporal source changed.")
                    await require_planner_protocol(uuid.UUID(payload["run_id"]))
                if kind == "harness.model_request":
                    model_run_id = uuid.UUID(payload["run_id"])
                    if runs[model_run_id]["kind"] == "planner":
                        await require_planner_protocol(model_run_id)
                harness_events.append(event)
            else:
                raise ValueError("Unsupported business event.")
        if not workspace_created:
            raise ValueError("Missing creation.")
        reviewer_state.finalize()
        if set(reader_deaths) != {
            identifier
            for identifier, value in reader_stops.items()
            if value["proof"] == "verified_death"
        }:
            raise ValueError("Reader death and release event sets differ.")
        for identifier in reader_deaths:
            if reader_stops[identifier]["state"] == "cancelled":
                requests = reader_controls.get(identifier, [])
                if (
                    not requests
                    or (identifier, requests[-1]["control_index"]) not in reader_control_acks
                ):
                    raise ValueError("Reader death cancel lacks its acknowledgement.")
        if workspace_lifecycle != {
            "project_id": str(scope.project_id) if scope.project_id else None,
            "revision": scope.revision,
            "lifecycle_epoch": scope.lifecycle_epoch,
            "is_deleted": scope.is_deleted,
        }:
            raise ValueError("Workspace lifecycle state mismatch.")
        actual_sessions = (
            await db.scalars(
                select(ResearchSession).where(
                    ResearchSession.workspace_id == scope.id,
                ),
                execution_options={"populate_existing": True},
            )
        ).all()
        from app.models.research_planning import ResearchPlanReceipt

        actual_receipts = set(
            (
                await db.scalars(
                    select(ResearchPlanReceipt.id)
                    .join(ResearchRun, ResearchRun.id == ResearchPlanReceipt.run_id)
                    .where(
                        ResearchRun.session_id.in_(sessions), ResearchPlanReceipt.owner_id == owner
                    )
                )
            ).all()
        )
        if actual_receipts != planner_receipts:
            raise ValueError("Planner receipt set differs from its events.")
        from app.models.research_planning import ResearchPlannerSDKFinish

        actual_finishes = set(
            (
                await db.scalars(
                    select(ResearchPlannerSDKFinish.id)
                    .join(ResearchRun, ResearchRun.id == ResearchPlannerSDKFinish.run_id)
                    .where(
                        ResearchRun.session_id.in_(sessions),
                        ResearchPlannerSDKFinish.owner_id == owner,
                    )
                )
            ).all()
        )
        if actual_finishes != planner_sdk_finishes:
            raise ValueError("Planner SDK finish event/row set mismatch.")
        from app.models.research_planning import ResearchPlanAcceptance, ResearchPlanStep

        acceptance_rows = (
            await db.scalars(
                select(ResearchPlanAcceptance)
                .where(ResearchPlanAcceptance.session_id.in_(sessions))
                .execution_options(populate_existing=True)
            )
        ).all()
        if {row.id for row in acceptance_rows} != plan_acceptances:
            raise ValueError("Plan acceptance event/row set mismatch.")
        for acceptance in acceptance_rows:
            if (
                dict(revision=acceptance.revision, cancel_requested=acceptance.cancel_requested)
                != plan_execution_controls[acceptance.id]
            ):
                raise ValueError("Plan execution control lacks an event.")
        step_rows = (
            await db.scalars(
                select(ResearchPlanStep)
                .join(
                    ResearchPlanAcceptance,
                    ResearchPlanAcceptance.id == ResearchPlanStep.acceptance_id,
                )
                .where(ResearchPlanAcceptance.session_id.in_(sessions))
                .execution_options(populate_existing=True)
            )
        ).all()
        from app.research import protocol_launch

        await protocol_launch.verify_sets(
            db, saved_plan_versions, acceptance_rows, step_rows, protocol_launches, recovered_fences
        )
        for step in step_rows:
            if step.child_run_id != plan_children.get(step.id):
                raise ValueError("Plan step child row/event mismatch.")
            result = plan_results.get(step.id)
            if result is None:
                if (
                    step.result_attempt_id is not None
                    or step.result_json is not None
                    or step.result_hash is not None
                ):
                    raise ValueError("Plan result lacks an event.")
            elif (
                step.result_hash != logical_hash(result)
                or step.result_json is None
                or parse_document(step.result_json) != result
                or (str(step.result_attempt_id) if step.result_attempt_id else None)
                != (
                    result["citation"].get("attempt_id")
                    if result["citation"].get("kind") == "native_result"
                    else None
                )
            ):
                raise ValueError("Plan step result row/event mismatch.")
        if {row.id for row in actual_sessions} != set(sessions):
            raise ValueError("Session set mismatch.")
        from app.research import protocol_proof

        await protocol_proof.verify_sets(db, sessions, protocol_versions, protocol_activation_ids)
        for session_row in actual_sessions:
            if session_row.title != sessions[session_row.id] or session_lifecycle[
                session_row.id
            ] != {
                "revision": session_row.revision,
                "lifecycle_epoch": session_row.lifecycle_epoch,
                "is_archived": session_row.is_archived,
                "is_deleted": session_row.is_deleted,
            }:
                raise ValueError("Session mismatch.")
        ids = list(sessions)
        actual_runs = (
            await db.scalars(
                select(ResearchRun).where(ResearchRun.session_id.in_(ids)),
                execution_options={"populate_existing": True},
            )
        ).all()
        if {row.id for row in actual_runs} != set(runs):
            raise ValueError("Run set mismatch.")
        for run_row in actual_runs:
            expected = runs[run_row.id]
            if (
                run_row.state != expected["state"]
                or run_row.revision != expected["revision"]
                or run_row.attempt_count != expected["attempt_count"]
                or run_row.input_hash != expected["input_hash"]
                or logical_hash(parse_document(run_row.input_json)) != run_row.input_hash
            ):
                raise ValueError("Run state mismatch.")
            if (
                str(run_row.session_id) != expected["session_id"]
                or run_row.kind != expected["kind"]
            ):
                raise ValueError("Run identity mismatch.")
            attempts = (
                await db.scalars(
                    select(ResearchAttempt)
                    .where(
                        ResearchAttempt.run_id == run_row.id,
                    )
                    .order_by(ResearchAttempt.number),
                    execution_options={"populate_existing": True},
                )
            ).all()
            if [attempt.number for attempt in attempts] != list(
                range(1, run_row.attempt_count + 1)
            ):
                raise ValueError("Attempt set mismatch.")
            for attempt_row in attempts:
                from app.models.research_execution import ResearchExecutionLease
                from app.models.research_planning import ResearchPlanningLease

                lease = await db.get(ResearchExecutionLease, attempt_row.id, populate_existing=True)
                planning = await db.get(
                    ResearchPlanningLease, attempt_row.id, populate_existing=True
                )
                if planning is not None and (
                    run_row.kind not in {"planner", "reader", "reviewer"}
                    or lease is not None
                    or planning.owner_id != owner
                    or (attempt_row.state == "completed" and planning.released_at_ms is None)
                ):
                    raise ValueError("Planner claim/completion identity mismatch.")
                if attempt_row.id in reader_claims and (
                    run_row.kind != "reader" or planning is None
                ):
                    raise ValueError("Reader original claim lease is missing.")
                if run_row.kind == "reviewer":
                    reviewer_state.final_attempt(owner, run_row, attempt_row, planning)
                if planning is not None:
                    if run_row.kind == "reader" and attempt_row.id in reader_claims:
                        from app.research.reader_recovery_contracts import Producer

                        if Producer.model_validate(
                            reader_epochs[attempt_row.id]
                        ) != reader_epoch_producer(planning):
                            raise ValueError("Reader original claim final identity mismatch.")
                    if (
                        planning.pause_requested
                        != expected["attempts"][attempt_row.number]["pause_requested"]
                    ):
                        raise ValueError("Planner pause final flag mismatch.")
                    recovery = planner_recoveries.get(attempt_row.id)
                    if run_row.kind == "planner" and (
                        (recovery is None and planning.fence != 1)
                        or (
                            recovery is not None
                            and any(
                                getattr(planning, name) != recovery[name]
                                for name in (
                                    "fence",
                                    "worker_id",
                                    "producer_host",
                                    "producer_pid",
                                    "producer_birth",
                                )
                            )
                        )
                    ):
                        raise ValueError("Planner recovery final identity mismatch.")
                    if (
                        run_row.kind == "reader"
                        and attempt_row.id not in reader_claims
                        and planning.fence != 1
                    ):
                        raise ValueError("Legacy Reader has no original recovery identity.")
                stopped_by_kind = (
                    reviewer_state.stops
                    if run_row.kind == "reviewer"
                    else reader_stops
                    if run_row.kind == "reader"
                    else planner_stops
                )
                unresolved = planner_unresolved.get(attempt_row.id)
                if run_row.kind == "planner" and planning is not None:
                    if unresolved is not None:
                        if (
                            attempt_row.state not in {"paused", "cancelled"}
                            or planning.released_at_ms is None
                            or planning.active_owner_id is not None
                            or planning.pause_requested
                            or (
                                attempt_row.state == "cancelled"
                                and (
                                    attempt_row.id not in planner_unresolved_cancels
                                    or (
                                        attempt_row.id,
                                        planner_unresolved_cancels[attempt_row.id]["control_index"],
                                    )
                                    not in planner_control_acks
                                )
                            )
                        ):
                            raise ValueError("Unresolved Planner final state changed.")
                    elif attempt_row.state == "paused" and planning.released_at_ms is not None:
                        raise ValueError("Released Planner pause lacks its final reply receipt.")
                if (
                    planning is not None
                    and attempt_row.state in {"failed", "cancelled", "interrupted"}
                    and unresolved is None
                    and (
                        attempt_row.id not in stopped_by_kind
                        or planning.released_at_ms is None
                        or stopped_by_kind[attempt_row.id]["state"] != attempt_row.state
                    )
                ):
                    raise ValueError("Planner terminal lacks a release event.")
                if (lease is not None and lease.result_only) != (
                    attempt_row.id in recovered_fences
                ):
                    raise ValueError("Result recovery event set mismatch.")
                if attempt_row.id in recovered_fences and (
                    lease is None or lease.fence != recovered_fences[attempt_row.id]
                ):
                    raise ValueError("Result recovery final epoch mismatch.")
                planned = expected["attempts"][attempt_row.number]
                if lease is not None and lease.pause_requested != planned["pause_requested"]:
                    raise ValueError("Pause control final flag mismatch.")
                for name in (
                    "state",
                    "domain_kind",
                    "completion_hash",
                    "retry_key",
                    "base_revision",
                ):
                    if getattr(attempt_row, name) != planned[name]:
                        raise ValueError("Attempt mismatch.")
                if (
                    str(attempt_row.domain_run_id) if attempt_row.domain_run_id else None
                ) != planned["domain_run_id"]:
                    raise ValueError("Attempt source mismatch.")
            current_attempt = attempts[-1]
            if (
                run_row.domain_kind != current_attempt.domain_kind
                or run_row.domain_run_id != current_attempt.domain_run_id
                or run_row.completion_hash != current_attempt.completion_hash
            ):
                raise ValueError("Current attempt proof mismatch.")
            if (
                run_row.state == "completed"
                and await ledger._prove_completion(db, owner, run_row, current_context=False)
                != run_row.completion_hash
            ):
                raise ValueError("Completion proof mismatch.")
        actual_attachments = (
            await db.scalars(
                select(ResearchAttachment).where(
                    ResearchAttachment.session_id.in_(ids),
                ),
                execution_options={"populate_existing": True},
            )
        ).all()
        if {row.id for row in actual_attachments} != set(attachments):
            raise ValueError("Attachment set mismatch.")
        for attachment_row in actual_attachments:
            expected = attachments[attachment_row.id]
            if (
                attachment_row.active != expected["active"]
                or attachment_row.target_kind != expected["kind"]
                or str(attachment_row.session_id) != expected["session_id"]
                or str(attachment_row.target_id) != expected["target_id"]
                or attachment_row.target_sha256 != expected["sha256"]
                or str(attachment_row.source_project_id) != expected["source_project_id"]
            ):
                raise ValueError("Attachment mismatch.")
        actual_messages = (
            await db.scalars(
                select(ResearchMessage).where(
                    ResearchMessage.session_id.in_(ids),
                ),
                execution_options={"populate_existing": True},
            )
        ).all()
        if {row.id for row in actual_messages} != set(messages):
            raise ValueError("Message set mismatch.")
        for message_row in actual_messages:
            expected = messages[message_row.id]
            if (
                message_row.role != expected["role"]
                or message_row.logical_hash != expected["logical_hash"]
                or str(message_row.session_id) != expected["session_id"]
                or (str(message_row.run_id) if message_row.run_id else None) != expected["run_id"]
                or logical_hash(parse_document(message_row.content_json))
                != message_row.logical_hash
            ):
                raise ValueError("Message mismatch.")
        owned_artifacts = (
            await db.scalars(
                select(ResearchArtifact).where(
                    ResearchArtifact.run_id.in_(list(runs)),
                ),
                execution_options={"populate_existing": True},
            )
        ).all()
        from app.research.domain_results import load_snapshot

        reachable = set(roots)
        for identifier, expected in roots.items():
            artifact_row = await ledger.artifact(db, owner, identifier)
            source_run = await db.get(ResearchRun, artifact_row.run_id, populate_existing=True)
            provenance = parse_document(artifact_row.provenance_json)
            source_attempt = await db.scalar(
                select(ResearchAttempt).where(
                    ResearchAttempt.run_id == artifact_row.run_id,
                    ResearchAttempt.number == provenance.get("attempt_number", 1),
                ),
                execution_options={"populate_existing": True},
            )
            if source_run is None or source_attempt is None:
                raise ValueError("Artifact attempt missing.")
            snapshot = await ledger._snapshot_for_attempt(db, owner, source_run, source_attempt)
            registered_keys = {
                "run_id",
                "domain_kind",
                "domain_run_id",
                "logical_hash",
                "output_sha256",
            }
            if set(expected) == registered_keys:
                # Historic execute writer omitted a redundant raw SHA. Require
                # the full immutable publication proof, never an adopt fallback.
                from app.models.research_execution import ResearchPublicationReceipt

                publication_receipt = await db.get(
                    ResearchPublicationReceipt, source_attempt.id, populate_existing=True
                )
                if (
                    parse_document(source_run.input_json)["parameters"].get("operation")
                    != "execute"
                    or publication_receipt is None
                    or publication_receipt.root_id != identifier
                ):
                    raise ValueError("Artifact publication receipt missing.")
                registered_sha = hashlib.sha256(canonical_bytes(snapshot.document)).hexdigest()
            elif set(expected) == registered_keys | {"sha256"}:
                registered_sha = expected["sha256"]
            else:
                raise ValueError("Artifact event fields mismatch.")
            if (
                str(artifact_row.run_id) != expected["run_id"]
                or artifact_row.sha256 != registered_sha
                or artifact_row.logical_hash != expected["logical_hash"]
                or provenance["domain_kind"] != expected["domain_kind"]
                or provenance["domain_run_id"] != expected["domain_run_id"]
            ):
                raise ValueError("Artifact mismatch.")
            edges = (
                await db.scalars(
                    select(ResearchArtifactEdge).where(
                        ResearchArtifactEdge.parent_id == identifier,
                    ),
                    execution_options={"populate_existing": True},
                )
            ).all()
            children = []
            child_keys = []
            for edge in edges:
                if edge.relation != "saved_output":
                    raise ValueError("Unexpected artifact edge.")
                child = await ledger.artifact(db, owner, edge.child_id)
                children.append(child.sha256)
                child_keys.append(child.object_key)
                reachable.add(child.id)
            if sorted(children) != sorted(expected["output_sha256"]):
                raise ValueError("Artifact relationship mismatch.")
            if sorted(child_keys) != sorted(output.object_key for output in snapshot.outputs):
                raise ValueError("Artifact source edge mismatch.")
        if any(row.id not in reachable for row in owned_artifacts):
            raise ValueError("Unrecorded Artifact.")
        local_ids = [row.id for row in owned_artifacts]
        incident_edges = (
            await db.scalars(
                select(ResearchArtifactEdge)
                .where(
                    ResearchArtifactEdge.parent_id.in_(local_ids)
                    | ResearchArtifactEdge.child_id.in_(local_ids),
                )
                .limit(10001),
                execution_options={"populate_existing": True},
            )
        ).all()
        if len(incident_edges) > 10000:
            raise ValueError("Incident graph verification limit.")
        outside_allowed: dict[uuid.UUID, set[uuid.UUID]] = {}
        for edge in incident_edges:
            if edge.parent_id in roots:
                continue
            if edge.parent_id in local_ids or edge.relation != "saved_output":
                raise ValueError("Unrecorded parent edge.")
            if edge.parent_id not in outside_allowed:
                external = await ledger.artifact(db, owner, edge.parent_id)
                if external.kind != "domain_result":
                    raise ValueError("Incoming parent is not a domain root.")
                _, external_scope = await ledger.run(db, external.run_id, owner)
                if external_scope.event_sequence > 10000:
                    raise ValueError("External event verification limit.")
                await ledger.verify_events(db, owner, external_scope.id)
                registrations = (
                    await db.scalars(
                        select(ResearchEvent).where(
                            ResearchEvent.workspace_id == external_scope.id,
                            ResearchEvent.entity_id == external.id,
                            ResearchEvent.event_type == "artifact.registered",
                        ),
                        execution_options={"populate_existing": True},
                    )
                ).all()
                if len(registrations) != 1:
                    raise ValueError("Incoming parent has no unique registration.")
                registration = parse_document(registrations[0].payload_json)
                provenance = parse_document(external.provenance_json)
                if (
                    registration["run_id"] != str(external.run_id)
                    or registration["sha256"] != external.sha256
                    or registration["logical_hash"] != external.logical_hash
                    or registration["domain_kind"] != provenance["domain_kind"]
                    or registration["domain_run_id"] != provenance["domain_run_id"]
                ):
                    raise ValueError("Incoming registration mismatch.")
                snapshot = await load_snapshot(
                    db, owner, provenance["domain_kind"], uuid.UUID(provenance["domain_run_id"])
                )
                if sorted(registration["output_sha256"]) != sorted(
                    output.sha256 for output in snapshot.outputs
                ):
                    raise ValueError("Incoming registration output mismatch.")
                expected_children = (
                    await db.scalars(
                        select(ResearchArtifact).where(
                            ResearchArtifact.object_key.in_(
                                [output.object_key for output in snapshot.outputs]
                            ),
                        ),
                        execution_options={"populate_existing": True},
                    )
                ).all()
                if {node.object_key for node in expected_children} != {
                    output.object_key for output in snapshot.outputs
                }:
                    raise ValueError("Incoming root output set incomplete.")
                outside_allowed[external.id] = {node.id for node in expected_children}
            if edge.child_id not in outside_allowed[edge.parent_id]:
                raise ValueError("Unexpected incoming child edge.")
        from app.services.research_security_service import verify_scope

        await verify_scope(db, owner, scope, security_events)
        await harness_replay.verify(db, owner, runs, harness_events)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ledger.error("BUSINESS_EVENT_INCONSISTENT") from None
    return {
        "verified": True,
        "event_count": count,
        "runs": len(runs),
        "attempts": sum(len(row["attempts"]) for row in runs.values()),
        "artifacts": len(reachable),
        "sessions": len(sessions),
    }
