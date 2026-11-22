"""Workflow orchestration: CRUD, graph validation, and reproducible runs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

import polars as pl
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload
from sqlalchemy.orm.attributes import set_committed_value

from app.exceptions import (
    DatasetAccessDeniedError,
    DatasetNotFoundError,
    ProfileComputationError,
    ProjectAccessDeniedError,
    ProjectNotFoundError,
    StatForgeError,
)
from app.ml.resources import MLResourceError
from app.ml.worker_control import MLTaskScope
from app.models.dataset import Dataset
from app.models.project import Project
from app.models.user import User
from app.models.workflow import (
    Workflow,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStatus,
)
from app.services import audit_service, storage_service
from app.services.profiling_service import load_dataframe
from app.workflow.catalog import KIND_FRAME, get_spec
from app.workflow.graph import validate_graph

logger = logging.getLogger("statforge.workflow")


async def check_project_access(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    """Read only authorization metadata, never eager project children."""
    with db.no_autoflush:
        row = (
            await db.execute(
                select(Project.owner_id, Project.is_deleted).where(Project.id == project_id)
            )
        ).one_or_none()
    if row is None or row.is_deleted:
        raise ProjectNotFoundError()
    if row.owner_id != user.id:
        raise ProjectAccessDeniedError()


async def check_dataset_access(
    db: AsyncSession, project_id: uuid.UUID, dataset_id: uuid.UUID | None, user: User
) -> None:
    await check_project_access(db, project_id, user)
    if dataset_id is None:
        return
    # Do not materialize Dataset: its selectin children include results and raw previews.
    with db.no_autoflush:
        actual_project = await db.scalar(select(Dataset.project_id).where(Dataset.id == dataset_id))
    if actual_project is None:
        raise DatasetNotFoundError()
    if actual_project != project_id:
        raise DatasetAccessDeniedError()


async def _check_workflow_owner(
    db: AsyncSession, workflow: Workflow, user: User, *, check_binding: bool = True
) -> None:
    """Authorize the persisted workflow identity, not caller-mutated ORM fields."""
    with db.no_autoflush:
        row = (
            await db.execute(
                select(Workflow.project_id, Workflow.dataset_id).where(Workflow.id == workflow.id)
            )
        ).one_or_none()
    if row is None:
        raise StatForgeError(404, "WORKFLOW_NOT_FOUND", "Workflow not found.")
    await check_project_access(db, row.project_id, user)
    if workflow.project_id != row.project_id:
        raise ProjectAccessDeniedError()
    if check_binding:
        await check_dataset_access(db, row.project_id, row.dataset_id, user)
        await check_dataset_access(db, row.project_id, workflow.dataset_id, user)


async def load_workflow(
    db: AsyncSession, workflow_id: uuid.UUID, user: User, *, check_binding: bool = True
) -> Workflow:
    with db.no_autoflush:
        row = (
            await db.execute(
                select(Workflow.project_id, Workflow.dataset_id).where(Workflow.id == workflow_id)
            )
        ).one_or_none()
    if row is None:
        raise StatForgeError(404, "WORKFLOW_NOT_FOUND", "Workflow not found.")
    await check_project_access(db, row.project_id, user)
    if check_binding:
        await check_dataset_access(db, row.project_id, row.dataset_id, user)
    statement = select(Workflow).options(noload("*"))
    if not check_binding:
        statement = statement.options(
            load_only(
                Workflow.id,
                Workflow.project_id,
                Workflow.dataset_id,
                Workflow.name,
                Workflow.version,
                Workflow.status,
                Workflow.created_by,
                Workflow.created_at,
                Workflow.updated_at,
            )
        )
    with db.no_autoflush:
        workflow = (await db.execute(statement.where(Workflow.id == workflow_id))).scalar_one()
    return workflow


async def check_snapshot_sources(
    db: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID, user: User
) -> None:
    # Read source metadata only after base project and actual run input ACL pass.
    with db.no_autoflush:
        snapshot_json = await db.scalar(
            select(WorkflowRun.graph_snapshot_json).where(WorkflowRun.id == run_id)
        )
    snapshot = json.loads(snapshot_json or "{}")
    sources = snapshot.get("_provenance", {}).get("sources", [])
    for source in sources:
        try:
            dataset_id = uuid.UUID(source["dataset_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise DatasetAccessDeniedError() from exc
        await check_dataset_access(db, project_id, dataset_id, user)


async def authorized_run_ids(
    db: AsyncSession, workflow: Workflow, user: User, *, max_runs: int | None = None
) -> list[uuid.UUID]:
    """Check historical actual inputs before reading any historical result payload."""
    await _check_workflow_owner(db, workflow, user, check_binding=False)
    statement = (
        select(WorkflowRun.id, WorkflowRun.dataset_id, WorkflowRun.status)
        .where(WorkflowRun.workflow_id == workflow.id)
        .order_by(WorkflowRun.created_at.desc())
    )
    if max_runs is not None:
        statement = statement.limit(max_runs + 1)
    with db.no_autoflush:
        rows = (await db.execute(statement)).all()
    if max_runs is not None and len(rows) > max_runs:
        raise StatForgeError(422, "WORKFLOW_DELETE_LIMIT", "Too much history for one deletion.")
    for row in rows:
        if row.dataset_id is None:
            raise DatasetAccessDeniedError()
        await check_dataset_access(db, workflow.project_id, row.dataset_id, user)
        await check_snapshot_sources(db, row.id, workflow.project_id, user)
    return [row.id for row in rows]


async def load_runs(db: AsyncSession, workflow: Workflow, user: User) -> list[WorkflowRun]:
    ids = await authorized_run_ids(db, workflow, user)
    if not ids:
        return []
    with db.no_autoflush:
        return list(
            (
                await db.scalars(
                    select(WorkflowRun)
                    .options(noload("*"))
                    .where(WorkflowRun.id.in_(ids))
                    .order_by(WorkflowRun.created_at.desc())
                )
            ).all()
        )


async def load_run(db: AsyncSession, run_id: uuid.UUID, user: User) -> WorkflowRun:
    with db.no_autoflush:
        row = (
            await db.execute(
                select(WorkflowRun.workflow_id, WorkflowRun.dataset_id, WorkflowRun.status).where(
                    WorkflowRun.id == run_id
                )
            )
        ).one_or_none()
    if row is None:
        raise StatForgeError(404, "WORKFLOW_RUN_NOT_FOUND", "Workflow run not found.")
    workflow = await load_workflow(db, row.workflow_id, user)
    if row.dataset_id is None:
        raise DatasetAccessDeniedError()
    await check_dataset_access(db, workflow.project_id, row.dataset_id, user)
    await check_snapshot_sources(db, run_id, workflow.project_id, user)
    with db.no_autoflush:
        return (
            await db.scalars(
                select(WorkflowRun).options(noload("*")).where(WorkflowRun.id == run_id)
            )
        ).one()


async def delete_workflow(db: AsyncSession, workflow: Workflow, user: User):
    from app.workflow.output_cleanup import (
        MAX_HISTORY,
        MAX_OBJECTS,
        MAX_SOURCES,
        record_plan,
        validated_key,
    )

    await _check_workflow_owner(db, workflow, user, check_binding=False)
    # A corrupt current binding may be removed, but FK cascade must not erase
    # historical results whose actual dataset is unauthorized. Explicit unbind
    # is the non-destructive cleanup option in that case.
    ids = await authorized_run_ids(db, workflow, user, max_runs=MAX_HISTORY)
    objects = []
    source_ids = set()
    for run_id in ids:
        # Payloads only after all historical actual inputs were authorized.
        run = (
            await db.scalars(
                select(WorkflowRun).options(noload("*")).where(WorkflowRun.id == run_id)
            )
        ).one()
        if run.dataset_id:
            source_ids.add(str(run.dataset_id))
        for source in (
            json.loads(run.graph_snapshot_json or "{}").get("_provenance", {}).get("sources", [])
        ):
            source_ids.add(source["dataset_id"])
        if len(source_ids) > MAX_SOURCES:
            raise StatForgeError(422, "WORKFLOW_DELETE_LIMIT", "Too many sources for one deletion.")
        for node in json.loads(run.node_results_json or "{}").values():
            for artifact in (node.get("output") or {}).get("artifacts", []):
                key = artifact.get("object_key", "")
                if not validated_key(key, workflow.project_id, {str(run_id)}):
                    raise StatForgeError(
                        409, "WORKFLOW_CLEANUP_INVALID", "Saved output scope is invalid."
                    )
                objects.append({"key": key, "run_id": str(run_id)})
                if len(objects) > MAX_OBJECTS:
                    raise StatForgeError(
                        422, "WORKFLOW_DELETE_LIMIT", "Too many outputs for one deletion."
                    )
    receipt = await record_plan(
        db, workflow.project_id, workflow.id, user, objects, sorted(source_ids), workflow.name
    )
    await db.execute(delete(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id))
    await db.execute(delete(WorkflowNode).where(WorkflowNode.workflow_id == workflow.id))
    await db.execute(delete(Workflow).where(Workflow.id == workflow.id))
    await db.flush()
    return receipt


def _graph_dict(graph: Any) -> dict:
    if graph is None:
        return {"nodes": [], "edges": []}
    if hasattr(graph, "model_dump"):
        return graph.model_dump()
    return graph


def _download_frame(
    object_key: str, file_format: str, expected_sha256: str | None = None
) -> pl.DataFrame:
    fd, tmp_path = tempfile.mkstemp(suffix=f".{file_format}", prefix="sf_wf_")
    os.close(fd)
    try:
        storage_service.download_to_path(object_key, tmp_path)
        digest = hashlib.sha256()
        with open(tmp_path, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
            raise ValueError("WORKFLOW_INPUT_CHANGED")
        return load_dataframe(tmp_path, file_format)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def create_workflow(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    name: str,
    dataset_id: uuid.UUID | None,
    graph: dict,
) -> Workflow:
    await check_dataset_access(db, project_id, dataset_id, user)
    workflow = Workflow(
        project_id=project_id,
        dataset_id=dataset_id,
        name=name,
        version=1,
        status=WorkflowStatus.DRAFT,
        graph_json=json.dumps(graph, ensure_ascii=False),
        created_by=user.email,
    )
    db.add(workflow)
    await db.flush()
    await audit_service.record(
        db,
        event_type="workflow.created",
        entity_type="workflow",
        entity_id=str(workflow.id),
        actor=user,
        project_id=project_id,
        after={"name": name},
    )
    return workflow


async def update_workflow(
    db: AsyncSession,
    workflow: Workflow,
    user: User,
    name: str | None,
    dataset_id: uuid.UUID | None,
    graph: dict | None,
    expected_version: int | None,
    *,
    dataset_id_set: bool = False,
) -> Workflow:
    unbind = dataset_id_set and dataset_id is None
    await _check_workflow_owner(db, workflow, user, check_binding=not unbind)
    if not unbind:
        await check_dataset_access(db, workflow.project_id, workflow.dataset_id, user)
        await check_dataset_access(db, workflow.project_id, dataset_id, user)
        await authorized_run_ids(db, workflow, user)
    if expected_version is not None and expected_version != workflow.version:
        from app.exceptions import StatForgeError

        raise StatForgeError(
            409,
            "WORKFLOW_VERSION_CONFLICT",
            f"Workflow changed (expected v{expected_version}, now v{workflow.version}). "
            "Reload and retry.",
        )
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if dataset_id_set or dataset_id is not None:
        changes["dataset_id"] = dataset_id
    if unbind:
        graph = {"schema_version": 2, "nodes": [], "edges": []}
    if graph is not None:
        changes["graph_json"] = json.dumps(graph, ensure_ascii=False)
        validation = validate_graph(graph)
        changes["status"] = WorkflowStatus.READY if validation["valid"] else WorkflowStatus.DRAFT
    if changes:
        original_version = workflow.version
        changes["version"] = original_version + 1
        with db.no_autoflush:
            result = await db.execute(
                update(Workflow)
                .where(Workflow.id == workflow.id, Workflow.version == original_version)
                .values(**changes)
                .returning(Workflow.id)
                .execution_options(synchronize_session=False)
            )
        if result.scalar_one_or_none() is None:
            raise StatForgeError(
                409, "WORKFLOW_VERSION_CONFLICT", "Workflow changed; reload before saving."
            )
        # These are the successfully written values, without reading the old
        # graph or historical result payload during corrupt-binding cleanup.
        for field, value in changes.items():
            set_committed_value(workflow, field, value)
        await db.refresh(workflow, attribute_names=["updated_at"])
    await db.flush()
    await audit_service.record(
        db,
        event_type="workflow.saved",
        entity_type="workflow",
        entity_id=str(workflow.id),
        actor=user,
        project_id=workflow.project_id,
        after={"version": workflow.version},
    )
    return workflow


async def list_workflows(db: AsyncSession, project_id: uuid.UUID, user: User) -> list[Workflow]:
    await check_project_access(db, project_id, user)
    with db.no_autoflush:
        res = await db.execute(
            select(Workflow.id)
            .where(Workflow.project_id == project_id)
            .order_by(Workflow.created_at.desc())
        )
    safe = []
    for workflow_id in res.scalars().all():
        try:
            workflow = await load_workflow(db, workflow_id, user)
            await authorized_run_ids(db, workflow, user)
        except (DatasetAccessDeniedError, DatasetNotFoundError):
            continue
        safe.append(workflow)
    return safe


async def validate_bound_graph(
    db: AsyncSession, workflow: Workflow, user: User, graph: dict
) -> dict:
    """Authorize all declared sources before payload reads or any node effects."""
    initial = validate_graph(graph)
    if not initial["valid"]:
        return initial
    nodes = {node["id"]: node for node in graph["nodes"]}
    output_columns: dict[str, list[str]] = {}
    input_columns: dict[str, list[str]] = {}
    for nid in initial["topo_order"]:
        node = nodes[nid]
        data = node.get("data", {})
        if node["type"] == "dataset":
            try:
                dataset_id = (
                    uuid.UUID(data["dataset_id"]) if data.get("dataset_id") else workflow.dataset_id
                )
            except (ValueError, TypeError):
                return {
                    **initial,
                    "valid": False,
                    "issues": [
                        {
                            "level": "error",
                            "code": "DATASET_ID_INVALID",
                            "node_id": nid,
                            "message": "Select a valid project dataset.",
                        }
                    ],
                }
            if dataset_id is None:
                output_columns[nid] = []
            else:
                await check_dataset_access(db, workflow.project_id, dataset_id, user)
                schema = await db.scalar(
                    select(Dataset.schema_json).where(Dataset.id == dataset_id)
                )
                output_columns[nid] = [column["name"] for column in json.loads(schema or "[]")]
            input_columns[nid] = output_columns[nid]
        else:
            predecessors = [edge["source"] for edge in graph["edges"] if edge["target"] == nid]
            flowing = next((source for source in predecessors if source in output_columns), None)
            input_columns[nid] = output_columns.get(flowing, []) if flowing else []
            spec = get_spec(node["type"])
            if spec is not None and KIND_FRAME in spec.outputs:
                output_columns[nid] = input_columns[nid]
                if node["type"] == "column_select" and isinstance(data.get("columns"), list):
                    output_columns[nid] = data["columns"]
    return validate_graph(graph, input_columns)


async def run_workflow(
    db: AsyncSession, workflow: Workflow, user: User, *, task: MLTaskScope | None = None
) -> WorkflowRun:
    """Execute the workflow's graph against its dataset and persist the run."""
    if task is None:
        async with MLTaskScope() as admitted:
            return await run_workflow(db, workflow, user, task=admitted)
    await task.check()
    await _check_workflow_owner(db, workflow, user)
    await check_dataset_access(db, workflow.project_id, workflow.dataset_id, user)
    graph = json.loads(workflow.graph_json) if workflow.graph_json else {"nodes": [], "edges": []}
    validation = await validate_bound_graph(db, workflow, user, graph)
    run = WorkflowRun(
        workflow_id=workflow.id,
        dataset_id=workflow.dataset_id,
        status=WorkflowRunStatus.RUNNING,
        graph_snapshot_json=json.dumps(
            {
                **graph,
                "_provenance": {
                    "workflow_version": workflow.version,
                    "workflow_engine": "2.0.0",
                    "sources": [],
                },
            },
            ensure_ascii=False,
        ),
        started_at=datetime.now(UTC),
        created_by=user.email,
    )
    db.add(run)
    await db.flush()

    if not validation["valid"]:
        run.status = WorkflowRunStatus.FAILED
        run.completed_at = datetime.now(UTC)
        run.error_message = "Graph validation failed: " + "; ".join(
            i["message"] for i in validation["issues"] if i["level"] == "error"
        )
        run.node_results_json = json.dumps({}, ensure_ascii=False)
        await db.flush()
        await db.refresh(run)
        return run

    if not workflow.dataset_id:
        run.status = WorkflowRunStatus.FAILED
        run.completed_at = datetime.now(UTC)
        run.error_message = "Workflow has no dataset to run against."
        run.node_results_json = json.dumps({}, ensure_ascii=False)
        await db.flush()
        await db.refresh(run)
        return run

    dataset = (
        await db.scalars(
            select(Dataset).options(noload("*")).where(Dataset.id == workflow.dataset_id)
        )
    ).one_or_none()
    if dataset is None:
        raise DatasetNotFoundError()

    try:
        from app.workflow.live_execution import observe_execution
        from app.workflow.runtime import WorkflowRuntime, execute_domain_graph

        runtime = WorkflowRuntime(db, workflow, run, user, task)
        with observe_execution(
            run_id=str(run.id),
            workflow_id=str(workflow.id),
            workflow_version=workflow.version,
            project_id=str(workflow.project_id),
            owner_id=str(user.id),
            dataset_id=str(dataset.id),
            dataset_sha256=dataset.sha256,
        ):
            result = await execute_domain_graph(graph, runtime)
        snapshot = {
            **graph,
            "_provenance": {
                "workflow_version": workflow.version,
                "workflow_engine": "2.0.0",
                "sources": result.get("sources", []),
            },
        }
        run.graph_snapshot_json = json.dumps(snapshot, ensure_ascii=False)
    except (StatForgeError, MLResourceError):
        raise
    except Exception as exc:  # noqa: BLE001
        run.status = WorkflowRunStatus.FAILED
        run.completed_at = datetime.now(UTC)
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        await db.flush()
        await audit_service.record(
            db,
            event_type="workflow_run.failed",
            entity_type="workflow",
            entity_id=str(workflow.id),
            actor=user,
            project_id=workflow.project_id,
            after={"error": run.error_message},
        )
        logger.exception("Workflow run failed: %s", workflow.id)
        raise ProfileComputationError() from exc

    await task.wait(_check_workflow_owner(db, workflow, user))
    for source in result.get("sources", []):
        await task.wait(
            check_dataset_access(db, workflow.project_id, uuid.UUID(source["dataset_id"]), user)
        )
    await task.check()
    run.completed_at = datetime.now(UTC)
    run.status = (
        WorkflowRunStatus.COMPLETED if result["status"] == "completed" else WorkflowRunStatus.FAILED
    )
    run.node_results_json = json.dumps(result.get("node_results", {}), ensure_ascii=False)
    run.order_json = json.dumps(result.get("order", []), ensure_ascii=False)
    run.total_seconds = result.get("total_seconds")
    if result["status"] != "completed":
        failed = [
            nr.get("error")
            for nr in result.get("node_results", {}).values()
            if nr.get("status") == "failed"
        ]
        run.error_message = "; ".join(f for f in failed if f)[:2000]

    # A completed historical snapshot must not mark a newer edited graph done.
    await db.execute(
        update(Workflow)
        .where(Workflow.id == workflow.id, Workflow.version == workflow.version)
        .values(
            status=WorkflowStatus.COMPLETED
            if run.status == WorkflowRunStatus.COMPLETED
            else WorkflowStatus.FAILED
        )
        .execution_options(synchronize_session=False)
    )
    await audit_service.record(
        db,
        event_type="workflow_run.completed",
        entity_type="workflow",
        entity_id=str(workflow.id),
        actor=user,
        project_id=workflow.project_id,
        after={"run_id": str(run.id), "status": run.status.value},
    )
    await db.flush()
    await db.refresh(run)
    logger.info("Workflow run complete: %s status=%s", workflow.id, run.status.value)
    return run
