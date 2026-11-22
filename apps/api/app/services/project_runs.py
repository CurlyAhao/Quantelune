"""Project run index. Stored history is never presented as a live execution."""

import uuid

from app.models.dataset import Dataset
from app.models.workspace import WorkspaceRecord
from app.schemas.analysis_jobs import AnalysisJob
from app.schemas.workspace import PlotRun
from app.services import analysis_jobs, plots, project_research
from app.services.workflow_service import check_project_access

ACTIVE = {"queued", "running", "cancel_requested", "cancel_pending"}


async def item(db, project, user, value):
    result = {
        **value,
        "can_cancel": False,
        "can_retry": False,
        "events": [],
        "started_at": None,
        "completed_at": None,
        "error_code": None,
        "error_message": None,
        "phase": value["status"],
    }
    kind, identity = value["kind"], uuid.UUID(value["id"])
    base = f"/projects/{project}"
    result["result_href"] = f"{base}/history?kind={kind}&sourceId={identity}"
    result["edit_href"] = result["result_href"]
    if value["archived"]:
        if result["status"] in ACTIVE:
            result["recorded_status"] = result["status"]
            result["status"] = "snapshot"
            result["phase"] = "snapshot"
        if kind == "analysis_run":
            source = await project_research.source(db, project, user, kind, identity)
            job = source["payload"]["record"]["payload"]
            result["operation"] = job["operation"]
            result.update(
                started_at=job.get("started_at"),
                completed_at=job.get("completed_at"),
                events=job.get("events", []),
                error_code=job.get("error_code"),
                error_message=analysis_jobs.readable_error(
                    job.get("error_code"), job.get("error_message")
                ),
            )
            if job.get("native_id"):
                result["result_href"] = (
                    f"{base}/history?kind={job['operation']}&sourceId={job['native_id']}"
                )
        return result
    if kind == "analysis_run":
        record = await analysis_jobs.get(db, identity, user)
        job = AnalysisJob.model_validate(record["payload"])
        result.update(
            operation=job.operation,
            status=job.status,
            phase=job.phase,
            started_at=job.started_at,
            completed_at=job.completed_at,
            events=[event.model_dump() for event in job.events],
            error_code=job.error_code,
            error_message=analysis_jobs.readable_error(job.error_code, job.error_message),
            dataset_id=str(job.dataset_id),
            can_cancel=job.status in ACTIVE,
            can_retry=job.status in {"failed", "cancelled", "interrupted", "not_applicable"},
        )
        if job.native_id:
            result["result_href"] = f"{base}/history?kind={job.operation}&sourceId={job.native_id}"
        result["edit_href"] = (
            f"{base}/machine-learning?datasetId={job.dataset_id}&sourceJob={identity}"
            if job.operation == "ml"
            else result["result_href"]
            if job.native_id
            else f"{base}/workspace?datasetId={job.dataset_id}"
        )
        dataset = await db.get(Dataset, job.dataset_id)
        if dataset is not None and dataset.project_id == project:
            result["filename"] = dataset.original_filename
    elif kind == "plot_run":
        record = await plots.get_run(db, project, identity, user)
        job = PlotRun.model_validate(record["payload"])
        result.update(
            status=job.status,
            phase=job.status,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error_code=job.error_code,
            error_message=job.error_message,
            can_cancel=job.status in ACTIVE,
            dataset_id=str(job.scope.dataset_id),
        )
        result["edit_href"] = f"{base}/workspace?view=plot&plotId={job.draft_id}"
    elif kind == "media_run":
        from app.services import multimodal_cases

        row = await db.get(WorkspaceRecord, identity)
        from app.schemas.media_runs import MediaRun

        payload = MediaRun.model_validate_json(row.payload_json)
        source_data = await db.get(Dataset, payload.dataset_id)
        job = await multimodal_cases.get_run(db, user, source_data, identity)
        result.update(
            status=job["status"],
            phase=job["progress"],
            error_message=job["error_message"],
            started_at=job["started_at"],
            completed_at=job["completed_at"],
        )
        result["result_href"] = f"{base}/workspace?datasetId={payload.dataset_id}"
    elif kind == "research":
        from app.services import research_service as ledger

        run, _ = await ledger.run(db, identity, user.id)
        result.update(
            status=run.state,
            phase=run.state,
            revision=run.revision,
            can_cancel=run.state in ACTIVE or run.state == "waiting_approval",
        )
        result["edit_href"] = f"{base}/workspace"
    elif result["status"] in {"running", "pending", "queued"}:
        # Old synchronous records carry no process identity or durable controller.
        result["recorded_status"] = result["status"]
        result["status"] = "unconfirmed"
        result["phase"] = "unconfirmed"
    return result


async def listing(db, project, user, q="", kind=None, state="all", offset=0, limit=30):
    result = await project_research.history(
        db, project, user, q, kind, offset, limit, runs_only=True, run_filter=state
    )
    result["items"] = [await item(db, project, user, row) for row in result["items"]]
    return result


async def detail(db, project, user, kind, identity):
    await check_project_access(db, project, user)
    source = await project_research.source(db, project, user, kind, identity)
    raw = source["payload"]
    record = raw.get("record", {})
    body = record.get("payload", {})
    meta = {
        "kind": kind,
        "id": str(identity),
        "archived": source["archived"],
        "status": body.get("status", raw.get("status", "saved")),
        "title": body.get("title", raw.get("method", kind)),
        "revision": source["latest_revision"],
        "dataset_id": source.get("dataset_id"),
        "filename": source.get("filename"),
    }
    result = await item(db, project, user, meta)
    if kind == "analysis_run":
        result["configuration"] = body["configuration"]
        result["dataset_sha256"] = body["dataset_sha256"]
    elif kind == "plot_run":
        result["code"] = body.get("code", "")
        result["dataset_sha256"] = body.get("scope", {}).get("dataset_sha256")
    else:
        result["configuration"] = raw.get("input_config", raw.get("input", {}))
        result["dataset_sha256"] = source.get("dataset_sha256")
    return result


async def cancel(db, project, user, kind, identity):
    await check_project_access(db, project, user)
    if kind == "analysis_run":
        row = await db.get(WorkspaceRecord, identity)
        if row is None or row.project_id != project:
            raise analysis_jobs.failure(status=404)
        await analysis_jobs.cancel(db, identity, user)
    elif kind == "plot_run":
        await plots.cancel(db, project, identity, user)
        await db.commit()
    elif kind == "research":
        from app.routers import research as router
        from app.schemas.research import CancelRun
        from app.services import research_service as ledger

        run, workspace = await ledger.run(db, identity, user.id)
        if workspace.project_id != project:
            raise analysis_jobs.failure(status=404)
        if run.kind in {"planner", "reader", "reviewer"}:
            from app.research import planning_control, reader_control, reviewer_control

            control = {
                "planner": planning_control,
                "reader": reader_control,
                "reviewer": reviewer_control,
            }[run.kind]
            await control.request(
                db, user.id, identity, run.revision, "cancel", f"run-page:{uuid.uuid4()}"
            )
        else:
            await router.cancel_run(identity, CancelRun(expected_revision=run.revision), db, user)
        await db.commit()
    else:
        raise analysis_jobs.failure("这条旧记录没有可确认的取消入口，请查看原记录。")
    return await detail(db, project, user, kind, identity)
