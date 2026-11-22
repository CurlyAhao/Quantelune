"""Image feature runs for real project files using the existing local model."""

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

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.user import User
from app.models.workspace import WorkspaceRecord
from app.preview.write_fence import acquire_write_fence
from app.services import (
    dataset_service,
    local_workspaces,
    multimodal_cases,
    project_assistant_tasks,
    project_sources,
    storage_service,
)
from app.services.plots import put

INSTANCE = uuid.uuid4().hex
JOBS: dict[uuid.UUID, asyncio.Task] = {}
FORMATS = {"png", "jpg", "jpeg", "gif", "webp"}


class VisualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_ids: list[uuid.UUID] = Field(min_length=1)
    descriptions: dict[str, str] = Field(default_factory=dict)
    task_id: uuid.UUID | None = None
    force: bool = False


class VisualRun(BaseModel):
    kind: Literal["visual_run"] = "visual_run"
    title: str
    status: Literal["queued", "running", "completed", "failed", "interrupted"] = "queued"
    instance: str
    task_id: uuid.UUID | None = None
    dataset_ids: list[uuid.UUID]
    parameters: dict = Field(default_factory=dict)
    progress: str = "等待处理"
    started_at: str
    completed_at: str | None = None
    error_message: str | None = None
    summary_id: str | None = None
    package_id: str | None = None
    index_id: str | None = None
    artifact_directory: str | None = None


def error(message: str, status: int = 422) -> StatForgeError:
    return StatForgeError(status, "VISUAL_INPUT", message)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def public(row: WorkspaceRecord, run: VisualRun) -> dict:
    return {"id": str(row.id), **run.model_dump(mode="json")}


async def owned_run(db, user, project, identity):
    await dataset_service._verify_project_access(db, project, user.id)
    row = await db.get(WorkspaceRecord, identity)
    if not row or row.user_id != user.id or row.project_id != project or row.kind != "visual_run":
        raise error("没有找到这次图像处理。", 404)
    run = VisualRun.model_validate_json(row.payload_json)
    if run.status in {"queued", "running"} and run.instance != INSTANCE:
        run.status = "interrupted"
        run.error_message = "处理已中断，请重新运行。"
        await acquire_write_fence(db, user.id)
        await put(db, user, project, run, row.id)
        await db.commit()
    return row, run


async def list_runs(db, user, project, dataset_id, task_id=None):
    await dataset_service._verify_project_access(db, project, user.id)
    rows = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == project,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "visual_run",
            )
            .order_by(WorkspaceRecord.created_at.desc())
        )
    ).all()
    result = []
    for row in rows:
        run = VisualRun.model_validate_json(row.payload_json)
        if dataset_id in run.dataset_ids and (task_id is None or run.task_id == task_id):
            _, run = await owned_run(db, user, project, row.id)
            result.append(public(row, run))
    return result


async def start(db, user, project, body: VisualRequest):
    await dataset_service._verify_project_access(db, project, user.id)
    if len(set(body.dataset_ids)) != len(body.dataset_ids):
        raise error("同一张图片只需选择一次。")
    if set(body.descriptions) - {str(item) for item in body.dataset_ids}:
        raise error("描述对应的图片已变化，请重新选择。")
    if any(len(text) > 4000 for text in body.descriptions.values()):
        raise error("图片描述不能超过 4000 字。")
    root = local_workspaces.project_root(project)
    if root is None:
        raise error("请先打开图片所在工作区。")
    if body.task_id:
        await project_assistant_tasks.context(db, project, user, str(body.task_id))
    selected = []
    for identity in body.dataset_ids:
        row = await dataset_service.get_dataset_with_access(db, identity, user.id)
        if (
            row.project_id != project
            or row.file_format.lower() not in FORMATS
            or row.upload_status == UploadStatus.DELETED
        ):
            raise error("请选择当前项目中的图片。")
        path = storage_service.get_local_object_path(row.object_key).resolve()
        try:
            actual = await asyncio.to_thread(file_hash, path)
        except OSError:
            raise error(f"图片不可读取：{row.original_filename}。") from None
        if actual != row.sha256:
            raise error(f"图片已变化：{row.original_filename}。请刷新文件夹后重新选择。")
        selected.append(
            {
                "id": str(row.id),
                "name": row.original_filename,
                "sha256": actual,
                "path": str(path),
                "description": body.descriptions.get(str(row.id), ""),
            }
        )
    config = multimodal_cases.runtime_config()
    weight_path = Path(config["options"]["resnet_weights"])
    try:
        weight_sha = await asyncio.to_thread(file_hash, weight_path)
    except OSError:
        raise error("本地 ResNet18 模型不可用，请检查桌面组件。") from None
    signature = hashlib.sha256(
        json.dumps(
            {"samples": selected, "weights": weight_sha, "engine": "resnet18-v1"},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if not body.force:
        rows = (
            await db.scalars(
                select(WorkspaceRecord)
                .where(
                    WorkspaceRecord.project_id == project,
                    WorkspaceRecord.user_id == user.id,
                    WorkspaceRecord.kind == "visual_run",
                )
                .order_by(WorkspaceRecord.created_at.desc())
            )
        ).all()
        for row in rows:
            old = VisualRun.model_validate_json(row.payload_json)
            if old.status == "completed" and old.parameters.get("signature") == signature:
                if body.task_id:
                    live = SimpleNamespace(
                        project=project, value={"task_context": {"id": str(body.task_id)}}
                    )
                    for identity in (old.summary_id, old.package_id, old.index_id):
                        if identity:
                            await project_assistant_tasks.append(
                                db, user, live, "dataset_ids", uuid.UUID(identity)
                            )
                    await db.commit()
                return {**public(row, old), "reused": True}
    identity = uuid.uuid4()
    run = VisualRun(
        title=f"图像比较 · {len(selected)} 张",
        instance=INSTANCE,
        task_id=body.task_id,
        dataset_ids=body.dataset_ids,
        parameters={
            "signature": signature,
            "model": "ResNet18",
            "samples": selected,
            "weights_sha256": weight_sha,
        },
        started_at=datetime.now(UTC).isoformat(),
    )
    await acquire_write_fence(db, user.id)
    await put(db, user, project, run, identity)
    await db.commit()
    JOBS[identity] = asyncio.create_task(
        execute(identity, user.id, project, root, selected, config)
    )
    JOBS[identity].add_done_callback(lambda _: JOBS.pop(identity, None))
    return {"id": str(identity), **run.model_dump(mode="json"), "reused": False}


async def execute(identity, owner, project, root, samples, config):
    output = root / ".statforge" / "storage" / "visual-runs" / identity.hex
    process = None
    try:
        local_workspaces.checked_path(output)
        output.mkdir(parents=True, exist_ok=False)
        request = output / "request.json"
        request.write_text(
            json.dumps(
                {"output": str(output), "samples": samples, "options": config["options"]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        worker = Path(__file__).with_name("visual_worker.py")
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TORCH_HOME": config["model_root"],
            "TEMP": config["temp"],
            "TMP": config["temp"],
        }
        process = await asyncio.create_subprocess_exec(
            config["python"],
            str(worker),
            str(request),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        errors = asyncio.create_task(process.stderr.read())
        async for line in process.stdout:
            try:
                progress = json.loads(line).get("label", "正在提取图像特征")
            except ValueError:
                continue
            async with async_session_factory() as db:
                user = await db.get(User, owner)
                row, run = await owned_run(db, user, project, identity)
                run.status, run.progress = "running", str(progress)
                await acquire_write_fence(db, owner)
                await put(db, user, project, run, identity)
                await db.commit()
        code = await process.wait()
        log = await errors
        (output / "process.log").write_bytes(log)
        if code:
            details = log.decode("utf-8", errors="replace").strip().splitlines()
            last = details[-1].partition(": ")[2] if details else ""
            raise RuntimeError(
                last
                if last.startswith("无法读取图片：")
                else "本地图像处理未完成，请检查模型与图片文件。"
            )
        method = json.loads((output / "parameters.json").read_text(encoding="utf-8"))
        for sample in samples:
            if await asyncio.to_thread(file_hash, Path(sample["path"])) != sample["sha256"]:
                raise error(f"处理期间图片已变化：{sample['name']}。请重新处理。")
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            row, run = await owned_run(db, user, project, identity)
            if method.get("model_sha256") != run.parameters.get("weights_sha256"):
                raise error("处理时模型文件已变化，请重新运行。")
            await acquire_write_fence(db, owner)
            summary = await dataset_service.upload_dataset(
                db,
                project,
                owner,
                str(output / "summary.csv"),
                "图像比较摘要.csv",
                allow_duplicate=True,
            )
            package = await project_sources.store(
                db,
                project,
                owner,
                str(output / "vectors.npz"),
                "图像完整特征.npz",
                f"图像比较/{identity.hex}/图像完整特征.npz",
            )
            index = await project_sources.store(
                db,
                project,
                owner,
                str(output / "index.json"),
                "图像样本索引.json",
                f"图像比较/{identity.hex}/图像样本索引.json",
            )
            sources = {s["id"]: s["sha256"] for s in samples}
            for item, kind in ((summary, "summary"), (package, "vectors"), (index, "index")):
                meta = json.loads(item.preview_json or "{}")
                meta["source_dataset_id"] = samples[0]["id"]
                meta["visual_result"] = {"run_id": str(identity), "sources": sources, "kind": kind}
                item.preview_json = json.dumps(meta, ensure_ascii=False)
            if run.task_id:
                live = SimpleNamespace(
                    project=project, value={"task_context": {"id": str(run.task_id)}}
                )
                for item in (summary, package, index):
                    await project_assistant_tasks.append(db, user, live, "dataset_ids", item.id)
            run.summary_id, run.package_id, run.index_id = (
                str(summary.id),
                str(package.id),
                str(index.id),
            )
            run.parameters["method"] = method
            run.artifact_directory = str(output)
            run.status, run.progress = "completed", f"{len(samples)} 张图片已完成"
            run.completed_at = datetime.now(UTC).isoformat()
            await put(db, user, project, run, identity)
            await db.commit()
    except Exception as exc:
        async with async_session_factory() as db:
            user = await db.get(User, owner)
            row, run = await owned_run(db, user, project, identity)
            run.status = "failed"
            run.error_message = str(exc)[:1500]
            run.completed_at = datetime.now(UTC).isoformat()
            await acquire_write_fence(db, owner)
            await put(db, user, project, run, identity)
            await db.commit()


async def similar(db, user, project, identity, dataset_id):
    row, run = await owned_run(db, user, project, identity)
    if run.status != "completed" or not run.package_id:
        raise error("图像处理尚未完成。")
    if dataset_id not in run.dataset_ids:
        raise error("这张图片不在此次结果中。")
    package = await dataset_service.get_dataset_with_access(db, uuid.UUID(run.package_id), user.id)
    path = storage_service.get_local_object_path(package.object_key)
    if await asyncio.to_thread(file_hash, path) != package.sha256:
        raise error("保存的图像特征已变化，请重新处理。")
    import numpy as np

    with np.load(path, allow_pickle=False) as bundle:
        vectors = bundle["image_vectors"]
        if len(vectors) != len(run.dataset_ids):
            raise error("保存的图像特征与样本不一致。")
        norms = np.linalg.norm(vectors, axis=1)
        if np.any(norms <= 0) or np.any(~np.isfinite(norms)):
            raise error("保存的图像特征不可比较。")
        reference = run.dataset_ids.index(dataset_id)
        scores = (vectors / norms[:, None]) @ (vectors[reference] / norms[reference])
    samples = run.parameters["samples"]
    current_rows = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == project,
                Dataset.id.in_(run.dataset_ids),
                Dataset.upload_status != UploadStatus.DELETED,
            )
        )
    ).all()
    current = {str(item.id): item.sha256 for item in current_rows}
    matches = [
        {
            "dataset_id": sample["id"],
            "name": sample["name"],
            "description": sample["description"],
            "score": float(scores[i]),
            "available": current.get(sample["id"]) == sample["sha256"],
        }
        for i, sample in enumerate(samples)
        if i != reference
    ]
    return {
        "items": sorted(matches, key=lambda item: item["score"], reverse=True),
        "method": "ResNet18 余弦相似度",
    }
