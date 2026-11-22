"""Owned plotting drafts, native process runs and immutable output files."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.ml.resources import MLResourceError, WorkerLimits
from app.ml.worker_control import MLTaskScope, run_ml_worker
from app.models.project import Project
from app.models.user import User
from app.models.workspace import WorkspaceRecord, WorkspaceRevision
from app.plotting.worker import ARTIFACT_TYPES, verify_bundle
from app.preview.context import canonical, load_dataset, verify_storage_version
from app.preview.workspace import credential_free, load_record, read_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.workspace import PlotArtifact, PlotDraft, PlotRun, SaveRecord
from app.services import storage_service
from app.services.workflow_service import check_project_access

INSTANCE = uuid.uuid4()
ACTIVE = {"queued", "running", "cancel_requested"}
JOBS: dict[uuid.UUID, tuple[asyncio.Event, asyncio.Task]] = {}


def failure(code, message, status=409):
    return StatForgeError(status, "PLOT_" + code, message)


def now():
    return datetime.now(UTC).isoformat()


async def put(db, user, project, payload, identity):
    """Internal state writer, always called under the existing actor fence."""
    raw = canonical(payload.model_dump(mode="json"))
    row = await db.get(WorkspaceRecord, identity)
    if row is None:
        row = WorkspaceRecord(
            id=identity, user_id=user.id, project_id=project, kind=payload.kind, payload_json=raw
        )
        db.add(row)
        await db.flush()
    else:
        if row.user_id != user.id or row.project_id != project or row.kind != payload.kind:
            raise failure("NOT_FOUND", "没有找到这份图形记录。", 404)
        row.revision += 1
        row.payload_json = raw
        row.updated_at = datetime.now(UTC)
    db.add(WorkspaceRevision(record_id=row.id, revision=row.revision, payload_json=raw))
    await db.flush()
    return row


async def get_run(db, project, identity, user):
    await check_project_access(db, project, user)
    row = await db.get(WorkspaceRecord, identity)
    if row is None or row.project_id != project or row.user_id != user.id:
        from app.plotting.archives import archived

        saved_project = await db.get(Project, project)
        historical = next(
            (item for item in archived(saved_project) if item["id"] == str(identity)), None
        )
        if historical is not None:
            return historical
        raise failure("NOT_FOUND", "没有找到这次绘图。", 404)
    row = await load_record(db, identity, user, project)
    if row.kind != "plot_run":
        raise failure("NOT_FOUND", "没有找到这次绘图。", 404)
    payload = PlotRun.model_validate_json(row.payload_json)
    if payload.status in ACTIVE and payload.instance != INSTANCE:
        await acquire_write_fence(db, user.id)
        await db.refresh(row)
        payload = PlotRun.model_validate_json(row.payload_json)
        if payload.status in ACTIVE and payload.instance != INSTANCE:
            payload.status = "interrupted"
            payload.error_code = "PLOT_INTERRUPTED"
            payload.error_message = "绘图已中断，可以修改代码后重新运行。"
            payload.completed_at = now()
            await put(db, user, project, payload, identity)
    return await read_record(db, identity, user)


async def start(db, project, draft_id, revision, request_id, user, *, lineage=None):
    await acquire_write_fence(db, user.id)
    await check_project_access(db, project, user)
    identity = uuid.uuid5(user.id, f"plot-run:{project}:{draft_id}:{request_id}")
    existing = await db.get(WorkspaceRecord, identity)
    if existing:
        payload = PlotRun.model_validate_json(existing.payload_json)
        if (
            existing.user_id != user.id
            or existing.project_id != project
            or payload.draft_revision != revision
            or payload.draft_id != draft_id
        ):
            raise failure("REQUEST_CHANGED", "运行请求已变化，请重新开始。")
        return await get_run(db, project, identity, user)
    draft = await load_record(db, draft_id, user, project)
    if draft.kind != "plot" or draft.revision != revision:
        raise failure("DRAFT_CHANGED", "代码已有新版本，请重新保存后运行。")
    source = PlotDraft.model_validate_json(draft.payload_json)
    await credential_free(db, user, source.model_dump(mode="json"))
    await load_dataset(db, source.scope, user)
    run = PlotRun(
        title=source.title,
        draft_id=draft_id,
        draft_revision=revision,
        request_id=request_id,
        instance=INSTANCE,
        scope=source.scope,
        code=source.code,
        dpi=source.dpi,
        status="queued",
        metadata=lineage or {},
    )
    await put(db, user, project, run, identity)
    await db.commit()
    cancel = asyncio.Event()
    task = asyncio.create_task(execute(identity, project, user.id, cancel), name=f"plot:{identity}")
    JOBS[identity] = (cancel, task)
    return await read_record(db, identity, user)


async def acoustic_overview(db, project, source_id, expected_revision, request_id, user):
    """Save and run a new draft revision bound to the exact old run and dataset."""
    from app.plotting.acoustic_summary import ACOUSTIC_SUMMARY_FIELDS, acoustic_summary_program
    from app.preview.context import schema_snapshot
    from app.preview.workspace import save_record

    await acquire_write_fence(db, user.id)
    source_row = await load_record(db, source_id, user, project)
    if source_row.kind != "plot_run":
        raise failure("NOT_FOUND", "没有找到这次绘图。", 404)
    source = PlotRun.model_validate_json(source_row.payload_json)
    if source.status not in {"completed", "failed"} or source.draft_revision != expected_revision:
        raise failure("DRAFT_CHANGED", "原图或草稿版本已变化，请重新打开后操作。")
    dataset = await load_dataset(db, source.scope, user)
    if (dataset.media_result or {}).get("kind") != "summary_csv":
        raise failure("ACOUSTIC_SUMMARY_REQUIRED", "请选择声学特征摘要图。", 422)
    columns = list(source.scope.columns)
    if not any(name in ACOUSTIC_SUMMARY_FIELDS for name in columns):
        raise failure("ACOUSTIC_SUMMARY_REQUIRED", "原图没有选择声学摘要指标。", 422)
    if set(columns) - ACOUSTIC_SUMMARY_FIELDS.keys() - {"speech_count_unit"}:
        raise failure(
            "ACOUSTIC_SCOPE", "原图还使用了其他字段，请保留原图，单独选择声学指标绘制概览。", 422
        )
    available = {column["name"] for column in schema_snapshot(dataset.schema_json)}
    if (
        "speech_rate_per_second" in columns
        and "speech_count_unit" in available
        and "speech_count_unit" not in columns
    ):
        columns.append("speech_count_unit")
    scope = source.scope.model_copy(update={"columns": columns})
    code = acoustic_summary_program(columns, dataset.row_count, source.title)
    # Repeated requests return the same run, without another draft revision.
    identity = uuid.uuid5(user.id, f"plot-run:{project}:{source.draft_id}:{request_id}")
    existing = await db.get(WorkspaceRecord, identity)
    if existing is not None:
        if (
            existing.user_id != user.id
            or existing.project_id != project
            or existing.kind != "plot_run"
        ):
            raise failure("REQUEST_CHANGED", "运行请求已变化，请重新开始。")
        prior = PlotRun.model_validate_json(existing.payload_json)
        if (
            prior.draft_id != source.draft_id
            or prior.draft_revision != expected_revision + 1
            or prior.scope != scope
            or prior.code != code
        ):
            raise failure("REQUEST_CHANGED", "运行请求已变化，请重新开始。")
        return await get_run(db, project, identity, user)
    draft_row = await load_record(db, source.draft_id, user, project)
    if draft_row.kind != "plot" or draft_row.revision != expected_revision:
        raise failure("DRAFT_CHANGED", "草稿已有新版本，请重新打开后操作。")
    draft = PlotDraft.model_validate_json(draft_row.payload_json)
    if draft.scope != source.scope or draft.code != source.code:
        raise failure("DRAFT_CHANGED", "草稿与原图的数据或代码不一致。")
    await verify_storage_version(dataset)
    revised = draft.model_copy(update={"scope": scope, "code": code})
    saved = await save_record(
        db,
        user,
        SaveRecord(project_id=project, expected_revision=expected_revision, payload=revised),
        record_id=source.draft_id,
        commit=False,
    )
    return await start(db, project, source.draft_id, saved["revision"], request_id, user)


async def transition(identity, project, owner, **changes):
    async with async_session_factory() as db:
        user = await db.get(User, owner)
        await acquire_write_fence(db, owner)
        row = await load_record(db, identity, user, project)
        value = PlotRun.model_validate_json(row.payload_json)
        if value.status not in ACTIVE:
            return value
        value = value.model_copy(update=changes)
        await put(db, user, project, value, identity)
        await db.commit()
        return value


async def replay_code(db, project, run, user):
    """Reconstruct legacy style ancestry without changing saved runs."""
    from app.plotting.program import STYLE_MARKER, split_styles

    pending = []
    seen = set()
    current = run
    while True:
        try:
            _, recorded = split_styles(current.code)
        except Exception:
            raise failure(
                "STYLE_UNAVAILABLE", "样式记录无法读取，请保留原图并重新绘制。", 422
            ) from None
        if recorded or not current.metadata.get("style_parent_id"):
            code = current.code
            base_dpi = recorded[0]["base_dpi"] if recorded else current.dpi
            break
        parent_id = current.metadata["style_parent_id"]
        if (
            parent_id in seen
            or len(seen) >= 100
            or not isinstance(current.metadata.get("style"), dict)
        ):
            raise failure("STYLE_UNAVAILABLE", "样式版本链无法读取，原图仍可查看。", 422)
        seen.add(parent_id)
        pending.append((current.metadata["style"], current.dpi))
        try:
            parent = await get_run(db, project, uuid.UUID(parent_id), user)
            current = PlotRun.model_validate(parent["payload"])
        except (ValueError, TypeError):
            raise failure("STYLE_UNAVAILABLE", "样式版本链无法读取，原图仍可查看。", 422) from None
    for style, dpi in reversed(pending):
        code += STYLE_MARKER + json.dumps(
            {"style": style, "dpi": dpi, "base_dpi": base_dpi},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return code, base_dpi


async def start_style(db, project, source_id, patch, request_id, user):
    """Version only the appearance of a native, verified rendered figure."""
    from app.preview.workspace import save_record

    await acquire_write_fence(db, user.id)
    source = await get_run(db, project, source_id, user)
    run = PlotRun.model_validate(source["payload"])
    if source.get("archived") or run.status != "completed" or "figure_state" not in run.artifacts:
        raise failure(
            "STYLE_UNAVAILABLE",
            "这幅旧图没有可用的渲染状态。新生成的图表支持只修改样式；旧图仍可查看和导出。",
            422,
        )
    if patch.colors:
        from matplotlib.colors import to_hex

        available = {to_hex(color, keep_alpha=True) for color in run.metadata.get("colors", [])}
        if available and any(
            to_hex(color, keep_alpha=True) not in available for color in patch.colors
        ):
            raise failure(
                "STYLE_COLOR_NOT_FOUND",
                "原颜色不在这幅图中，请重新读取图形并使用实际显示的颜色。",
                422,
            )
    # artifact() enforces this native run's storage prefix, length and SHA.
    content, _ = await artifact(db, project, source_id, "figure_state", user)
    from app.plotting.program import STYLE_MARKER, split_styles

    code, base_dpi = await replay_code(db, project, run, user)
    style = patch.model_dump(mode="json", exclude_none=True)
    dpi = patch.dpi or run.dpi
    code += STYLE_MARKER + json.dumps(
        {"style": style, "dpi": dpi, "base_dpi": base_dpi},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        split_styles(code)
    except ValueError:
        raise failure("STYLE_UNAVAILABLE", "样式记录过长或无效，原图仍可查看。", 422) from None
    if len(code) > 32000:
        raise failure("STYLE_UNAVAILABLE", "样式记录过长，原图仍可查看。", 422)
    draft = PlotDraft(title=(patch.title or run.title)[:160], scope=run.scope, code=code, dpi=dpi)
    saved = await save_record(db, user, SaveRecord(project_id=project, payload=draft), commit=False)
    identity = uuid.uuid5(user.id, f"plot-style:{project}:{source_id}:{request_id}")
    derived = PlotRun(
        title=draft.title,
        draft_id=uuid.UUID(saved["id"]),
        draft_revision=saved["revision"],
        request_id=request_id,
        instance=INSTANCE,
        scope=run.scope,
        code=code,
        dpi=draft.dpi,
        status="queued",
        metadata={
            "style_parent_id": str(source_id),
            "style_root_draft_id": run.metadata.get("style_root_draft_id", str(run.draft_id)),
            "style": style,
        },
    )
    await put(db, user, project, derived, identity)
    await db.commit()
    cancel = asyncio.Event()
    task = asyncio.create_task(
        execute(
            identity,
            project,
            user.id,
            cancel,
            style_source=(content, run.metadata, derived.metadata),
        ),
        name=f"plot-style:{identity}",
    )
    JOBS[identity] = (cancel, task)
    return await read_record(db, identity, user)


async def execute(identity, project, owner, cancel, style_source=None):
    uploaded = []
    publishing = False
    try:

        async def cancelled():
            return cancel.is_set()

        async with MLTaskScope(WorkerLimits(wall_seconds=180), cancelled) as task:
            run = await transition(identity, project, owner, status="running", started_at=now())
            async with async_session_factory() as db:
                user = await db.get(User, owner)
                dataset = await load_dataset(db, run.scope, user)
                source_key, fmt, sheet, encoding = (
                    dataset.object_key,
                    dataset.file_format,
                    dataset.sheet_name,
                    dataset.csv_encoding,
                )
                if style_source is None:
                    await verify_storage_version(dataset)
            with tempfile.TemporaryDirectory(prefix="sf-plot-") as directory:
                source = Path(directory) / ("source." + fmt)
                config = {"code": run.code, "columns": run.scope.columns, "dpi": run.dpi}
                input_sha, operation = run.scope.dataset_sha256, "plot_render"
                if style_source is None:
                    storage_service.download_to_path(source_key, str(source))
                else:
                    content, parent_metadata, style_metadata = style_source
                    source.write_bytes(content)
                    input_sha, fmt, sheet, encoding = (
                        hashlib.sha256(content).hexdigest(),
                        "figure_state",
                        None,
                        None,
                    )
                    operation = "plot_style"
                    config.update(
                        style=style_metadata["style"],
                        source_sha256=run.scope.dataset_sha256,
                        parent_metadata=parent_metadata,
                    )
                response = await run_ml_worker(
                    str(source),
                    fmt,
                    config,
                    input_sha,
                    operation=operation,
                    task=task,
                    sheet_name=sheet,
                    csv_encoding=encoding,
                )
                files = verify_bundle(response, config, run.scope.dataset_sha256)
                await task.check()
                artifacts = {}
                for extension, content in files.items():
                    await task.check()
                    sha = hashlib.sha256(content).hexdigest()
                    key = f"users/{owner}/projects/{project}/plots/{identity}/{sha}.{extension}"
                    path = Path(directory) / ("figure." + extension)
                    path.write_bytes(content)
                    storage_service.upload_file(key, str(path), ARTIFACT_TYPES[extension])
                    uploaded.append(key)
                    artifacts[extension] = PlotArtifact(
                        object_key=key, sha256=sha, bytes=len(content)
                    )
                await task.check()
                async with async_session_factory() as db:
                    user = await db.get(User, owner)
                    await acquire_write_fence(db, owner)
                    row = await load_record(db, identity, user, project)
                    current = PlotRun.model_validate_json(row.payload_json)
                    await load_dataset(db, current.scope, user)
                    if cancel.is_set() or current.status == "cancel_requested":
                        raise MLResourceError("ML_CANCELLED", "绘图已取消。")
                    complete = current.model_copy(
                        update={
                            "status": "completed",
                            "completed_at": now(),
                            "artifacts": artifacts,
                            "metadata": {
                                **current.metadata,
                                **response["result"],
                                **{
                                    key: value
                                    for key, value in current.metadata.items()
                                    if key in {"style_parent_id", "style_root_draft_id", "style"}
                                },
                                "runtime_limits": response["runtime_limits"],
                            },
                        }
                    )
                    await credential_free(db, user, complete.model_dump(mode="json"))
                    publishing = True
                    await put(db, user, project, complete, identity)
                    await db.commit()
                uploaded = []
    except BaseException as error:
        if not publishing:
            for key in uploaded:
                storage_service.delete_object(key)
        code = getattr(error, "code", getattr(error, "error_code", "PLOT_FAILED"))
        if isinstance(error, asyncio.CancelledError):
            code = "PLOT_INTERRUPTED"
        status = (
            "cancelled"
            if code == "ML_CANCELLED"
            else "interrupted"
            if isinstance(error, asyncio.CancelledError)
            else "failed"
        )
        message = (
            str(error)[:1000]
            if isinstance(error, (MLResourceError, StatForgeError))
            else "绘图没有完成，请检查代码后重新运行。"
        )
        if code == "ML_CANCELLED":
            message = "绘图已取消。"
        await asyncio.shield(
            transition(
                identity,
                project,
                owner,
                status=status,
                completed_at=now(),
                error_code=code,
                error_message=message,
            )
        )
    finally:
        JOBS.pop(identity, None)


async def cancel(db, project, identity, user):
    await acquire_write_fence(db, user.id)
    record = await get_run(db, project, identity, user)
    run = PlotRun.model_validate(record["payload"])
    if run.status in ACTIVE:
        active = JOBS.get(identity)
        if active is None:
            raise failure("WORKER_UNAVAILABLE", "进程状态无法确认，请重新读取记录。")
        run.status = "cancel_requested"
        await put(db, user, project, run, identity)
        active[0].set()
    return await read_record(db, identity, user)


async def list_runs(db, project, draft_id, user):
    await check_project_access(db, project, user)
    rows = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == project,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "plot_run",
            )
            .order_by(WorkspaceRecord.created_at.desc())
        )
    ).all()
    result = []
    from app.services.project_research import timestamp

    for row in rows:
        run = PlotRun.model_validate_json(row.payload_json)
        if draft_id is None or run.draft_id == draft_id:
            item = await get_run(db, project, row.id, user)
            item["created_at"] = timestamp(row.created_at)
            result.append(item)
        if len(result) >= 50:
            break
    from app.plotting.archives import archived

    saved_project = await db.get(Project, project)
    seen = {item["id"] for item in result}
    for item in archived(saved_project):
        if item["id"] not in seen and (
            draft_id is None or item["payload"]["draft_id"] == str(draft_id)
        ):
            result.append(item)
    result.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return result[:50]


async def artifact(db, project, identity, extension, user):
    record = await get_run(db, project, identity, user)
    run = PlotRun.model_validate(record["payload"])
    if run.status != "completed":
        raise failure("NOT_READY", "这次绘图还没有可用产物。")
    if extension == "py":
        code, _ = await replay_code(db, project, run, user)
        note = "# 绘图代码需配合导出的代码与数据包运行；单独此文件不能复现图形。\n"
        return (note + code).encode("utf-8"), "text/x-python;charset=utf-8"
    value = run.artifacts.get(extension)
    prefix = (
        f"users/{user.id}/projects/{project}/imported-plots/"
        if record.get("archived")
        else f"users/{user.id}/projects/{project}/plots/{identity}/"
    )
    if not value or not value.object_key.startswith(prefix):
        raise failure("ARTIFACT_UNAVAILABLE", "图形文件无法读取，请重新运行。")
    with tempfile.TemporaryDirectory(prefix="sf-plot-read-") as directory:
        path = Path(directory) / "figure"
        storage_service.download_to_path(value.object_key, str(path))
        if path.stat().st_size != value.bytes:
            raise failure("ARTIFACT_CHANGED", "图形文件已变化，请重新运行。")
        content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != value.sha256:
        raise failure("ARTIFACT_CHANGED", "图形文件已变化，请重新运行。")
    return content, ARTIFACT_TYPES[extension]


async def close():
    pending = [entry[1] for entry in JOBS.values()]
    for signal, _task in JOBS.values():
        signal.set()
    await asyncio.gather(*pending, return_exceptions=True)
