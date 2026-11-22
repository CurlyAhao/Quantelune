"""Observed background runs for the existing statistics and ML services."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.ml.resources import MLResourceError
from app.ml.worker_control import MLTaskScope
from app.models.dataset import Dataset, UploadStatus
from app.models.ml import MLExperiment, MLRun, MLRunStatus
from app.models.statistics import StatisticalRun, StatisticalRunStatus
from app.models.user import User
from app.models.workspace import WorkspaceRecord, WorkspaceRevision
from app.preview.context import canonical
from app.preview.workspace import credential_free, load_record, read_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.analysis_jobs import AnalysisJob, RunEvent
from app.schemas.statistics import StatisticalRunRequest
from app.services import ml_service, statistics_service
from app.services.workflow_service import check_project_access
from app.statistics.registry import METHOD_CATEGORIES, METHOD_REGISTRY

INSTANCE = uuid.uuid4()
ACTIVE = {"queued", "running", "cancel_requested"}
TERMINAL = {"completed", "not_applicable", "failed", "cancelled", "interrupted"}


def readable_error(code, message):
    if code == "ML_OUTPUT_LIMIT":
        return "模型文件过大，请减少树数或限制树深度后重试。"
    return message


@dataclass
class LiveJob:
    owner: uuid.UUID
    project: uuid.UUID
    cancel: asyncio.Event
    task: asyncio.Task
    shutdown: bool = False


JOBS: dict[uuid.UUID, LiveJob] = {}


def failure(message="运行状态无法读取，请刷新后重试。", status=409):
    return StatForgeError(status, "ANALYSIS_JOB_CHANGED", message)


def now():
    return datetime.now(UTC).isoformat()


def changed(value, **fields):
    result = value.model_copy(update=fields)
    phase = fields.get("phase", fields.get("status", result.phase))
    result.phase = phase
    if not result.events or result.events[-1].phase != phase:
        result.events = [*result.events, RunEvent(phase=phase, at=now())][-30:]
    return result


async def put(db, user, project, identity, payload):
    row = await db.get(WorkspaceRecord, identity)
    raw = canonical(payload.model_dump(mode="json"))
    if row is None:
        row = WorkspaceRecord(
            id=identity, user_id=user.id, project_id=project, kind="analysis_run", payload_json=raw
        )
        db.add(row)
        await db.flush()
    else:
        if row.user_id != user.id or row.project_id != project or row.kind != "analysis_run":
            raise failure(status=404)
        row.revision += 1
        row.payload_json = raw
        row.updated_at = datetime.now(UTC)
    db.add(WorkspaceRevision(record_id=row.id, revision=row.revision, payload_json=raw))
    await db.flush()


async def native(db, value):
    if value.native_id is None:
        return None
    if value.operation == "statistics":
        row = await db.get(StatisticalRun, value.native_id)
        if row is not None and row.dataset_id != value.dataset_id:
            raise failure(status=404)
        return row
    row = await db.get(MLRun, value.native_id)
    if row is not None and row.experiment_id != value.experiment_id:
        raise failure(status=404)
    return row


def native_status(row):
    return str(getattr(row.status, "value", row.status)) if row is not None else None


async def fresh_source(db, project, user, value):
    await db.refresh(user)
    if not user.is_active:
        raise failure("当前身份不可用。", 403)
    await check_project_access(db, project, user)
    dataset = await db.get(Dataset, value.dataset_id, populate_existing=True)
    if (
        dataset is None
        or dataset.project_id != project
        or dataset.upload_status != UploadStatus.READY
        or dataset.sha256 != value.dataset_sha256
        or dataset.sheet_name != value.sheet_name
        or dataset.csv_encoding != value.csv_encoding
    ):
        raise failure("数据来源已变化，请重新选择数据。")
    if value.operation == "ml":
        experiment = await db.get(MLExperiment, value.experiment_id, populate_existing=True)
        if (
            experiment is None
            or experiment.project_id != project
            or experiment.dataset_id != dataset.id
            or experiment.dataset_sha256 != dataset.sha256
            or json.loads(experiment.config_json or "null") != value.configuration
        ):
            raise failure("模型参数已有变化，请重新开始。")
    return dataset


async def load(db, identity, user):
    row = await load_record(db, identity, user)
    if row.kind != "analysis_run":
        raise failure(status=404)
    return row, AnalysisJob.model_validate_json(row.payload_json)


async def get(db, identity, user):
    row, value = await load(db, identity, user)
    if value.status in ACTIVE and (value.instance != INSTANCE or identity not in JOBS):
        await acquire_write_fence(db, user.id)
        await db.refresh(row)
        value = AnalysisJob.model_validate_json(row.payload_json)
        if value.status in ACTIVE:
            completed = await native(db, value)
            state = native_status(completed)
            if state in {"completed", "not_applicable", "failed"}:
                value = changed(
                    value,
                    status=state,
                    completed_at=now(),
                    error_code=completed.error_code,
                    error_message=readable_error(completed.error_code, completed.error_message),
                )
            else:
                value = changed(
                    value,
                    status="interrupted",
                    completed_at=now(),
                    error_code="ANALYSIS_INTERRUPTED",
                    error_message="上次运行已中断，可以重新开始。",
                )
                if completed is not None:
                    completed.status = (
                        StatisticalRunStatus.FAILED
                        if value.operation == "statistics"
                        else MLRunStatus.FAILED
                    )
                    completed.completed_at = datetime.now(UTC)
                    completed.error_code = "ANALYSIS_INTERRUPTED"
                    completed.error_message = value.error_message
            await put(db, user, row.project_id, identity, value)
            await db.commit()
    return await read_record(db, identity, user)


async def start(
    db, user, dataset, operation, request_id, configuration, experiment=None, expected_sha=None
):
    await acquire_write_fence(db, user.id)
    await check_project_access(db, dataset.project_id, user)
    if expected_sha is not None and dataset.sha256 != expected_sha:
        raise failure("原数据版本已变化，请重新选择数据。")
    if operation == "statistics":
        request = StatisticalRunRequest.model_validate(configuration)
        method = METHOD_REGISTRY.get(request.method_id)
        count = len(request.variables) + len(request.categorical_variables)
        if (
            not method.min_variables <= count <= method.max_variables
            or (method.needs_group and not request.group_variable)
            or (METHOD_CATEGORIES.get(request.method_id) == "regression" and not request.target)
        ):
            raise failure("请补齐此方法需要的字段与参数。", 422)
        configuration = request.model_dump(mode="json")
        title = method.name[:160]
    else:
        ml_service.config_from_dict(configuration)
        title = experiment.name[:160]
    try:
        json.dumps(configuration, allow_nan=False)
    except (ValueError, TypeError):
        raise failure("参数含有无法使用的数值，请调整后重试。", 422) from None
    identity = uuid.uuid5(user.id, f"analysis-job:{dataset.project_id}:{request_id}")
    previous = await db.get(WorkspaceRecord, identity)
    if previous:
        old = AnalysisJob.model_validate_json(previous.payload_json)
        if (
            old.dataset_id != dataset.id
            or old.dataset_sha256 != dataset.sha256
            or old.operation != operation
            or old.configuration != configuration
            or old.experiment_id != (experiment.id if experiment else None)
        ):
            raise failure("运行请求已变化，请重新开始。")
        return await get(db, identity, user)
    if sum(item.owner == user.id for item in JOBS.values()) >= 20:
        raise failure("已有 20 项计算正在运行或排队，请先等待或取消。", 429)
    value = AnalysisJob(
        title=title,
        operation=operation,
        request_id=request_id,
        instance=INSTANCE,
        dataset_id=dataset.id,
        dataset_sha256=dataset.sha256,
        sheet_name=dataset.sheet_name,
        csv_encoding=dataset.csv_encoding,
        experiment_id=experiment.id if experiment else None,
        configuration=configuration,
        created_at=now(),
        events=[RunEvent(phase="queued", at=now())],
    )
    await credential_free(db, user, value.model_dump(mode="json"))
    await fresh_source(db, dataset.project_id, user, value)
    await put(db, user, dataset.project_id, identity, value)
    await db.commit()
    stop = asyncio.Event()
    task = asyncio.create_task(
        execute(identity, dataset.project_id, user.id, stop), name=f"analysis:{identity}"
    )
    JOBS[identity] = LiveJob(user.id, dataset.project_id, stop, task)
    return await read_record(db, identity, user)


async def transition(identity, project, owner, **fields):
    async with async_session_factory() as db:
        user = await db.get(User, owner)
        await acquire_write_fence(db, owner)
        row, value = await load(db, identity, user)
        if value.status not in ACTIVE:
            return value
        value = changed(value, **fields)
        await put(db, user, project, identity, value)
        await db.commit()
        return value


async def finish_error(identity, project, owner, error):
    code = getattr(error, "code", getattr(error, "error_code", "ANALYSIS_FAILED"))
    live = JOBS.get(identity)
    status = (
        "cancelled"
        if code == "ML_CANCELLED"
        else "interrupted"
        if isinstance(error, asyncio.CancelledError)
        else "failed"
    )
    if live and live.shutdown:
        status = "interrupted"
    messages = {
        "ML_CANCELLED": "计算已取消。",
        "ML_TIMEOUT": "计算超过本次时限，可以调整参数后重试。",
        "ML_INPUT_CHANGED": "原文件已变化，请重新导入。",
    }
    message = messages.get(code, "计算未完成，请检查字段和参数后重试。")
    async with async_session_factory() as db:
        user = await db.get(User, owner)
        await acquire_write_fence(db, owner)
        row, value = await load(db, identity, user)
        if value.status not in ACTIVE:
            return
        domain = await native(db, value)
        state = native_status(domain)
        if state in {"completed", "not_applicable"}:
            status, code, message = state, None, None
        elif domain is not None and state in {"running", "pending"}:
            domain.status = (
                StatisticalRunStatus.FAILED
                if value.operation == "statistics"
                else MLRunStatus.FAILED
            )
            domain.completed_at = datetime.now(UTC)
            domain.error_code, domain.error_message = code, message
        value = changed(
            value, status=status, completed_at=now(), error_code=code, error_message=message
        )
        await put(db, user, project, identity, value)
        await db.commit()


async def execute(identity, project, owner, stop):
    scope = None
    try:

        async def cancelled():
            return stop.is_set()

        while True:
            if stop.is_set():
                raise MLResourceError("ML_CANCELLED", "Cancelled before admission.")
            scope = MLTaskScope(cancel_check=cancelled)
            try:
                await scope.__aenter__()
                break
            except MLResourceError as error:
                if error.code != "ML_BUSY":
                    raise
                scope = None
                await asyncio.sleep(0.2)
        value = await transition(
            identity, project, owner, status="running", phase="prepare", started_at=now()
        )
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            dataset = await fresh_source(db, project, user, value)

            async def started(domain):
                await acquire_write_fence(db, owner)
                await fresh_source(db, project, user, value)
                await scope.check()
                db.add(domain)
                await db.flush()
                row, current = await load(db, identity, user)
                current = changed(current, native_id=domain.id, phase="compute")
                await put(db, user, project, identity, current)
                await db.commit()

            async def computed(_domain):
                await scope.check()
                await acquire_write_fence(db, owner)
                await fresh_source(db, project, user, value)
                row, current = await load(db, identity, user)
                await put(db, user, project, identity, changed(current, phase="publish"))

            if value.operation == "statistics":
                domain = await statistics_service.run_statistics(
                    db,
                    dataset,
                    user,
                    StatisticalRunRequest.model_validate(value.configuration),
                    task=scope,
                    on_started=started,
                    on_computed=computed,
                )
            else:
                experiment = await db.get(MLExperiment, value.experiment_id)
                domain = await ml_service.run_experiment(
                    db, experiment, user, task=scope, on_started=started, on_computed=computed
                )
            await ml_service.publish_transaction(db, scope)
            status = native_status(domain)
            await transition(
                identity,
                project,
                owner,
                status=status,
                completed_at=now(),
                error_code=domain.error_code,
                error_message=readable_error(domain.error_code, domain.error_message),
            )
    except BaseException as error:
        await asyncio.shield(finish_error(identity, project, owner, error))
    finally:
        if scope is not None and scope.active:
            await scope.__aexit__(None, None, None)
        JOBS.pop(identity, None)


async def cancel(db, identity, user):
    await acquire_write_fence(db, user.id)
    record = await get(db, identity, user)
    value = AnalysisJob.model_validate(record["payload"])
    if value.status in ACTIVE:
        completed = await native(db, value)
        if native_status(completed) in {"completed", "not_applicable"}:
            value = changed(value, status=native_status(completed), completed_at=now())
        else:
            live = JOBS.get(identity)
            if live is None:
                raise failure()
            live.cancel.set()
            value = changed(value, status="cancel_requested")
        await put(db, user, uuid.UUID(record["project_id"]), identity, value)
        await db.commit()
    return await read_record(db, identity, user)


async def retry(db, identity, user, request_id):
    record = await get(db, identity, user)
    value = AnalysisJob.model_validate(record["payload"])
    if value.status not in {"failed", "cancelled", "interrupted", "not_applicable"}:
        raise failure("这次运行无需重试。")
    dataset = await fresh_source(db, uuid.UUID(record["project_id"]), user, value)
    experiment = await db.get(MLExperiment, value.experiment_id) if value.experiment_id else None
    return await start(
        db,
        user,
        dataset,
        value.operation,
        request_id,
        value.configuration,
        experiment,
        value.dataset_sha256,
    )


async def close():
    running = list(JOBS.values())
    for live in running:
        live.shutdown = True
        live.cancel.set()
    if running:
        await asyncio.gather(*(live.task for live in running), return_exceptions=True)
