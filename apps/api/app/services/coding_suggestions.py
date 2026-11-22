"""User-requested theme suggestions grounded in a verified, bounded selection."""

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.coding import CodingLocation
from app.schemas.material import CitationRequest
from app.services import material_service, project_assistant, text_coding


class ThemeSuggestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annotation_id: uuid.UUID | None = None
    share_selection: Literal[True]
    share_previous_context: bool = False
    task_id: uuid.UUID | None = None
    revision: int = Field(ge=0)
    material_id: uuid.UUID
    material_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    material_version: int = Field(ge=1)
    locator: CodingLocation
    purpose: str = Field(min_length=1, max_length=500)


def digest(locator) -> str:
    value = json.dumps(
        locator.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def path(owner, project, identity) -> Path:
    return project_assistant.directory(owner, project) / "coding-suggestions" / f"{identity}.json"


async def transcript_excerpt(db, project, user, material, locator, include_previous=False):
    """Resolve only the pinned transcription version and selected segments."""
    from app.services import dataset_service

    ref = locator.transcription
    if ref is None:
        raise text_coding.fail("请先选择可核验的转录片段。", 422)
    from app.services import transcription

    text = await dataset_service.get_dataset_with_access(db, ref.text_id, user.id)
    meta = json.loads(text.preview_json or "{}").get("transcription", {})
    if text.project_id != project or not meta.get("source_id"):
        raise text_coding.fail("请从原录音打开对应转录。", 422)
    source = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(meta["source_id"]), user.id
    )
    if json.loads(material.metadata_json or "{}").get("source_dataset_id") != str(
        source.id
    ) and json.loads(source.preview_json or "{}").get("material_id") != str(material.id):
        raise text_coding.fail("转录与所选材料不匹配。")
    if source.project_id != project or source.sha256 != material.sha256:
        raise text_coding.fail("转录与所选材料不匹配。")
    saved = await transcription.version(db, user, source, ref.run_id, ref.text_id)
    if not saved["annotatable"] or saved["version"] != ref.version:
        raise text_coding.fail("转录版本已变化，请重新选择。")
    segments = saved["segments"]
    if len(set(ref.segment_ids)) != len(ref.segment_ids):
        raise text_coding.fail("转录片段无效，请重新选择。", 422)
    selected = [
        item for item in segments if isinstance(item, dict) and item.get("id") in ref.segment_ids
    ]
    if (
        [item.get("id") for item in selected] != ref.segment_ids
        or any(
            not isinstance(item.get("text"), str)
            or not isinstance(item.get("start"), (int, float))
            or not isinstance(item.get("end"), (int, float))
            or item["start"] < locator.start_seconds
            or item["end"] > locator.end_seconds
            for item in selected
        )
        or "\n".join(item["text"].strip() for item in selected) != ref.quote
    ):
        raise text_coding.fail("转录内容或时间范围已变化，请重新选择。")
    if include_previous:
        first_index = next(
            i for i, item in enumerate(segments) if item.get("id") == ref.segment_ids[0]
        )
        prior = segments[first_index - 1] if first_index else None
        previous = (
            prior.get("text", "")
            if prior and prior.get("end", float("inf")) <= selected[0]["start"]
            else ""
        )
        return ref.quote, previous[-400:]
    return ref.quote


async def selection_context(db, project, user, body):
    from app.research.draft_advice import freeze
    from app.services import project_assistant_tasks

    await material_service.project_access(db, project, user.id)
    state = await text_coding.read(db, project, user)
    if state["revision"] != body.revision:
        raise text_coding.fail("主题已变化，请重新请求建议。")
    themes = [theme for theme in state["payload"]["themes"] if theme.get("is_codable", True)]
    if not themes and body.action == "themes":
        raise text_coding.fail("请先新建主题，再请求建议。", 422)
    if body.locator.kind == "signal_time":
        raise text_coding.fail("此处暂不支持波形选段的主题建议。", 422)
    material = await material_service.load(db, body.material_id, user.id, project)
    await material_service.verify_version(material)
    material_service.validate_locator(
        material,
        CitationRequest(
            material_sha256=body.material_sha256,
            material_version=body.material_version,
            reviewed=True,
            locator=body.locator,
        ),
    )
    previous = ""
    if body.locator.kind == "text":
        # validate_locator has compared the quote against the exact extracted page.
        excerpt = body.locator.quote
        content_kind = "已核验的原文选段"
        if body.share_previous_context:
            pages = json.loads(material.extraction_json).get("pages", [])
            page = next(item for item in pages if item["page"] == body.locator.page)
            previous = page["text"][max(0, body.locator.start - 400) : body.locator.start]
    elif body.locator.kind == "time":
        if body.share_previous_context:
            excerpt, previous = await transcript_excerpt(
                db, project, user, material, body.locator, include_previous=True
            )
        else:
            excerpt = await transcript_excerpt(db, project, user, material, body.locator)
        content_kind = "版本化转录片段"
    elif body.locator.kind == "region" and body.locator.note.strip():
        excerpt = body.locator.note.strip()
        content_kind = "用户提供的图片区域描述（并非模型视觉识别）"
    else:
        raise text_coding.fail(
            "请先选择文字或填写图片区域描述；媒体时间段需要可核验的转录选段。", 422
        )
    task = (
        await project_assistant_tasks.context(db, project, user, str(body.task_id))
        if body.task_id
        else None
    )
    annotation = {}
    if body.annotation_id:
        segment = next(
            (
                item
                for item in state["payload"]["segments"]
                if str(item["id"]) == str(body.annotation_id)
            ),
            None,
        )
        if (
            not segment
            or str(segment["material_id"]) != str(material.id)
            or segment["material_sha256"] != material.sha256
            or segment["locator"] != body.locator.model_dump(mode="json")
        ):
            raise text_coding.fail("这条标注已变化，请重新打开。")
        annotation = {
            "annotation": {
                "note": segment["memo"],
                "themes": [
                    theme["name"] for theme in themes if theme["id"] in segment["theme_ids"]
                ],
            }
        }
    return freeze(
        {
            **annotation,
            "kind": "selection",
            "project_id": str(project),
            "action": body.action,
            "task_id": str(body.task_id) if body.task_id else None,
            "purpose": body.purpose,
            "question": ((task or {}).get("question") or (task or {}).get("title") or "")[:500],
            "material_id": str(material.id),
            "material_version": material.version,
            "material_sha256": material.sha256,
            "locator_hash": digest(body.locator),
            "coding_revision": body.revision,
            "selection_type": content_kind,
            "excerpt": excerpt,
            "previous_context": previous,
            "selection_policy": "excerpt-v3",
            "themes": [
                {"id": theme["id"], "name": theme["name"], "description": theme["memo"]}
                for theme in themes
            ]
            if body.action == "themes"
            else [],
        }
    )


async def suggest(db, project, user, body: ThemeSuggestionInput, request, action="themes"):
    from app.schemas.draft_advice import AdviceCreate, AdviceSend, SelectionTarget
    from app.services import draft_advice_service as advice
    from app.services import material_guidance
    from app.services.research_gateway_service import get_gateway

    target = SelectionTarget(**body.model_dump(), action=action)
    context = await selection_context(db, project, user, target)
    identity = uuid.UUID(context.source_version[:32])
    saved_path = path(user.id, project, identity)
    if saved_path.is_file():
        return json.loads(saved_path.read_text(encoding="utf-8"))
    gateway = get_gateway()
    # This explicit per-selection action binds approval and charge to the verified wire.
    prepared = await advice.create(
        db,
        user,
        project,
        AdviceCreate(
            request_id=uuid.uuid4(),
            source_version=context.source_version,
            question=body.purpose,
            target=target,
        ),
        gateway,
    )
    run_id = uuid.UUID(prepared["id"])
    approval = prepared["approval"]
    await advice.send(
        db,
        user,
        project,
        run_id,
        AdviceSend(approval_id=approval["id"], request_hash=approval["request_hash"]),
        gateway,
    )
    await db.commit()
    job = advice.JOBS.get(run_id)
    if job:
        while not job[1].done():
            await asyncio.wait({job[1]}, timeout=0.3)
            if await request.is_disconnected():
                await advice.cancel(db, user, project, run_id, gateway)
                raise asyncio.CancelledError
    completed = await advice.read(db, user, project, run_id, gateway)
    result = completed["proposal"]
    if result is None:
        raise text_coding.fail(
            completed.get("error") or "未取得可定位的建议，请重新选择片段后重试。", 422
        )
    if (
        await selection_context(db, project, user, target)
    ).source_version != context.source_version:
        raise text_coding.fail("选段或主题已更新，请重新选择。")
    suggestions = result["suggestions"]
    data = {
        "proposal_id": str(identity),
        "model_run_id": str(run_id),
        "revision": body.revision,
        "material_id": str(body.material_id),
        "material_sha256": body.material_sha256,
        "material_version": body.material_version,
        "locator_hash": digest(body.locator),
        "theme_ids": [item["theme_id"] for item in suggestions],
        "suggestions": suggestions,
        "summary": result["summary"],
        "evidence": result["evidence"],
        "previous_context": context.document.get("previous_context", ""),
    }
    material_guidance.write_json(saved_path, data)
    return data


def adopted(owner, project, body):
    if not body.proposal_id:
        return set()
    try:
        record = json.loads(path(owner, project, body.proposal_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise text_coding.fail("建议已失效，请重新生成。") from None
    if (
        record.get("revision") != body.revision
        or record.get("material_id") != str(body.material_id)
        or record.get("material_sha256") != body.material_sha256
        or record.get("material_version") != body.material_version
        or not body.locator
        or record.get("locator_hash") != digest(body.locator)
    ):
        raise text_coding.fail("选区或主题已变化，请重新生成建议。")
    return set(record.get("theme_ids", [])) & {str(item) for item in body.theme_ids}
