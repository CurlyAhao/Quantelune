"""Authenticated formal ledger endpoints; execution remains a business-worker concern."""

import hashlib
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.research import (
    ResearchArtifact,
    ResearchAttachment,
    ResearchAttempt,
    ResearchEvent,
    ResearchMessage,
)
from app.models.user import User
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import State
from app.research.plan_contracts import DraftSave, PlanAccept, RuleProposalRequest
from app.research.plan_v2_contracts import DraftSaveV2
from app.research.protocol_contracts import ResearchProtocolDocument
from app.research.protocol_requests import ProtocolActivate, ProtocolSave
from app.research.reader_contracts import ReaderControlRequest, ReaderRunCreate
from app.research.replay import verify_business
from app.research.result_contexts import ResultExplanationBody
from app.research.reviewer_contracts import ReviewerControlRequest, ReviewerRunCreate
from app.schemas.research import (
    ArtifactResponse,
    AttachmentCreate,
    AttachmentResponse,
    AttemptResponse,
    CancelRun,
    DomainResultCreate,
    EventResponse,
    LifecycleChange,
    MessageCreate,
    MessageResponse,
    PlannerRunCreate,
    PlannerRunCreateV2,
    PlanningControlRequest,
    RetryRun,
    RunCreate,
    RunResponse,
    SessionCreate,
    SessionResponse,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceSave,
)
from app.services import research_lifecycle_service as lifecycle
from app.services import research_service as ledger
from app.services import storage_service
from app.services.research_gateway_service import Gateway, get_gateway

router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.post("/sessions/{session_id}/result-explanations")
async def explain_attached_results(
    session_id: uuid.UUID,
    body: ResultExplanationBody,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import result_contexts

    values = await result_contexts.read(
        db,
        user.id,
        session_id,
        body.selection,
        body.source_version,
        artifact_only=not body.selection.datasets,
    )
    return await result_contexts.explain(db, user, body.request, values)


@router.post("/sessions/{session_id}/plans/acceptances", status_code=201)
async def accept_plan(
    session_id: uuid.UUID,
    body: PlanAccept,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import plan_acceptance
    from app.research.contracts import parse_document

    row = await plan_acceptance.create(
        db,
        user.id,
        session_id,
        body.version_id,
        body.plan_hash,
        body.source_version,
        body.idempotency_key,
    )
    return parse_document(row.document_json)


@router.get("/sessions/{session_id}/plans/acceptances/{acceptance_id}")
async def get_plan_acceptance(
    session_id: uuid.UUID,
    acceptance_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.models.research_planning import ResearchPlanAcceptance
    from app.research.contracts import parse_document

    _, scope = await ledger.session(db, session_id, user.id)
    await verify_business(db, user.id, scope.id)
    row = await db.get(ResearchPlanAcceptance, acceptance_id, populate_existing=True)
    if row is None or row.owner_id != user.id or row.session_id != session_id:
        raise ledger.error("PLAN_ACCEPTANCE_UNAVAILABLE", 404)
    return parse_document(row.document_json)


@router.get("/sessions/{session_id}/plans/acceptances/{acceptance_id}/execution")
async def get_plan_execution(
    session_id: uuid.UUID,
    acceptance_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import plan_execution

    row, _, plan, steps = await plan_execution.load(db, user.id, acceptance_id)
    if row.session_id != session_id:
        raise ledger.error("PLAN_ACCEPTANCE_UNAVAILABLE", 404)
    return await plan_execution.status(db, user.id, row, plan, steps)


@router.get("/sessions/{session_id}/plans/acceptances/{acceptance_id}/steps/{step_id}/result-facts")
async def get_p4_result_facts(
    session_id: uuid.UUID,
    acceptance_id: uuid.UUID,
    step_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research.p4_result_reader import read

    return await read(db, user.id, session_id, acceptance_id, step_id)


@router.get("/sessions/{session_id}/plans/acceptances/{acceptance_id}/report")
async def get_p4_saved_report(
    session_id: uuid.UUID,
    acceptance_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import p4_report

    saved = await p4_report.document(db, user.id, session_id, acceptance_id)
    return Response(
        content=p4_report.bundle(saved),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="research-report-{acceptance_id}.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/sessions/{session_id}/plans/acceptances/{acceptance_id}/cancel")
async def cancel_plan_execution(
    session_id: uuid.UUID,
    acceptance_id: uuid.UUID,
    body: CancelRun,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import plan_execution

    row, _, _, _ = await plan_execution.load(db, user.id, acceptance_id)
    if row.session_id != session_id:
        raise ledger.error("PLAN_ACCEPTANCE_UNAVAILABLE", 404)
    return await plan_execution.cancel(db, user.id, acceptance_id, body.expected_revision)


@router.post("/sessions/{session_id}/planner-runs", response_model=RunResponse, status_code=202)
async def create_planner_run(
    session_id: uuid.UUID,
    body: PlannerRunCreate | PlannerRunCreateV2,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    from app.research import planner_admission

    return await planner_admission.create(
        db,
        user.id,
        session_id,
        body.idempotency_key,
        body.source_version,
        body.selection,
        gateway,
        protocol_request=body if isinstance(body, PlannerRunCreateV2) else None,
    )


@router.post("/sessions/{session_id}/reader-runs", response_model=RunResponse, status_code=202)
async def create_reader_run(
    session_id: uuid.UUID,
    body: ReaderRunCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    from app.research import reader_admission

    return await reader_admission.create(db, user.id, session_id, body, gateway)


@router.post("/runs/{run_id}/reader/control")
async def control_reader(
    run_id: uuid.UUID,
    body: ReaderControlRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import reader_control

    return await reader_control.request(
        db, user.id, run_id, body.expected_revision, body.action, body.idempotency_key
    )


@router.post("/sessions/{session_id}/reviews", response_model=RunResponse, status_code=202)
async def create_reviewer_run(
    session_id: uuid.UUID,
    body: ReviewerRunCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    from app.research import reviewer_admission

    return await reviewer_admission.create(db, user.id, session_id, body, gateway)


@router.get("/sessions/{session_id}/reviews/{run_id}")
async def get_reviewer_result(
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import reviewer_results

    return await reviewer_results.get_review(db, user.id, session_id, run_id)


@router.post("/runs/{run_id}/reviewer/control")
async def control_reviewer(
    run_id: uuid.UUID,
    body: ReviewerControlRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import reviewer_control

    return await reviewer_control.request(
        db, user.id, run_id, body.expected_revision, body.action, body.idempotency_key
    )


@router.post("/runs/{run_id}/planning/control")
async def control_planning(
    run_id: uuid.UUID,
    body: PlanningControlRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import planning_control

    return await planning_control.request(
        db, user.id, run_id, body.expected_revision, body.action, body.idempotency_key
    )


@router.get("/runs/{run_id}/planning/outcome")
async def planning_outcome(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research import planner_unresolved

    return await planner_unresolved.read(db, user.id, run_id)


@router.get("/sessions/{session_id}/context", response_model=ContextSnapshot)
async def session_context(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import research_context_service

    return await research_context_service.snapshot(db, user.id, session_id)


@router.get("/capabilities")
async def research_capabilities(user: User = Depends(get_current_user)):
    from app.research.plan_validation import capabilities

    return [item.model_dump(mode="json") for item in capabilities()]


@router.post("/sessions/{session_id}/protocols/assessment")
async def assess_research_protocol(
    session_id: uuid.UUID,
    body: ResearchProtocolDocument,
    method_id: str | None = Query(default=None, max_length=255),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research.protocol_validation import assess_protocol
    from app.services import research_context_service

    context = await research_context_service.snapshot(
        db, user.id, session_id, expected_source_version=body.source_version
    )
    await verify_business(db, user.id, context.workspace_id)
    assessment = assess_protocol(body, context, method_id=method_id)
    if method_id is not None:
        from app.research.p4_method_capabilities import live_assessment

        assessment = live_assessment(assessment, body, method_id)
    return assessment


@router.post("/sessions/{session_id}/protocols/versions")
async def save_research_protocol(
    session_id: uuid.UUID,
    body: ProtocolSave,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import research_protocol_service

    return await research_protocol_service.save(
        db, user.id, session_id, body.protocol, body.expected_revision, body.idempotency_key
    )


@router.post("/sessions/{session_id}/protocols/activations")
async def activate_research_protocol(
    session_id: uuid.UUID,
    body: ProtocolActivate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import research_protocol_service

    return await research_protocol_service.activate(db, user.id, session_id, body)


@router.get("/sessions/{session_id}/protocols")
async def research_protocol_history(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import research_protocol_service

    return await research_protocol_service.history(db, user.id, session_id)


@router.post("/sessions/{session_id}/plans/rule-proposals")
async def propose_structured_plan(
    session_id: uuid.UUID,
    body: RuleProposalRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research.plan_validation import rule_proposal
    from app.services import research_context_service

    context = await research_context_service.snapshot(
        db, user.id, session_id, expected_source_version=body.source_version
    )
    return rule_proposal(context, body.selection)


@router.post("/sessions/{session_id}/plans/drafts")
async def create_plan_draft(
    session_id: uuid.UUID,
    body: DraftSave | DraftSaveV2,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import research_plan_service

    return await research_plan_service.save(
        db, user.id, session_id, body.plan, body.expected_revision, body.idempotency_key
    )


@router.put("/sessions/{session_id}/plans/drafts/{draft_id}")
async def save_plan_draft(
    session_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: DraftSave | DraftSaveV2,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import research_plan_service

    return await research_plan_service.save(
        db, user.id, session_id, body.plan, body.expected_revision, body.idempotency_key, draft_id
    )


@router.get("/sessions/{session_id}/plans/drafts/{draft_id}/versions")
async def plan_draft_versions(
    session_id: uuid.UUID,
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import research_plan_service

    return await research_plan_service.versions(db, user.id, session_id, draft_id)


@router.get("/workspaces/{workspace_id}/business/verify")
async def verify_business_state(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await verify_business(db, user.id, workspace_id)


@router.post("/runs/{run_id}/retry", response_model=RunResponse, status_code=202)
async def retry_run(
    run_id: uuid.UUID,
    body: RetryRun,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    from app.research import assistant_invocation, execution_lease

    await execution_lease._boundary(db, user.id)
    current, scope = await ledger.run(db, run_id, user.id)
    old_attempt = await db.scalar(
        select(ResearchAttempt)
        .where(
            ResearchAttempt.run_id == current.id, ResearchAttempt.number == current.attempt_count
        )
        .execution_options(populate_existing=True)
    )
    invocation = await assistant_invocation.load(db, user.id, current, old_attempt)
    if invocation is None:
        # A broken formal lineage must not silently fall back to native work.
        prior_formal = await db.scalar(
            select(ResearchEvent.id)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.event_type == assistant_invocation.EVENT,
                ResearchEvent.entity_id.in_(
                    select(ResearchAttempt.id).where(ResearchAttempt.run_id == current.id)
                ),
            )
            .limit(1)
        )
        if prior_formal is not None:
            raise ledger.error("INVOCATION_BINDING_CHANGED")
    else:
        await assistant_invocation.verify_current(db, user.id, current, old_attempt, invocation)
    fresh = await ledger.transition(
        db, user.id, run_id, body.expected_revision, State.QUEUED, retry_key=body.retry_key
    )
    if invocation is not None:
        await assistant_invocation.register(db, user.id, fresh.id, invocation["selection"], gateway)
    return fresh


@router.get("/runs/{run_id}/attempts", response_model=list[AttemptResponse])
async def run_attempts(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await ledger.run(db, run_id, user.id)
    return (
        await db.scalars(
            select(ResearchAttempt)
            .where(
                ResearchAttempt.run_id == run_id,
            )
            .order_by(ResearchAttempt.number)
        )
    ).all()


@router.post("/workspaces", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.create_workspace(db, user.id, body.title, body.project_id)


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await lifecycle.workspaces(db, user.id)


@router.post("/workspaces/{workspace_id}/save", response_model=WorkspaceResponse)
async def save_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceSave,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await lifecycle.save_workspace(
        db, user.id, workspace_id, body.expected_revision, body.project_id
    )


@router.delete("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def delete_workspace(
    workspace_id: uuid.UUID,
    expected_revision: int = Query(ge=0),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await lifecycle.delete_workspace(db, user.id, workspace_id, expected_revision)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.workspace(db, workspace_id, user.id)


@router.post("/workspaces/{workspace_id}/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    workspace_id: uuid.UUID,
    body: SessionCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.create_session(db, user.id, workspace_id, body.title)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    conversation, _ = await ledger.session(db, session_id, user.id)
    return conversation


@router.get("/workspaces/{workspace_id}/sessions", response_model=list[SessionResponse])
async def list_sessions(
    workspace_id: uuid.UUID,
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await lifecycle.sessions(db, user.id, workspace_id, include_archived=include_archived)


@router.post("/sessions/{session_id}/lifecycle", response_model=SessionResponse)
async def change_session_lifecycle(
    session_id: uuid.UUID,
    body: LifecycleChange,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await lifecycle.change_session(
        db, user.id, session_id, body.expected_revision, body.action
    )


@router.post(
    "/sessions/{session_id}/attachments", response_model=AttachmentResponse, status_code=201
)
async def add_attachment(
    session_id: uuid.UUID,
    body: AttachmentCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.attach(
        db,
        user.id,
        session_id,
        body.target_kind,
        body.target_id,
        allow_cross_project=body.allow_cross_project,
    )


@router.get("/sessions/{session_id}/attachments", response_model=list[AttachmentResponse])
async def list_attachments(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await ledger.bindings(db, user.id, session_id)
    return (
        await db.scalars(
            select(ResearchAttachment)
            .where(
                ResearchAttachment.session_id == session_id,
                ResearchAttachment.active.is_(True),
            )
            .order_by(ResearchAttachment.target_kind, ResearchAttachment.target_id)
        )
    ).all()


@router.delete("/attachments/{attachment_id}", status_code=204)
async def remove_attachment(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await ledger.detach(db, user.id, attachment_id)


@router.post("/sessions/{session_id}/runs", response_model=RunResponse, status_code=202)
async def queue_run(
    session_id: uuid.UUID,
    body: RunCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
):
    run = await ledger.start_run(
        db,
        user.id,
        session_id,
        body.kind,
        body.idempotency_key,
        body.parameters,
    )
    if body.assistant is not None:
        from app.research.assistant_invocation import register

        await register(db, user.id, run.id, body.assistant, gateway)
    return run


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    item, _ = await ledger.run(db, run_id, user.id)
    return item


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: uuid.UUID,
    body: CancelRun,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    item, _ = await ledger.run(db, run_id, user.id)
    from app.research.contracts import parse_document
    from app.research.execution_control import request

    control_required = {
        "planner": "PLANNING_CONTROL_REQUIRED",
        "reader": "READER_CONTROL_REQUIRED",
        "reviewer": "REVIEWER_CONTROL_REQUIRED",
    }.get(item.kind)
    if control_required is not None:
        raise ledger.error(control_required, 422)
    if parse_document(item.input_json)["parameters"].get("operation") == "execute":
        return await request(db, user.id, run_id, body.expected_revision, "cancel")
    after = State.CANCEL_REQUESTED if item.state == State.RUNNING.value else State.CANCELLED
    return await ledger.transition(db, user.id, run_id, body.expected_revision, after)


@router.post("/runs/{run_id}/pause", response_model=RunResponse)
async def pause_run(
    run_id: uuid.UUID,
    body: CancelRun,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research.execution_control import request

    item, _ = await ledger.run(db, run_id, user.id)
    if item.kind == "reviewer":
        raise ledger.error("REVIEWER_CONTROL_REQUIRED", 422)
    return await request(db, user.id, run_id, body.expected_revision, "pause")


@router.post("/runs/{run_id}/resume", response_model=RunResponse)
async def resume_run(
    run_id: uuid.UUID,
    body: CancelRun,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.research.execution_control import request

    item, _ = await ledger.run(db, run_id, user.id)
    if item.kind == "reviewer":
        raise ledger.error("REVIEWER_CONTROL_REQUIRED", 422)
    return await request(db, user.id, run_id, body.expected_revision, "resume")


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse, status_code=201)
async def add_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.add_user_message(db, user.id, session_id, body.text)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await ledger.session(db, session_id, user.id)
    return (
        await db.scalars(
            select(ResearchMessage)
            .where(
                ResearchMessage.session_id == session_id,
            )
            .order_by(ResearchMessage.created_at, ResearchMessage.id)
            .limit(limit)
        )
    ).all()


@router.get("/workspaces/{workspace_id}/events", response_model=list[EventResponse])
async def events(
    workspace_id: uuid.UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await ledger.verify_events(db, user.id, workspace_id)
    return (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == workspace_id,
                ResearchEvent.sequence > after_sequence,
            )
            .order_by(ResearchEvent.sequence)
            .limit(limit)
        )
    ).all()


@router.get("/workspaces/{workspace_id}/events/verify")
async def verify_events(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    count = await ledger.verify_events(db, user.id, workspace_id)
    return {"verified": True, "event_count": count}


@router.post("/sessions/{session_id}/domain-results", response_model=RunResponse, status_code=201)
async def adopt_domain_result(
    session_id: uuid.UUID,
    body: DomainResultCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.adopt_saved_result(
        db,
        user.id,
        session_id,
        body.domain_kind,
        body.domain_run_id,
        body.idempotency_key,
    )


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactResponse])
async def run_artifacts(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await ledger.run(db, run_id, user.id)
    items = (
        await db.scalars(select(ResearchArtifact).where(ResearchArtifact.run_id == run_id))
    ).all()
    for item in items:
        await ledger.artifact(db, user.id, item.id)
    return items


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.artifact(db, user.id, artifact_id)


@router.get("/artifacts/{artifact_id}/content")
async def artifact_content(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    item = await ledger.artifact(db, user.id, artifact_id)
    # Freeze the exact authenticated byte snapshot before sending headers.
    # A FileResponse would reopen the source after the hash verification.
    if not 0 < item.byte_size <= 128 * 1024 * 1024:
        raise ledger.error("CONTENT_LIMIT")
    try:
        with storage_service.get_local_object_path(item.object_key).open("rb") as source:
            data = source.read(item.byte_size + 1)
    except OSError:
        raise ledger.error("CONTENT_UNAVAILABLE") from None
    if len(data) != item.byte_size or hashlib.sha256(data).hexdigest() != item.sha256:
        raise ledger.error("CONTENT_CHANGED")
    return Response(
        content=data,
        media_type=item.media_type,
        headers={
            "X-Content-SHA256": item.sha256,
            "Content-Disposition": f'attachment; filename="{item.kind}-{item.id}"',
        },
    )


@router.get("/artifacts/{artifact_id}/graph")
async def artifact_graph(
    artifact_id: uuid.UUID,
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await ledger.artifact_graph(db, user.id, artifact_id, limit)
