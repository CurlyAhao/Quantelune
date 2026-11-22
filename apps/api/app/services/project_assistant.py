"""Persistent, question-first conversations using the existing Deep Agents SDK.

Conversation files belong to the local project. Computations remain ordinary
StatForge runs, executed and cancelled by the existing analysis job supervisor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.models.dataset import UploadStatus
from app.models.user import User
from app.research.egress import credentials
from app.schemas.project_assistant import AssistantTurn
from app.services import dataset_service, local_workspaces
from app.services import research_provider_service as providers
from app.services.workflow_service import check_project_access


def now():
    return datetime.now(UTC).isoformat()


def failure(message: str, status=409):
    return StatForgeError(status, "ASSISTANT_UNAVAILABLE", message)


async def access(db, project, user):
    await check_project_access(db, project, user)
    if settings.storage_backend != "local":
        raise failure("请在桌面版打开项目后使用分析助手。", 422)


def directory(owner, project):
    root = local_workspaces.project_root(project)
    if root is not None:
        path = root / ".statforge" / "assistant" / str(owner)
    else:
        path = (
            Path(settings.app_data_path or settings.local_storage_path)
            / "assistant"
            / str(owner)
            / str(project)
        )
    return local_workspaces.checked_path(path)


def save(owner, project, value):
    local_workspaces.write_json(directory(owner, project) / f"{uuid.UUID(value['id'])}.json", value)


@dataclass
class LiveTurn:
    owner: uuid.UUID
    project: uuid.UUID
    value: dict
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    tool_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    in_model: bool = False
    job_id: uuid.UUID | None = None
    plot_job_id: uuid.UUID | None = None
    tool_cache: dict[str, Any] = field(default_factory=dict)

    def check(self):
        if self.cancelled.is_set():
            raise asyncio.CancelledError

    def progress(self, text):
        if self.value["active_turn"]:
            self.value["active_turn"]["progress"] = "正在停止…" if self.cancelled.is_set() else text

    def checkpoint(self):
        self.value["updated_at"] = now()
        save(self.owner, self.project, self.value)


LIVE: dict[tuple, LiveTurn] = {}
START_LOCK = asyncio.Lock()


def read(owner, project, identity):
    key = (owner, project, identity)
    if key in LIVE:
        return LIVE[key].value
    path = directory(owner, project) / f"{identity}.json"
    if not path.is_file():
        raise failure("这段对话不存在，请新建对话。", 404)
    local_workspaces.checked_path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("owner_id") != str(owner) or value.get("project_id") != str(project):
        raise failure("无法访问这段对话。", 404)
    if value.get("active_turn"):
        value["active_turn"] = None
        if value["messages"] and value["messages"][-1].get("status") == "running":
            message = value["messages"][-1]
            message["status"] = "cancelled"
            message["content"] = (message["content"] + "\n\n上次分析已中断，可以继续提问。").strip()
        save(owner, project, value)
    return value


def list_conversations(owner, project, context_key):
    root = directory(owner, project)
    if not root.exists():
        return []
    result = []
    for path in root.glob("*.json"):
        try:
            identity = uuid.UUID(path.stem)
        except ValueError:
            continue
        value = read(owner, project, identity)
        if value["context_key"] == context_key:
            result.append(value)
    return sorted(result, key=lambda item: item["updated_at"], reverse=True)


def material_selection(dataset, context_key):
    meta = json.loads(dataset.preview_json or "{}")
    key = "signal_options" if dataset.signal_session else "sample_selection"
    selected = meta.get(key, {}).get(context_key or "project", {})
    if selected.get("sha256") and selected["sha256"] != dataset.sha256:
        return {}
    return selected


async def latest_material_result(db, owner, project, dataset, context_key):
    """Restore a matching completed turn without modifying saved history."""
    from app.models.dataset import Dataset
    from app.models.workspace import WorkspaceRecord
    from app.services.material_run_facts import material_brief, material_facts

    if not dataset.sha256:
        return None
    candidates = []
    root = directory(owner, project)
    for path in root.glob("*.json") if root.exists() else []:
        try:
            identity = uuid.UUID(path.stem)
            live = LIVE.get((owner, project, identity))
            value = (
                live.value
                if live
                else json.loads(local_workspaces.checked_path(path).read_text(encoding="utf-8"))
            )
            if (
                value.get("owner_id") != str(owner)
                or value.get("project_id") != str(project)
                or value.get("context_key") != context_key
            ):
                continue
            messages = value.get("messages", [])
            for i in range(1, len(messages)):
                question, answer = messages[i - 1], messages[i]
                if (
                    question.get("role") == "user"
                    and answer.get("role") == "assistant"
                    and answer.get("status") == "completed"
                    and answer.get("results")
                    and question.get("dataset_id") == str(dataset.id)
                    and question.get("material_selection", {})
                    == material_selection(dataset, context_key)
                    and (
                        not question.get("dataset_sha256")
                        or question["dataset_sha256"] == dataset.sha256
                    )
                ):
                    candidates.append((answer.get("created_at", ""), value, question, answer))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    for _, value, question, answer in sorted(candidates, key=lambda x: x[0], reverse=True):
        facts = None
        for result in answer["results"]:
            if result.get("kind") != "dataset":
                continue
            try:
                derived = await db.get(Dataset, uuid.UUID(result["id"]))
                if not derived or derived.project_id != project or not derived.media_result:
                    continue
                row = await db.get(WorkspaceRecord, uuid.UUID(derived.media_result["run_id"]))
                if (
                    not row
                    or row.project_id != project
                    or row.user_id != owner
                    or row.kind != "media_run"
                ):
                    continue
                run = json.loads(row.payload_json)
                if (
                    run.get("status") != "completed"
                    or (run.get("task_id") or "project") != context_key
                ):
                    continue
                if str(derived.id) not in {
                    str(run.get(k)) for k in ("summary_id", "package_id", "index_id")
                }:
                    continue
                facts = material_facts(
                    project, {**run, "id": str(row.id)}, str(dataset.id), dataset.sha256
                )
                if facts:
                    break
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        if not question.get("dataset_sha256") and not facts:
            continue
        item = {**value, "messages": [question, answer], "active_turn": None}
        if facts:
            item["material_brief"] = material_brief(facts)
            item["material_facts"] = facts
        return item
    return None


async def start(db, project, user, request: AssistantTurn):
    await access(db, project, user)
    question = request.question.strip()
    if not question:
        raise failure("请输入问题。", 422)
    if credentials(question):
        raise failure("请将 API Key 填在模型设置中，不要发送到对话。", 422)
    identity = request.conversation_id or uuid.uuid5(
        user.id, f"assistant:{project}:{request.request_id}"
    )
    digest = hashlib.sha256(
        json.dumps(
            {
                "question": question,
                "dataset": str(request.dataset_id),
                "context": request.context_key,
                **({"material_action": True} if request.material_action else {}),
                **(
                    {"figure_target": request.figure_target.model_dump(mode="json")}
                    if request.figure_target
                    else {}
                ),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    async with START_LOCK:
        path = directory(user.id, project) / f"{identity}.json"
        value = read(user.id, project, identity) if path.exists() else None
        if request.conversation_id and value is None:
            raise failure("这段对话不存在，请新建对话。", 404)
        if value:
            if value["context_key"] != request.context_key:
                raise failure("当前对话所属项目位置已变化，请新建对话。")
            previous = next(
                (
                    item
                    for item in value["messages"]
                    if item.get("request_id") == str(request.request_id)
                ),
                None,
            )
            if previous:
                if previous.get("request_hash") != digest:
                    raise failure("问题已变化，请重新发送。")
                return value
            if value["active_turn"]:
                raise failure("当前分析还在进行，可以先停止再发送。")
        try:
            await providers.current(db, user.id, required=True)
        except StatForgeError as exc:
            if "NOT_CONFIGURED" in exc.error_code:
                raise failure("请先在模型设置中保存 API Key 和模型。", 422) from None
            raise
        datasets = await dataset_service.list_project_datasets(db, project, user.id)
        ready = [
            item
            for item in datasets
            if item.upload_status == UploadStatus.READY
            or (item.raw_file and item.upload_status == UploadStatus.UPLOADED)
        ]
        from app.services import project_assistant_tasks

        task_context = await project_assistant_tasks.context(db, project, user, request.context_key)
        if request.material_action and not request.dataset_id:
            raise failure("请选择当前材料。", 422)
        dataset_id = (
            request.dataset_id
            if request.material_action
            else (
                (uuid.UUID(task_context["dataset_id"]) if task_context["dataset_id"] else None)
                if task_context
                else request.dataset_id
            )
        )
        figure = None
        if request.figure_target:
            from app.preview.workspace import load_record

            row = await load_record(db, request.figure_target.id, user, project)
            payload = json.loads(row.payload_json)
            if row.revision != request.figure_target.revision:
                raise failure("图形版本已变化，请重新打开这张图。")
            if row.kind not in {"plot_run", "figure"} or (
                row.kind == "plot_run" and payload["status"] != "completed"
            ):
                raise failure("请选择已生成的图形。", 422)
            dataset_id = uuid.UUID(payload["scope"]["dataset_id"])
            figure = {
                **request.figure_target.model_dump(mode="json"),
                "kind": row.kind,
                "title": payload["title"],
            }
        if task_context is None and dataset_id is None and value and value.get("dataset_id"):
            prior = uuid.UUID(value["dataset_id"])
            if any(item.id == prior for item in ready):
                dataset_id = prior
        if task_context is None and dataset_id is None and len(ready) == 1:
            dataset_id = ready[0].id
        if dataset_id and not any(item.id == dataset_id for item in ready):
            raise failure("所选数据尚未就绪，请在项目数据中完成导入。", 422)
        await db.commit()  # Release the provider journal fence before model/network work.
        if value is None:
            value = {
                "id": str(identity),
                "owner_id": str(user.id),
                "project_id": str(project),
                "title": question[:80],
                "context_key": request.context_key,
                "messages": [],
                "created_at": now(),
                "updated_at": now(),
                "dataset_id": None,
                "active_turn": None,
            }
        value["dataset_id"] = str(dataset_id) if dataset_id else None
        value["task_context"] = task_context
        value["material_action"] = request.material_action
        value["current_figure"] = figure
        value["messages"].extend(
            [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": question,
                    "created_at": now(),
                    "request_id": str(request.request_id),
                    "request_hash": digest,
                    **({"figure_target": figure} if figure else {}),
                    "task_context": task_context,
                    "dataset_id": value["dataset_id"],
                    "material_selection": next(
                        (
                            material_selection(item, request.context_key)
                            for item in ready
                            if item.id == dataset_id
                        ),
                        {},
                    ),
                    "dataset_sha256": next(
                        (item.sha256 for item in ready if item.id == dataset_id), None
                    ),
                },
                {
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": "",
                    "created_at": now(),
                    "status": "running",
                    "results": [],
                },
            ]
        )
        value["active_turn"] = {
            "id": str(request.request_id),
            "status": "running",
            "progress": "正在读取项目…",
        }
        live = LiveTurn(user.id, project, value)
        live.checkpoint()
        LIVE[(user.id, project, identity)] = live
        live.task = asyncio.create_task(execute(live), name=f"project-assistant:{identity}")
        return value


def stop(owner, project, identity):
    value = read(owner, project, identity)
    live = LIVE.get((owner, project, identity))
    if live:
        live.cancelled.set()
        value["active_turn"]["status"] = "stopping"
        live.progress("正在停止…")
        live.checkpoint()
        if live.in_model and live.task:
            live.task.cancel()  # Cancels the actual HTTP stream, not only the UI.
    return value


async def execute(live):
    message = live.value["messages"][-1]
    try:
        from app.research.project_chat import converse

        live.check()
        await converse(live)
        live.check()
        if not message["content"].strip():
            message["content"] = (
                "计算已完成，结果见下方。"
                if message["results"]
                else "模型没有返回回答，请重试或检查模型设置。"
            )
        message["status"] = (
            "completed"
            if message["results"]
            or message["content"] != "模型没有返回回答，请重试或检查模型设置。"
            else "failed"
        )
    except asyncio.CancelledError:
        message["status"] = "cancelled"
        message["content"] = (message["content"] + "\n\n已停止。").strip()
    except Exception as exc:
        message["status"] = "failed"
        text = exc.detail if isinstance(exc, StatForgeError) else "这次分析未完成，请重试。"
        message["content"] = (message["content"] + "\n\n" + text).strip()
    finally:
        try:
            if live.plot_job_id:
                from app.services import plots

                async with async_session_factory() as db:
                    user = await db.get(User, live.owner)
                    await plots.cancel(db, live.project, live.plot_job_id, user)
                    await db.commit()
                plot_job = plots.JOBS.get(live.plot_job_id)
                if plot_job:
                    await asyncio.shield(plot_job[1])
            if live.job_id:
                from app.services import analysis_jobs

                async with async_session_factory() as db:
                    user = await db.get(User, live.owner)
                    await analysis_jobs.cancel(db, live.job_id, user)
                job = analysis_jobs.JOBS.get(live.job_id)
                if job:
                    await asyncio.shield(job.task)
        except Exception:
            message["status"] = "failed"
            message["content"] += "\n\n计算状态未能确认，请在项目结果中查看。"
        finally:
            live.value["active_turn"] = None
            try:
                live.checkpoint()
            finally:
                LIVE.pop((live.owner, live.project, uuid.UUID(live.value["id"])), None)


async def close():
    running = list(LIVE.values())
    for live in running:
        stop(live.owner, live.project, uuid.UUID(live.value["id"]))
    if running:
        await asyncio.gather(*(live.task for live in running if live.task), return_exceptions=True)
