"""Workflow routes: catalog, CRUD, graph validation, execution, run history."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.ml.resources import MLResourceError
from app.ml.worker_control import MLTaskScope
from app.models.dataset import Dataset
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun
from app.schemas.workflow import (
    GraphIssue,
    GraphValidationResponse,
    NodeResult,
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowRunResponse,
    WorkflowRunsResponse,
    WorkflowRunSummary,
    WorkflowUpdateRequest,
)
from app.services import workflow_service
from app.workflow import output_cleanup
from app.workflow.catalog import catalog_payload
from app.workflow.publication import publish_workflow_transaction, rollback_preserving_error
from app.workflow.storage import verified_output

logger = logging.getLogger("statforge.workflow")
router = APIRouter(tags=["workflow"])


def _loads(text: str | None, default: Any) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _workflow_to_response(
    wf: Workflow, runs: list[WorkflowRun] | None = None, *, include_history: bool = True
) -> WorkflowResponse:
    runs = runs or []
    latest = runs[0] if runs else None
    graph = _loads(wf.graph_json, None)
    return WorkflowResponse(
        id=wf.id,
        project_id=wf.project_id,
        dataset_id=wf.dataset_id,
        name=wf.name,
        version=wf.version,
        status=wf.status.value,
        graph=graph,
        created_by=wf.created_by,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        run_count=len(runs) if include_history else None,
        latest_run_status=latest.status.value if latest else None,
    )


def _run_to_response(run: WorkflowRun) -> WorkflowRunResponse:
    raw = _loads(run.node_results_json, {}) or {}
    for result in raw.values():
        for artifact in (result.get("output") or {}).get("artifacts", []):
            artifact.pop("object_key", None)
    node_results = {k: NodeResult(**v) for k, v in raw.items()}
    snapshot = _loads(run.graph_snapshot_json, None)
    provenance = (snapshot or {}).get("_provenance", {})
    return WorkflowRunResponse(
        id=run.id,
        workflow_id=run.workflow_id,
        dataset_id=run.dataset_id,
        graph_snapshot=snapshot,
        workflow_version=provenance.get("workflow_version"),
        sources=provenance.get("sources", []),
        software_versions={"workflow_engine": provenance["workflow_engine"]}
        if provenance.get("workflow_engine")
        else {},
        status=run.status,
        node_results=node_results,
        order=_loads(run.order_json, []),
        total_seconds=run.total_seconds,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
        created_by=run.created_by,
        created_at=run.created_at,
    )


@router.get("/api/v1/workflow/runs/{run_id}/artifacts/{artifact_id}", response_model=None)
async def get_workflow_artifact(
    run_id: uuid.UUID,
    artifact_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    run = await workflow_service.load_run(db, run_id, current_user)
    workflow = await workflow_service.load_workflow(db, run.workflow_id, current_user)
    refs = [
        artifact
        for result in _loads(run.node_results_json, {}).values()
        for artifact in (result.get("output") or {}).get("artifacts", [])
        if artifact.get("id") == artifact_id
    ]
    if len(refs) != 1:
        raise StatForgeError(
            404, "WORKFLOW_ARTIFACT_NOT_FOUND", "Output not found in this saved run."
        )
    ref = refs[0]
    key = ref.get("object_key", "")
    prefix = f"workflow-artifacts/{workflow.project_id}/{run.id}/{artifact_id}/"
    if (
        len(artifact_id) != 32
        or any(character not in "0123456789abcdef" for character in artifact_id)
        or not key.startswith(prefix)
        or ".." in key
    ):
        raise StatForgeError(404, "WORKFLOW_ARTIFACT_NOT_FOUND", "Output reference is invalid.")
    try:
        content = await verified_output(key, ref["bytes"], ref["sha256"])
    except (ValueError, OSError) as exc:
        raise StatForgeError(
            409, "WORKFLOW_ARTIFACT_CHANGED", "Saved output is unavailable or its SHA changed."
        ) from exc
    await workflow_service.check_snapshot_sources(db, run.id, workflow.project_id, current_user)
    await workflow_service.check_dataset_access(
        db, workflow.project_id, run.dataset_id, current_user
    )
    return Response(
        content=content,
        media_type=ref["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{ref["filename"]}"',
            "ETag": f'"{ref["sha256"]}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/v1/workflow/catalog")
async def get_catalog(current_user: User = Depends(get_current_user)) -> dict:
    """Node catalog (types, categories, ports) for the canvas palette."""
    return catalog_payload()


@router.post(
    "/api/v1/projects/{project_id}/workflows",
    response_model=WorkflowResponse,
    status_code=201,
)
async def create_workflow(
    project_id: uuid.UUID,
    body: WorkflowCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> WorkflowResponse:
    graph = body.graph.model_dump(by_alias=True)
    workflow = await workflow_service.create_workflow(
        db, project_id, current_user, body.name, body.dataset_id, graph
    )
    await db.refresh(workflow, attribute_names=["created_at", "updated_at"])
    return _workflow_to_response(workflow)


@router.get("/api/v1/projects/{project_id}/workflows", response_model=list[WorkflowResponse])
async def list_workflows(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[WorkflowResponse]:
    workflows = await workflow_service.list_workflows(db, project_id, current_user)
    return [
        _workflow_to_response(w, await workflow_service.load_runs(db, w, current_user))
        for w in workflows
    ]


@router.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> WorkflowResponse:
    workflow = await workflow_service.load_workflow(db, workflow_id, current_user)
    runs = await workflow_service.load_runs(db, workflow, current_user)
    return _workflow_to_response(workflow, runs)


@router.patch("/api/v1/workflows/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> WorkflowResponse:
    unbind = "dataset_id" in body.model_fields_set and body.dataset_id is None
    workflow = await workflow_service.load_workflow(
        db, workflow_id, current_user, check_binding=not unbind
    )
    if not unbind:
        await workflow_service.authorized_run_ids(db, workflow, current_user)
    graph = body.graph.model_dump(by_alias=True) if body.graph is not None else None
    await workflow_service.update_workflow(
        db,
        workflow,
        current_user,
        body.name,
        body.dataset_id,
        graph,
        body.expected_version,
        dataset_id_set="dataset_id" in body.model_fields_set,
    )
    await db.refresh(workflow, attribute_names=["updated_at"])
    runs = [] if unbind else await workflow_service.load_runs(db, workflow, current_user)
    return _workflow_to_response(workflow, runs, include_history=not unbind)


@router.delete("/api/v1/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    try:
        async with MLTaskScope(cancel_check=request.is_disconnected) as task:
            workflow = await workflow_service.load_workflow(
                db, workflow_id, current_user, check_binding=False
            )
            receipt = await workflow_service.delete_workflow(db, workflow, current_user)
            receipt_id = receipt.id
            initial = await output_cleanup.summary(db, receipt)
            await publish_workflow_transaction(db, task)
            try:
                return await output_cleanup.cleanup(db, receipt_id, current_user, task)
            except (MLResourceError, asyncio.CancelledError):
                # The root DELETE already succeeded. Late cancellation only
                # defers file cleanup; it cannot change that business outcome.
                await rollback_preserving_error(db)
                return initial
    except BaseException as exc:
        await rollback_preserving_error(db)
        if isinstance(exc, MLResourceError):
            raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc
        raise


@router.get("/api/v1/projects/{project_id}/workflow-output-deletions")
async def pending_workflow_deletions(
    project_id: uuid.UUID,
    offset: int = Query(default=0, ge=0, le=1_000_000),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    return await output_cleanup.list_pending(db, project_id, current_user, offset=offset)


@router.post("/api/v1/workflow-output-deletions/{receipt_id}/cleanup")
async def retry_workflow_deletion(
    receipt_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    try:
        async with MLTaskScope(cancel_check=request.is_disconnected) as task:
            return await output_cleanup.cleanup(db, receipt_id, current_user, task)
    except BaseException as exc:
        await rollback_preserving_error(db)
        if isinstance(exc, MLResourceError):
            raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc
        raise


@router.post("/api/v1/workflows/{workflow_id}/validate", response_model=GraphValidationResponse)
async def validate_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> GraphValidationResponse:
    workflow = await workflow_service.load_workflow(db, workflow_id, current_user)
    graph = _loads(workflow.graph_json, {"nodes": [], "edges": []})
    result = await workflow_service.validate_bound_graph(db, workflow, current_user, graph)
    return GraphValidationResponse(
        valid=result["valid"],
        issues=[GraphIssue(**i) for i in result["issues"]],
        topo_order=result["topo_order"],
    )


@router.post(
    "/api/v1/workflows/{workflow_id}/run", response_model=WorkflowRunResponse, status_code=201
)
async def run_workflow(
    workflow_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> WorkflowRunResponse:
    try:
        async with MLTaskScope(cancel_check=request.is_disconnected) as task:
            workflow = await task.wait(
                workflow_service.load_workflow(db, workflow_id, current_user)
            )
            run = await workflow_service.run_workflow(db, workflow, current_user, task=task)
            response = _run_to_response(run)
            await publish_workflow_transaction(db, task)
            return response
    except BaseException as exc:
        await rollback_preserving_error(db)
        if isinstance(exc, MLResourceError):
            raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc
        raise


@router.get("/api/v1/workflows/{workflow_id}/runs", response_model=WorkflowRunsResponse)
async def list_runs(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> WorkflowRunsResponse:
    workflow = await workflow_service.load_workflow(db, workflow_id, current_user)
    runs = await workflow_service.load_runs(db, workflow, current_user)
    return WorkflowRunsResponse(
        runs=[
            WorkflowRunSummary(
                id=r.id,
                workflow_id=r.workflow_id,
                status=r.status,
                total_seconds=r.total_seconds,
                error_message=r.error_message,
                created_at=r.created_at,
            )
            for r in runs
        ],
        total=len(runs),
    )


@router.get("/api/v1/workflows/{workflow_id}/live-executions")
async def live_executions(
    workflow_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Current-owner observations only; uncommitted execution is not a checkpoint."""
    from app.workflow.live_execution import snapshot

    workflow = await workflow_service.load_workflow(db, workflow_id, current_user)
    response.headers["Cache-Control"] = "no-store"
    dataset = (
        (
            await db.execute(
                select(Dataset.id, Dataset.sha256).where(Dataset.id == workflow.dataset_id)
            )
        ).one_or_none()
        if workflow.dataset_id
        else None
    )
    entries = (
        snapshot(
            str(workflow.id),
            str(current_user.id),
            str(workflow.project_id),
            str(dataset.id),
            dataset.sha256,
        )
        if dataset is not None
        else []
    )
    return {
        "scope": "live_execution_observation",
        "durable": False,
        "business_checkpoint": False,
        "executions": entries,
    }


@router.get("/api/v1/workflow-runs/{run_id}", response_model=WorkflowRunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> WorkflowRunResponse:
    run = await workflow_service.load_run(db, run_id, current_user)
    return _run_to_response(run)
