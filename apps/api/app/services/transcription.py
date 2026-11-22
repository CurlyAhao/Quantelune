"""Local transcription runs and immutable text versions for existing media datasets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.models.user import User
from app.models.workspace import WorkspaceRecord
from app.preview.workspace import load_record
from app.preview.write_fence import acquire_write_fence
from app.services import (
    dataset_service,
    local_workspaces,
    project_assistant_tasks,
    project_sources,
    storage_service,
)
from app.services.multimodal_cases import runtime_config
from app.services.plots import put

INSTANCE = uuid.uuid4().hex
JOBS: dict[uuid.UUID, asyncio.Task] = {}


def invalid(message: str, status: int = 422):
    return StatForgeError(status, "TRANSCRIPTION_INPUT", message)


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=10000)

    @model_validator(mode="after")
    def check_range(self):
        if self.end <= self.start:
            raise ValueError("片段结束时间应晚于开始时间。")
        return self


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    task_id: uuid.UUID | None = None
    language: Literal["auto", "en", "zh"] = "auto"
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)
    force: bool = False

    @model_validator(mode="after")
    def check_range(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("结束时间应晚于开始时间。")
        return self


class TranscriptionEdit(BaseModel):
    expected_revision: int = Field(ge=1)
    segments: list[Segment] = Field(max_length=10000)
    task_id: uuid.UUID | None = None


class TranscriptionRun(BaseModel):
    kind: Literal["transcription_run"] = "transcription_run"
    title: str
    source_id: uuid.UUID
    source_sha256: str
    task_id: uuid.UUID | None = None
    instance: str
    status: Literal["queued", "running", "completed", "failed", "interrupted"] = "queued"
    progress: str = ""
    error_message: str | None = None
    request: dict
    signature: str
    machine_segments: list[Segment] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    text_id: str | None = None
    srt_id: str | None = None
    versions: list[dict] = Field(default_factory=list)
    tool: dict = Field(default_factory=dict)
    started_at: str
    completed_at: str | None = None


def now():
    return datetime.now(UTC).isoformat()


def path_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def transcript_text(segments):
    return "\n".join(item.text.strip() for item in segments if item.text.strip()) + "\n"


def srt_time(seconds):
    milliseconds = round(seconds * 1000)
    hours, remaining = divmod(milliseconds, 3600000)
    minutes, remaining = divmod(remaining, 60000)
    seconds, milliseconds = divmod(remaining, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def transcript_srt(segments):
    return (
        "\n\n".join(
            f"{i}\n{srt_time(item.start)} --> {srt_time(item.end)}\n{item.text.strip()}"
            for i, item in enumerate(segments, 1)
            if item.text.strip()
        )
        + "\n"
    )


async def record(db, user, source, identity):
    row = await load_record(db, identity, user, source.project_id)
    if row.kind != "transcription_run":
        raise invalid("没有找到这次转录。", 404)
    run = TranscriptionRun.model_validate_json(row.payload_json)
    if run.source_id != source.id:
        raise invalid("这次转录不属于当前材料。", 404)
    return row, run


async def get(db, user, source, identity):
    row, run = await record(db, user, source, identity)
    if run.status in {"queued", "running"} and run.instance != INSTANCE:
        await acquire_write_fence(db, user.id)
        run.status, run.error_message = "interrupted", "软件关闭时转录尚未完成，请重新转录。"
        await put(db, user, source.project_id, run, row.id)
        await db.commit()
        await db.refresh(row)
    return {"id": str(row.id), "revision": row.revision, **run.model_dump(mode="json")}


async def listing(db, user, source):
    rows = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == source.project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "transcription_run",
            )
            .order_by(WorkspaceRecord.created_at.desc())
        )
    ).all()
    results = []
    for row in rows:
        run = TranscriptionRun.model_validate_json(row.payload_json)
        if run.source_id == source.id:
            results.append(await get(db, user, source, row.id))
    return results


async def save_texts(db, user, source, run, identity, directory, task_id=None):
    version = len(run.versions) + 1
    version_id = uuid.uuid4().hex
    outputs = []
    for suffix, content in (
        ("txt", transcript_text(run.segments)),
        ("srt", transcript_srt(run.segments)),
    ):
        name = f"{Path(source.original_filename).stem} · 转录.{suffix}"
        path = directory / f"{version_id}.{suffix}"
        path.write_text(content, encoding="utf-8")
        saved = await project_sources.store(
            db, source.project_id, user.id, str(path), name, f"转录/{version_id}/{name}"
        )
        meta = json.loads(saved.preview_json or "{}")
        relative = path.relative_to(local_workspaces.project_root(source.project_id)).as_posix()
        meta.update(
            {
                "source_dataset_id": str(source.id),
                "display_name": name,
                "transcription": {
                    "run_id": str(identity),
                    "version": version,
                    "source_id": str(source.id),
                    "source_sha256": source.sha256,
                    "workspace_path": relative,
                    "start": run.request["start"],
                    "paired_selection": run.request.get("paired_selection"),
                    "end": run.request.get("actual_end"),
                    "segments": [s.model_dump(mode="json") for s in run.segments],
                    "method": "manual_revision"
                    if run.segments != run.machine_segments
                    else "machine",
                },
            }
        )
        saved.preview_json = json.dumps(meta, ensure_ascii=False)
        outputs.append(saved)
    run.text_id, run.srt_id = (str(item.id) for item in outputs)
    run.versions.append(
        {
            "version": version,
            "text_id": run.text_id,
            "srt_id": run.srt_id,
            "saved_at": now(),
            "method": "manual_revision" if run.segments != run.machine_segments else "machine",
        }
    )
    destination_task = task_id or run.task_id
    if destination_task:
        live = SimpleNamespace(
            project=source.project_id, value={"task_context": {"id": str(destination_task)}}
        )
        await project_assistant_tasks.append(db, user, live, "dataset_ids", outputs[0].id)


async def version(db, user, source, identity, text_id):
    """Read an immutable saved transcript revision belonging to this run and source."""
    _, run = await record(db, user, source, identity)
    indices = [i for i, item in enumerate(run.versions) if item.get("text_id") == str(text_id)]
    if len(indices) != 1:
        raise invalid("没有找到唯一的转录修订。", 404)
    index = indices[0]
    text = await dataset_service.get_dataset_with_access(db, text_id, user.id)
    meta = json.loads(text.preview_json or "{}").get("transcription") or {}
    if (
        text.project_id != source.project_id
        or source.sha256 != run.source_sha256
        or meta.get("source_id") != str(source.id)
        or meta.get("source_sha256") != run.source_sha256
    ):
        raise invalid("转录修订与原材料不匹配。", 409)
    segments = [Segment.model_validate(item) for item in meta.get("segments", [])]
    root = local_workspaces.project_root(source.project_id)
    relative = meta.get("workspace_path")
    if root is None or not isinstance(relative, str):
        raise invalid("转录修订文件不可用。", 409)
    from app.services.multimodal_cases import checked_file

    path = checked_file(root, relative)
    if path_hash(path) != text.sha256 or path.read_text(encoding="utf-8") != transcript_text(
        segments
    ):
        raise invalid("转录修订文字已变化，请恢复原文件。", 409)
    # Older saved revisions lack these two fields; the unique run.versions entry is
    # still an exact version binding. Partially conflicting metadata is rejected.
    annotatable = meta.get("run_id") in (None, str(identity)) and meta.get("version") in (
        None,
        index + 1,
    )
    return {
        "text_id": str(text_id),
        "version": index + 1,
        "run_id": str(identity),
        "annotatable": annotatable,
        "segments": [item.model_dump(mode="json") for item in segments],
    }


async def start(db, user, source, body: TranscriptionRequest, *, paired_selection=None):
    if source.file_format.lower() not in {"wav", "mp4"}:
        raise invalid("请选择 WAV 音频或 MP4 视频。")
    root = local_workspaces.project_root(source.project_id)
    if root is None:
        raise invalid("请先打开材料所在的文件夹。")
    if body.task_id:
        await project_assistant_tasks.context(db, source.project_id, user, str(body.task_id))
    config = runtime_config()
    model = config["options"].get("whisper_path")
    if not model or not Path(model).is_dir():
        raise invalid("本地转录模型尚未配置，请检查桌面版模型文件。")
    path = storage_service.get_local_object_path(source.object_key).resolve()
    if not path.is_file() or path_hash(path) != source.sha256:
        raise invalid("原文件已变化或不可用，请刷新工作区后重新选择。")
    model_digest = hashlib.sha256()
    for file in sorted(Path(model).rglob("*")):
        if file.is_file():
            model_digest.update(str(file.relative_to(model)).encode("utf-8"))
            model_digest.update(path_hash(file).encode("ascii"))
    request = body.model_dump(mode="json", exclude={"force", "task_id"})
    if paired_selection is not None:
        request["paired_selection"] = paired_selection
    signature = hashlib.sha256(
        json.dumps(
            {
                "source": source.sha256,
                "request": request,
                "model": model_digest.hexdigest(),
                "engine": "whisper-local-v1",
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if not body.force:
        for item in await listing(db, user, source):
            if item["status"] == "completed" and item["signature"] == signature:
                if body.task_id and item["text_id"]:
                    await acquire_write_fence(db, user.id)
                    live = SimpleNamespace(
                        project=source.project_id, value={"task_context": {"id": str(body.task_id)}}
                    )
                    await project_assistant_tasks.append(
                        db, user, live, "dataset_ids", uuid.UUID(item["text_id"])
                    )
                    await db.commit()
                return {**item, "reused": True}
    identity = uuid.uuid4()
    run = TranscriptionRun(
        title=f"{source.original_filename} · 转录",
        source_id=source.id,
        source_sha256=source.sha256,
        task_id=body.task_id,
        instance=INSTANCE,
        request=request,
        signature=signature,
        started_at=now(),
        progress="等待转录",
        tool={"model_sha256": model_digest.hexdigest()},
    )
    await acquire_write_fence(db, user.id)
    await put(db, user, source.project_id, run, identity)
    await db.commit()
    JOBS[identity] = asyncio.create_task(
        execute(identity, source.id, source.project_id, user.id, path, root, config)
    )
    JOBS[identity].add_done_callback(lambda _: JOBS.pop(identity, None))
    return {"id": str(identity), "revision": 1, **run.model_dump(mode="json"), "reused": False}


async def execute(identity, source_id, project, owner, source_path, root, config):
    directory = root / ".statforge" / "storage" / "transcription" / identity.hex
    try:
        local_workspaces.checked_path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            source = await dataset_service.get_dataset_with_access(db, source_id, owner)
            _, run = await record(db, user, source, identity)
            request = {
                **run.request,
                "model": config["options"]["whisper_path"],
                "ffmpeg": config["options"]["ffmpeg_path"],
                "source": str(source_path),
                "output": str(directory),
            }
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HOME": config["model_root"],
            "TEMP": config["temp"],
            "TMP": config["temp"],
        }
        worker = Path(__file__).with_name("transcription_worker.py")
        process = await asyncio.create_subprocess_exec(
            config["python"],
            str(worker),
            str(request_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )

        async def collect_error():
            return (await process.stderr.read()).decode("utf-8", errors="replace")

        stderr_task = asyncio.create_task(collect_error())
        async for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            async with async_session_factory() as db:
                user = await db.get(User, owner)
                source = await dataset_service.get_dataset_with_access(db, source_id, owner)
                _, run = await record(db, user, source, identity)
                run.status, run.progress = "running", str(event.get("progress", "正在转录"))
                await acquire_write_fence(db, owner)
                await put(db, user, project, run, identity)
                await db.commit()
        exit_code = await process.wait()
        error = await stderr_task
        if exit_code:
            raise RuntimeError(
                error.strip().splitlines()[-1] if error.strip() else "本地转录未完成。"
            )
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            source = await dataset_service.get_dataset_with_access(db, source_id, owner)
            _, run = await record(db, user, source, identity)
            if path_hash(source_path) != run.source_sha256:
                raise invalid("转录期间原文件发生变化，请刷新后重新转录。")
            run.machine_segments = [Segment.model_validate(item) for item in result["segments"]]
            run.segments = [item.model_copy() for item in run.machine_segments]
            run.request["actual_end"] = run.request["start"] + result["duration"]
            run.tool.update(
                {
                    "name": "Whisper",
                    "local_model": request["model"],
                    "transformers": result["transformers"],
                    "torch": result["torch"],
                    "language": run.request["language"],
                    "sample_rate": 16000,
                    "time_basis": "source player seconds",
                }
            )
            await acquire_write_fence(db, owner)
            await save_texts(db, user, source, run, identity, directory)
            run.status, run.progress, run.completed_at = "completed", "转录完成", now()
            await put(db, user, project, run, identity)
            await db.commit()
    except Exception as error:
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            source = await dataset_service.get_dataset_with_access(db, source_id, owner)
            _, run = await record(db, user, source, identity)
            run.status, run.error_message, run.completed_at = "failed", str(error)[:1500], now()
            await acquire_write_fence(db, owner)
            await put(db, user, project, run, identity)
            await db.commit()


async def edit(db, user, source, identity, body: TranscriptionEdit):
    await acquire_write_fence(db, user.id)
    row, run = await record(db, user, source, identity)
    await db.refresh(row)
    if row.revision != body.expected_revision:
        raise invalid("转录已更新，请重新打开后修改。", 409)
    run = TranscriptionRun.model_validate_json(row.payload_json)
    if run.status != "completed":
        raise invalid("请等待转录完成。")
    source_path = storage_service.get_local_object_path(source.object_key).resolve()
    if (
        source.sha256 != run.source_sha256
        or not source_path.is_file()
        or path_hash(source_path) != run.source_sha256
    ):
        raise invalid("原文件已变化或不可用，请重新转录。", 409)
    old_times = [(s.id, s.start, s.end) for s in run.machine_segments]
    new_times = [(s.id, s.start, s.end) for s in body.segments]
    if old_times != new_times:
        raise invalid("转录片段时间已变化，请重新打开后修改。", 409)
    run.segments = body.segments
    root = local_workspaces.project_root(source.project_id)
    if root is None:
        raise invalid("请先打开材料所在的文件夹。")
    directory = root / ".statforge" / "storage" / "transcription" / identity.hex
    local_workspaces.checked_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if body.task_id:
        await project_assistant_tasks.context(db, source.project_id, user, str(body.task_id))
    await save_texts(db, user, source, run, identity, directory, body.task_id)
    await put(db, user, source.project_id, run, identity)
    await db.commit()
    return await get(db, user, source, identity)
