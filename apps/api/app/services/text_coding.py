"""Finite text coding commands over the existing revisioned project store."""

import csv
import io
import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import anyio
from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.workspace import WorkspaceRecord, WorkspaceRevision
from app.preview.workspace import read_record, save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.coding import CodedSegment, CodingChange, CodingDocument, LocalCoder, Theme
from app.schemas.material import CitationRequest
from app.schemas.workspace import SaveRecord
from app.services import material_service


def fail(message="编码未保存，请刷新后重试。", status=409):
    return StatForgeError(status, "CODING_CHANGED", message)


async def current(db, project, user):
    await material_service.project_access(db, project, user.id)
    row = await db.scalar(
        select(WorkspaceRecord)
        .where(
            WorkspaceRecord.project_id == project,
            WorkspaceRecord.user_id == user.id,
            WorkspaceRecord.kind == "coding",
        )
        .execution_options(populate_existing=True)
    )
    return row


async def transcription_details(db, project, user, document):
    """Resolve exact pinned versions, including annotations on saved transcript TXT."""
    from app.models.dataset import Dataset
    from app.models.material import Material
    from app.services import storage_service
    from app.services.transcription import TranscriptionRun, path_hash

    text_segments = [s for s in document.segments if s.locator.kind == "text" and s.material_id]
    materials = (
        (
            await db.scalars(
                select(Material).where(
                    Material.project_id == project,
                    Material.user_id == user.id,
                    Material.id.in_([s.material_id for s in text_segments]),
                )
            )
        ).all()
        if text_segments
        else []
    )
    by_material = {item.id: item for item in materials}
    source_ids = set()
    for item in materials:
        try:
            source_ids.add(
                uuid.UUID(json.loads(item.metadata_json or "{}").get("source_dataset_id", ""))
            )
        except (ValueError, TypeError):
            pass
    datasets = (
        (
            await db.scalars(
                select(Dataset).where(Dataset.project_id == project, Dataset.id.in_(source_ids))
            )
        ).all()
        if source_ids
        else []
    )
    by_dataset = {item.id: item for item in datasets}
    records = (
        await db.scalars(
            select(WorkspaceRecord).where(
                WorkspaceRecord.project_id == project,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "transcription_run",
            )
        )
    ).all()
    runs = {}
    for record in records:
        try:
            runs[record.id] = TranscriptionRun.model_validate_json(record.payload_json)
        except ValueError:
            pass
    original_ids = {run.source_id for run in runs.values()}
    originals = (
        (
            await db.scalars(
                select(Dataset).where(Dataset.project_id == project, Dataset.id.in_(original_ids))
            )
        ).all()
        if original_ids
        else []
    )
    by_original = {item.id: item for item in originals}
    details = {}
    hashes = {}
    from app.services.survey_import import collections

    survey_groups = await collections(db, project)
    for segment in document.segments:
        ref = segment.locator.transcription if segment.locator.kind == "time" else None
        run_id = ref.run_id if ref else None
        text_id = ref.text_id if ref else None
        version = ref.version if ref else None
        valid_source = True
        if segment.locator.kind == "text" and segment.material_id:
            material = by_material.get(segment.material_id)
            try:
                source_id = (
                    uuid.UUID(
                        json.loads(material.metadata_json or "{}").get("source_dataset_id", "")
                    )
                    if material
                    else None
                )
            except (ValueError, TypeError):
                source_id = None
            source = by_dataset.get(source_id)
            survey = (
                json.loads(source.preview_json or "{}").get("survey_answer") if source else None
            )
            if survey:
                collection = survey_groups.get(survey["group_id"])
                sample = (
                    next(
                        (
                            s
                            for s in collection.media_case["samples"]
                            if s["sample_id"] == survey["case_id"]
                        ),
                        None,
                    )
                    if collection
                    else None
                )
                adopted = (sample or {}).get("answers", {}).get(survey["question"])
                valid = (
                    material.sha256 == source.sha256 == segment.material_sha256
                    and material.version == segment.material_version
                )
                details[str(segment.id)] = {
                    "status": (
                        "current"
                        if adopted and adopted["material_id"] == str(material.id)
                        else "historical"
                    )
                    if valid
                    else "unavailable",
                    "version": material.version,
                    **(
                        {
                            "case": {
                                "sample_id": survey["case_id"],
                                "sample_collection_id": str(collection.id),
                                "sample_title": survey["case_id"],
                            }
                        }
                        if collection
                        else {}
                    ),
                }
                continue
            meta = json.loads(source.preview_json or "{}").get("transcription") if source else None
            if not isinstance(meta, dict):
                continue  # Ordinary TXT is not a transcript just because of its name.
            text_id = source.id
            candidates = [
                (identity, run, index)
                for identity, run in runs.items()
                for index, item in enumerate(run.versions, 1)
                if item.get("text_id") == str(text_id)
            ]
            if len(candidates) == 1:
                run_id, run, version = candidates[0]
                valid_source = (
                    source.file_format.lower() == "txt"
                    and material.sha256 == segment.material_sha256 == source.sha256
                    and material.version == segment.material_version
                    and meta.get("source_id") == str(run.source_id)
                    and meta.get("source_sha256") == run.source_sha256
                    and meta.get("run_id") in (None, str(run_id))
                    and meta.get("version") in (None, version)
                )
                if source.id not in hashes:
                    try:
                        hashes[source.id] = path_hash(
                            storage_service.get_local_object_path(source.object_key)
                        )
                    except (OSError, ValueError, RuntimeError):
                        hashes[source.id] = None
                valid_source = valid_source and hashes[source.id] == source.sha256
            else:
                details[str(segment.id)] = {"status": "unavailable", "text_id": str(text_id)}
                continue
        if not text_id or not run_id:
            continue
        run = runs.get(run_id)
        matches = (
            [
                (index, item)
                for index, item in enumerate(run.versions, 1)
                if item.get("text_id") == str(text_id)
            ]
            if run
            else []
        )
        valid = (
            valid_source
            and len(matches) == 1
            and matches[0][0] == version
            and matches[0][1].get("version") in (None, version)
        )
        current_ids = (
            [item for item in run.versions if item.get("text_id") == run.text_id]
            if run and run.text_id
            else []
        )
        status = (
            "current"
            if valid and len(current_ids) == 1 and run.text_id == str(text_id)
            else "historical"
            if valid and len(current_ids) == 1
            else "unavailable"
        )
        info = {
            "status": status,
            "run_id": str(run_id),
            "text_id": str(text_id),
            "version": version,
        }
        original = by_original.get(run.source_id) if run and valid else None
        if segment.locator.kind == "text" and original and original.sha256 == run.source_sha256:
            pair = run.request.get("paired_selection")
            if not pair:
                info["case"] = {
                    "sample_id": str(original.id),
                    "sample_collection_id": str(original.id),
                    "sample_title": original.original_filename,
                }
            elif (
                pair.get("collection_id")
                and pair.get("sample_id")
                and (
                    (pair.get("audio_id"), pair.get("audio_sha256"))
                    == (str(original.id), original.sha256)
                    or (pair.get("video_id"), pair.get("video_sha256"))
                    == (str(original.id), original.sha256)
                )
            ):
                info["case"] = {
                    "sample_id": pair["sample_id"],
                    "sample_collection_id": pair["collection_id"],
                    "sample_title": original.original_filename,
                }
        details[str(segment.id)] = info
    return details


async def with_transcription_status(db, project, user, data):
    document = CodingDocument.model_validate(data["payload"])
    data["local_coders"] = local_coders(document, user)
    data["current_coder"] = current_coder(document, user)
    details = await transcription_details(db, project, user, document)
    data["transcription_status"] = {key: item["status"] for key, item in details.items()}
    data["transcription_info"] = details
    # Expose the immutable transcript's case at read time. Explicit case choices win.
    for segment in data["payload"].get("segments", []):
        case = details.get(str(segment["id"]), {}).get("case")
        if case and not segment.get("sample_collection_id"):
            segment.update(case)
    data.update(
        await coding_cases(db, project, CodingDocument.model_validate(data["payload"]).segments)
    )
    return data


async def read(db, project, user):
    row = await current(db, project, user)
    if row is None:
        return await with_transcription_status(
            db,
            project,
            user,
            {
                "id": None,
                "project_id": str(project),
                "revision": 0,
                "payload": CodingDocument().model_dump(mode="json"),
            },
        )
    return await with_transcription_status(db, project, user, await read_record(db, row.id, user))


async def summary_snapshot(db, project, user, dataset_id):
    from app.services import dataset_service

    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    meta = json.loads(dataset.preview_json or "{}").get("coding_summary") or {}
    if (
        dataset.project_id != project
        or not meta.get("coding_id")
        or not meta.get("coding_revision")
    ):
        raise fail("此结果没有可读取的编码快照。", 404)
    record = await current(db, project, user)
    if not record or str(record.id) != meta["coding_id"]:
        raise fail("编码快照不可用。", 404)
    data = await read_record(db, record.id, user, int(meta["coding_revision"]))
    return await with_transcription_status(db, project, user, data)


def segment_from_citation(citation, themes, memo="", identity=None, origin=None):
    return CodedSegment(
        id=identity or uuid.uuid4(),
        origin=origin,
        theme_ids=themes,
        memo=memo,
        citation_id=citation["id"],
        citation_revision=citation["revision"],
        citation_sha256=citation["citation_sha256"],
        material_id=citation["material_id"],
        material_sha256=citation["material_sha256"],
        material_version=citation["material_version"],
        filename=citation["filename"],
        locator=citation["locator"],
    )


def local_coders(document, user):
    return [
        {"id": str(user.id), "name": user.display_name or "本地研究者"},
        *[item.model_dump() for item in document.local_coders],
    ]


def current_coder(document, user):
    identity = document.active_coder_id or str(user.id)
    coder = next((c for c in local_coders(document, user) if c["id"] == identity), None)
    if not coder:
        raise fail("当前编码者不可用，请重新选择。", 422)
    return coder


async def selected_segment(db, project, user, body, previous=None, coder=None):
    """All manual selectors use the same versioned coding document."""
    if body.task_id:
        from app.services.project_assistant_tasks import context

        await context(db, project, user, str(body.task_id))
    if body.locator is None:
        raise fail("请先选择需要标注的内容。", 422)
    if body.locator.kind == "signal_time":
        from app.services import dataset_service, physiocgm

        if not body.dataset_id or not body.dataset_sha256:
            raise fail("请重新打开信号并选择时间段。", 422)
        source = await dataset_service.get_dataset_with_access(db, body.dataset_id, user.id)
        if (
            source.project_id != project
            or not source.signal_session
            or source.sha256 != body.dataset_sha256
        ):
            raise fail("信号版本已变化，请重新打开。")
        saved = (
            json.loads(source.preview_json or "{}")
            .get("signal_options", {})
            .get(str(body.task_id or "project"), {})
        )
        options = physiocgm.Options.model_validate(
            {
                **saved,
                "start": body.locator.start_seconds,
                "end": body.locator.end_seconds,
                "window": body.locator.window_index,
            }
        )
        signature = await anyio.to_thread.run_sync(physiocgm.source_fingerprint, source, options)
        if body.source_fingerprint != signature:
            raise fail("原文件已变化，请重新读取信号后标注。")
        view = await anyio.to_thread.run_sync(physiocgm.source_preview, source, options)
        channels = {track["id"] for track in view["tracks"]}
        if view.get("cgm"):
            channels.add("cgm")
        if (
            body.locator.start_seconds < view["range"][0]
            or body.locator.end_seconds > view["range"][1]
            or not set(body.locator.channel_ids) <= channels
        ):
            raise fail("选段或通道不在当前信号范围内，请重新选择。", 422)
        origin = (
            "mixed"
            if previous and previous.origin == "ai_accepted"
            else (previous.origin if previous else "manual")
        )
        return CodedSegment(
            id=previous.id if previous else uuid.uuid4(),
            origin=origin,
            theme_ids=body.theme_ids,
            coder_id=previous.coder_id if previous else (coder or {"id": str(user.id)})["id"],
            coder_name=previous.coder_name
            if previous
            else (coder or {"name": user.display_name or "本地研究者"})["name"],
            memo=body.memo,
            task_id=body.task_id,
            dataset_id=source.id,
            dataset_sha256=source.sha256,
            source_fingerprint=signature,
            filename=source.original_filename,
            locator=body.locator,
        )
    if not body.material_id or not body.material_sha256 or not body.material_version:
        raise fail("请重新打开文件并选择要标注的内容。", 422)
    material = await material_service.load(db, body.material_id, user.id, project)
    request = CitationRequest(
        material_sha256=body.material_sha256,
        material_version=body.material_version,
        locator=body.locator,
        reviewed=True,
    )
    await material_service.verify_version(material)
    material_service.validate_locator(material, request)
    from app.services import coding_suggestions

    if body.locator.kind == "time" and body.locator.transcription:
        await coding_suggestions.transcript_excerpt(db, project, user, material, body.locator)
    proposed = coding_suggestions.adopted(user.id, project, body)
    citation = await material_service.save_citation(db, material.id, user.id, request)
    origin = (
        "mixed"
        if previous and previous.origin == "ai_accepted"
        else (previous.origin if previous else "manual")
    )
    if proposed:
        if previous and previous.origin is None:
            origin = "legacy_ai"
        elif previous or {str(value) for value in body.theme_ids} - proposed or body.memo.strip():
            origin = "mixed"
        else:
            origin = "ai_accepted"
    segment = segment_from_citation(
        citation, body.theme_ids, body.memo, previous.id if previous else None, origin
    )
    segment.coder_id = previous.coder_id if previous else (coder or {"id": str(user.id)})["id"]
    segment.coder_name = (
        previous.coder_name
        if previous
        else (coder or {"name": user.display_name or "本地研究者"})["name"]
    )
    segment.task_id = body.task_id
    if previous and previous.sample_collection_id and not body.sample_collection_id:
        segment.sample_id = previous.sample_id
        segment.sample_title = previous.sample_title
        segment.sample_collection_id = previous.sample_collection_id
    elif body.sample_id or body.sample_collection_id:
        from app.services import dataset_service, multimodal_cases

        if not body.sample_id or not body.sample_collection_id:
            raise fail("请重新选择样本后标注。", 422)
        collection = await dataset_service.get_dataset_with_access(
            db, body.sample_collection_id, user.id
        )
        if collection.project_id != project:
            raise fail("请选择当前项目中的样本。", 422)
        if collection.file_format.lower() in {"wav", "mp4"} and body.sample_id == str(
            collection.id
        ):
            key = "audio" if collection.file_format.lower() == "wav" else "video_av"
            field = "audio_id" if key == "audio" else "video_id"
            sample = {
                "sample_id": str(collection.id),
                "title": collection.original_filename,
                field: str(collection.id),
                "files": {key: {"sha256": collection.sha256}},
            }
        else:
            collection, root, descriptor = await multimodal_cases.collection(
                db, user, body.sample_collection_id
            )
            samples = (
                descriptor["samples"]
                if descriptor["kind"] == "media"
                else await multimodal_cases.linked_samples(db, collection, root, descriptor)
            )
            sample = next(
                (item for item in samples if str(item.get("sample_id")) == body.sample_id), None
            )
        linked = False
        source_id = json.loads(material.metadata_json or "{}").get("source_dataset_id")
        for key, field in (
            ("audio", "audio_id"),
            ("video_av", "video_id"),
            ("text", "text_id"),
            ("image_path", "image_dataset_id"),
        ):
            identity = (sample or {}).get(field)
            if not identity:
                continue
            member = await dataset_service.get_dataset_with_access(db, uuid.UUID(identity), user.id)
            pinned = sample.get("files", {}).get(key, {}).get("sha256")
            if (
                member.project_id != project
                or member.sha256 != material.sha256
                or (pinned and member.sha256 != pinned)
            ):
                continue
            # Older materials store the explicit link only on the dataset.
            if source_id == identity or json.loads(member.preview_json or "{}").get(
                "material_id"
            ) == str(material.id):
                linked = True
                break
        if not linked:
            for item in (sample or {}).get("members", []):
                try:
                    identity = str(uuid.UUID(item["id"]))
                    pinned = item["sha256"]
                except (ValueError, TypeError, KeyError):
                    continue
                member = await dataset_service.get_dataset_with_access(
                    db, uuid.UUID(identity), user.id
                )
                if (
                    member.project_id == project
                    and member.sha256 == pinned == material.sha256
                    and (
                        source_id == identity
                        or json.loads(member.preview_json or "{}").get("material_id")
                        == str(material.id)
                    )
                ):
                    linked = True
                    break
        if not linked:
            raise fail("材料与选定样本的关联已变化，请重新选择。", 422)
        segment.sample_id = body.sample_id
        segment.sample_title = str(sample.get("title") or body.sample_id)[:255]
        segment.sample_collection_id = collection.id
    return segment


async def apply(db, project, user, body: CodingChange):
    await acquire_write_fence(db, user.id)
    row = await current(db, project, user)
    revision = row.revision if row else 0
    if revision != body.revision:
        raise fail("编码已在另一处修改，请刷新后再试。")
    document = CodingDocument.model_validate_json(row.payload_json) if row else CodingDocument()
    old_revision = revision
    themes = {item.id: item for item in document.themes}
    segments = {item.id: item for item in document.segments}
    operation = body.operation
    if operation == "question":
        document.ai_question = body.question
    elif operation == "add_coder":
        name = body.name.strip()
        if not name or any(
            c["name"].casefold() == name.casefold() for c in local_coders(document, user)
        ):
            raise fail("请填写不同的编码者名称。", 422)
        coder = LocalCoder(id=str(uuid.uuid4()), name=name)
        document.local_coders.append(coder)
        document.active_coder_id = coder.id
    elif operation == "select_coder":
        if body.coder_id not in {c["id"] for c in local_coders(document, user)}:
            raise fail("请选择已有的本地编码者。", 422)
        document.active_coder_id = body.coder_id
    elif operation == "undo":
        if not row or not document.undo_stack:
            raise fail("没有可撤销的修改。")
        stack = document.undo_stack[:-1]
        target = document.undo_stack[-1]
        if target == 0:
            document = CodingDocument()
        else:
            raw = await db.scalar(
                select(WorkspaceRevision.payload_json).where(
                    WorkspaceRevision.record_id == row.id,
                    WorkspaceRevision.revision == target,
                )
            )
            if raw is None:
                raise fail("上一版本暂时无法读取。")
            document = CodingDocument.model_validate_json(raw)
        latest = CodingDocument.model_validate_json(row.payload_json)
        document.ai_question = latest.ai_question
        document.local_coders = latest.local_coders
        document.active_coder_id = latest.active_coder_id
        document.undo_stack = stack
    else:
        if operation == "bind_attributes":
            from app.schemas.coding_attributes import AttributeBinding
            from app.services import coding_attributes

            if body.attribute_binding is None:
                raise fail("请选择属性表和关联字段。", 422)
            preview = await coding_attributes.preview_binding(
                db, project, user, body.attribute_binding
            )
            if preview["unmatched"] or not preview["matched_count"]:
                raise fail("还有未关联的表格行，请指定对应文件或调整关联字段。", 422)
            binding = AttributeBinding.model_validate(preview["binding"])
            document.attribute_bindings = [
                b for b in document.attribute_bindings if b.id != binding.id
            ] + [binding]
            coding_attributes.validated_conditions(document)
        elif operation == "filter_attributes":
            from app.services import coding_attributes

            if body.attribute_filter is None:
                raise fail("请设置筛选条件。", 422)
            document.attribute_filter = body.attribute_filter
            coding_attributes.validated_conditions(document)
        if (
            operation in {"edit_theme", "merge_theme", "delete_theme", "move_theme"}
            and body.theme_id not in themes
        ):
            raise fail("主题已变化，请重新选择。")
        if operation in {"add_theme", "edit_theme"}:
            if not body.name.strip():
                raise fail("请填写主题名称。", 422)
            identity = body.theme_id if operation == "edit_theme" else uuid.uuid4()
            previous = themes.get(identity)
            is_codable = (
                body.is_codable
                if body.is_codable is not None
                else (previous.is_codable if previous else True)
            )
            if not is_codable and any(identity in s.theme_ids for s in segments.values()):
                raise fail("此标签已有标注，不能改为分组。", 422)
            rgb = (
                body.color_rgb
                if "color_rgb" in body.model_fields_set
                else (previous.color_rgb if previous and previous.color == body.color else None)
            )
            themes[identity] = Theme(
                id=identity,
                name=body.name.strip(),
                color=body.color,
                parent_id=body.parent_id,
                memo=body.memo,
                color_rgb=rgb,
                is_codable=is_codable,
                merged_ids=previous.merged_ids if previous else [],
            )
        elif operation == "move_theme":
            selected = themes[body.theme_id]
            siblings = [
                identity
                for identity, item in themes.items()
                if item.parent_id == selected.parent_id
            ]
            index = siblings.index(selected.id)
            other = index + (-1 if body.direction == "up" else 1)
            if body.direction is None or other < 0 or other >= len(siblings):
                raise fail("此位置不能继续移动。", 422)
            order = list(themes)
            left, right = order.index(selected.id), order.index(siblings[other])
            order[left], order[right] = order[right], order[left]
            themes = {identity: themes[identity] for identity in order}
        elif operation == "import_codebook":
            from app.services import refi_codebook

            imported = refi_codebook.parse(body.codebook_xml)
            combined, added = refi_codebook.combine(document.themes, imported)
            import_notice = {"added": added, "existing": len(imported) - added}
            if not added:
                saved = await read(db, project, user)
                return {**saved, "codebook_import": import_notice}
            themes = {item.id: item for item in combined}
        elif operation == "merge_theme":
            if body.target_id not in themes or body.target_id == body.theme_id:
                raise fail("请选择另一个主题。", 422)
            if not themes[body.target_id].is_codable and any(
                body.theme_id in s.theme_ids for s in segments.values()
            ):
                raise fail("来源标签已有标注，请合并到可编码标签。", 422)
            parent = body.target_id
            while parent:
                if parent == body.theme_id:
                    raise fail("不能合并到自己的下级，请选择其他标签。", 422)
                parent = themes[parent].parent_id
            for theme in themes.values():
                if theme.parent_id == body.theme_id:
                    theme.parent_id = body.target_id
            for segment in segments.values():
                segment.theme_ids = list(
                    dict.fromkeys(
                        body.target_id if value == body.theme_id else value
                        for value in segment.theme_ids
                    )
                )
            themes[body.target_id].merged_ids = list(
                dict.fromkeys(
                    [
                        *themes[body.target_id].merged_ids,
                        body.theme_id,
                        *themes[body.theme_id].merged_ids,
                    ]
                )
            )
            del themes[body.theme_id]
        elif operation == "delete_theme":
            if any(body.theme_id in s.theme_ids for s in segments.values()) or any(
                t.parent_id == body.theme_id for t in themes.values()
            ):
                raise fail("主题仍有编码或子主题，请先合并或移出。")
            del themes[body.theme_id]
        elif operation == "add_segment":
            coder = current_coder(document, user)
            if not set(body.theme_ids) <= themes.keys() or (
                not body.theme_ids and not body.memo.strip()
            ):
                raise fail("请选择标签或填写备注。", 422)
            if any(not themes[identity].is_codable for identity in body.theme_ids):
                raise fail("分组只用于组织标签，请选择其中的标签进行标注。", 422)
            existing = next(
                (
                    s
                    for s in segments.values()
                    if s.coder_id == coder["id"]
                    and s.material_id == body.material_id
                    and s.dataset_id == body.dataset_id
                    and s.task_id == body.task_id
                    and s.sample_id == body.sample_id
                    and s.sample_collection_id == body.sample_collection_id
                    and s.locator == body.locator
                ),
                None,
            )
            segment = await selected_segment(db, project, user, body, existing, coder)
            if existing:
                segment.theme_ids = list(dict.fromkeys([*existing.theme_ids, *body.theme_ids]))
                segment.memo = body.memo or existing.memo
            segments[segment.id] = segment
        elif operation in {"edit_segment", "remove_segment"}:
            if body.segment_id not in segments:
                raise fail("编码已变化，请重新选择。")
            if operation == "remove_segment":
                del segments[body.segment_id]
            else:
                if any(
                    identity not in themes or not themes[identity].is_codable
                    for identity in body.theme_ids
                ):
                    raise fail("请选择可编码标签，分组不能用于标注。", 422)
                previous = segments[body.segment_id]
                if body.locator is not None:
                    request = body.model_copy(
                        update={
                            "material_id": previous.material_id,
                            "material_sha256": previous.material_sha256,
                            "material_version": previous.material_version,
                            "dataset_id": previous.dataset_id,
                            "dataset_sha256": previous.dataset_sha256,
                            "source_fingerprint": body.source_fingerprint
                            or previous.source_fingerprint,
                            "task_id": previous.task_id,
                        }
                    )
                    segments[body.segment_id] = await selected_segment(
                        db, project, user, request, previous
                    )
                else:
                    previous.theme_ids = body.theme_ids
                    previous.memo = body.memo
                    if previous.origin == "ai_accepted":
                        previous.origin = "mixed"
        document.themes = list(themes.values())
        document.segments = list(segments.values())
        document.undo_stack = [*document.undo_stack, old_revision][-100:]
    try:
        document = CodingDocument.model_validate(document.model_dump())
    except ValueError:
        raise fail("标签层级形成循环或标注引用无效，请调整上级或所选标签。", 422) from None
    if len(document.model_dump_json().encode()) > 4 * 1024 * 1024:
        raise fail("编码内容超过 4 MB，请拆分项目。", 422)
    saved = await save_record(
        db,
        user,
        SaveRecord(project_id=project, expected_revision=revision, payload=document),
        row.id if row else None,
        coding_write=True,
    )
    result = await with_transcription_status(db, project, user, saved)
    if operation == "import_codebook":
        result["codebook_import"] = import_notice
    return result


async def collection_groups(db, project, segments):
    from app.models.dataset import Dataset

    ids = {segment.sample_collection_id for segment in segments if segment.sample_collection_id}
    rows = (
        (
            await db.scalars(
                select(Dataset).where(Dataset.project_id == project, Dataset.id.in_(ids))
            )
        ).all()
        if ids
        else []
    )
    return {
        str(item.id): str(json.loads(item.preview_json or "{}").get("media_group_id") or item.id)
        for item in rows
    }


async def coding_cases(db, project, segments):
    """Read-only case choices from actual annotations; group identity spans versions."""
    from app.models.dataset import Dataset

    ids = {s.sample_collection_id for s in segments if s.sample_collection_id and s.sample_id}
    rows = (
        (
            await db.scalars(
                select(Dataset).where(Dataset.project_id == project, Dataset.id.in_(ids))
            )
        ).all()
        if ids
        else []
    )
    collections = {str(row.id): row for row in rows}
    cases, segment_cases = {}, {}
    from app.services.survey_import import collections as survey_collections

    for group, collection in (await survey_collections(db, project)).items():
        for sample in collection.media_case["samples"]:
            key = group + ":" + sample["sample_id"]
            cases[key] = {
                "key": key,
                "group_id": group,
                "collection_id": str(collection.id),
                "sample_id": sample["sample_id"],
                "title": sample["sample_id"],
                "group_title": collection.display_name or collection.original_filename,
            }
    for segment in segments:
        collection = collections.get(str(segment.sample_collection_id))
        if not collection or not segment.sample_id:
            continue
        meta = json.loads(collection.preview_json or "{}")
        group = str(meta.get("media_group_id") or collection.id)
        key = f"{group}:{segment.sample_id}"
        segment_cases[str(segment.id)] = key
        if key not in cases:
            cases[key] = {
                "key": key,
                "group_id": group,
                "collection_id": str(collection.id),
                "sample_id": segment.sample_id,
                "title": segment.sample_title or segment.filename,
                "group_title": meta.get("display_name") or collection.original_filename,
            }
    return {"case_scopes": list(cases.values()), "segment_cases": segment_cases}


async def selected_case(db, project, user, data, collection_id=None, sample_id=None):
    if collection_id is None and sample_id is None:
        return None
    if not collection_id or not sample_id:
        raise fail("请重新选择案例。", 422)
    from app.services import dataset_service

    collection = await dataset_service.get_dataset_with_access(db, collection_id, user.id)
    if collection.project_id != project:
        raise fail("此案例不属于当前项目。", 422)
    group = str(json.loads(collection.preview_json or "{}").get("media_group_id") or collection.id)
    case = next(
        (
            case
            for case in data.get("case_scopes", [])
            if case["group_id"] == group and case["sample_id"] == sample_id
        ),
        None,
    )
    if not case:
        raise fail("此案例没有可读取的标注，请重新选择。", 422)
    return case


async def export(db, project, user, format, case_collection_id=None, case_sample_id=None):
    data = await read(db, project, user)
    if format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json"
    document = CodingDocument.model_validate(data["payload"])
    themes = {item.id: item.name for item in document.themes}
    statuses = data.get("transcription_status", {})
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    if format == "summary_csv":
        from app.services.coding_attributes import scoped

        scope = await selected_case(db, project, user, data, case_collection_id, case_sample_id)
        applied = scoped(data, document, scope)
        scope_label = applied["scope_label"]
        eligible_ids = set(applied["current_segment_ids"])
        writer.writerow(["范围", "主题", "样本", "文件", "内容类型", "研究任务", "标注数"])
        counts = {}
        group_by_collection = await collection_groups(db, project, document.segments)
        for segment in document.segments:
            if str(segment.id) not in eligible_ids:
                continue
            for theme in segment.theme_ids:
                collection_id = str(segment.sample_collection_id or "")
                group_id = group_by_collection.get(collection_id, collection_id)
                key = (
                    theme,
                    group_id,
                    segment.sample_id,
                    segment.material_id or segment.dataset_id,
                    segment.task_id,
                    segment.locator.kind,
                )
                if key not in counts:
                    counts[key] = [segment.sample_title or "", segment.filename, 0]
                counts[key][2] += 1
        for (theme, _collection, _sample, _source, task, kind), (
            sample_title,
            filename,
            count,
        ) in counts.items():

            def safe_cell(value):
                value = str(value)
                return (
                    "'" + value
                    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                    else value
                )

            writer.writerow(
                [
                    safe_cell(scope_label),
                    safe_cell(themes.get(theme, "")),
                    safe_cell(sample_title),
                    safe_cell(filename),
                    {
                        "text": "文字",
                        "region": "图像区域",
                        "time": "媒体时间段",
                        "signal_time": "波形时间段",
                    }[kind],
                    str(task or ""),
                    count,
                ]
            )
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"
    writer.writerow(
        [
            "标签",
            "案例",
            "案例集合ID",
            "案例ID",
            "内容类型",
            "原文",
            "备注",
            "文件",
            "页码",
            "起点",
            "终点",
            "区域X",
            "区域Y",
            "区域宽",
            "区域高",
            "通道",
            "窗口",
            "研究任务",
            "标注来源",
            "来源SHA256",
            "转录记录ID",
            "转录文字ID",
            "转录版本",
            "版本状态",
        ]
    )

    def cell(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value

    for segment in document.segments:
        locator = segment.locator.model_dump()
        transcript = (
            data.get("transcription_info", {}).get(str(segment.id))
            or locator.get("transcription")
            or {}
        )
        status = {"current": "当前", "historical": "历史", "unavailable": "版本不可核验"}.get(
            statuses.get(str(segment.id)), "不适用"
        )
        for theme in segment.theme_ids or [None]:
            writer.writerow(
                [
                    cell(themes.get(theme, "")),
                    cell(segment.sample_title or ""),
                    str(segment.sample_collection_id or ""),
                    cell(segment.sample_id or ""),
                    {
                        "text": "文字",
                        "region": "图像区域",
                        "time": "媒体时间段",
                        "signal_time": "波形时间段",
                    }[locator["kind"]],
                    cell(
                        locator.get("quote")
                        or (locator.get("transcription") or {}).get("quote", "")
                    ),
                    cell(segment.memo),
                    cell(segment.filename),
                    locator.get("page", ""),
                    locator.get("start", locator.get("start_seconds", "")),
                    locator.get("end", locator.get("end_seconds", "")),
                    locator.get("x", ""),
                    locator.get("y", ""),
                    locator.get("width", ""),
                    locator.get("height", ""),
                    cell(" / ".join(locator.get("channel_ids", []))),
                    locator.get("window_index", ""),
                    str(segment.task_id or ""),
                    {
                        "manual": "人工",
                        "ai_accepted": "AI建议已采用",
                        "mixed": "人工与AI",
                        "legacy_ai": "历史标注与AI建议",
                    }.get(segment.origin, "历史标注"),
                    segment.source_fingerprint or segment.material_sha256 or segment.dataset_sha256,
                    str(transcript.get("run_id", "")),
                    str(transcript.get("text_id", "")),
                    transcript.get("version", ""),
                    status,
                ]
            )
    return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"


async def summary_dataset(
    db, project, user, revision, task_id=None, case_collection_id=None, case_sample_id=None
):
    """Create a sample-level analysis table from explicitly associated annotations."""
    from app.services import dataset_service, project_assistant_tasks

    await acquire_write_fence(db, user.id)
    row = await current(db, project, user)
    if row is None or row.revision != revision:
        raise fail("标注已变化，请刷新后重新生成汇总。")
    if task_id:
        await project_assistant_tasks.context(db, project, user, str(task_id))
    view = await with_transcription_status(
        db, project, user, {"payload": json.loads(row.payload_json)}
    )
    document = CodingDocument.model_validate(view["payload"])
    scope = await selected_case(db, project, user, view, case_collection_id, case_sample_id)
    from app.services.coding_attributes import scoped

    applied = scoped(view, document, scope)
    eligible_ids = set(applied["segment_ids"])
    eligible = [segment for segment in document.segments if str(segment.id) in eligible_ids]
    statuses = view["transcription_status"]
    segments = [
        segment for segment in eligible if statuses.get(str(segment.id), "current") == "current"
    ]
    excluded = len(eligible) - len(segments)
    associated = [
        segment for segment in segments if segment.sample_id and segment.sample_collection_id
    ]
    skipped = len(segments) - len(associated)
    if not associated:
        raise fail("当前范围没有可汇总的案例标注，请调整筛选或案例范围。", 422)

    themes = {theme.id: theme for theme in document.themes}
    relevant_theme_ids = {identity for segment in associated for identity in segment.theme_ids}
    theme_columns = {}
    used_columns = {"case_key", "sample_id", "sample_collection_id", "样本", "标注数"}
    for theme in document.themes:
        if theme.id not in relevant_theme_ids:
            continue
        path = [theme.name]
        parent = theme.parent_id
        while parent:
            ancestor = themes[parent]
            path.insert(0, ancestor.name)
            parent = ancestor.parent_id
        base = "主题 · " + " / ".join(path)
        label = base
        suffix = 2
        while label in used_columns:
            label = f"{base} ({suffix})"
            suffix += 1
        used_columns.add(label)
        theme_columns[theme.id] = label

    # Case identity spans revisions of the same media group, but never distinct groups.
    collection_ids = {segment.sample_collection_id for segment in associated}
    group_by_collection = await collection_groups(db, project, associated)
    groups = {}
    case_keys = {}
    for segment in associated:
        sample_id = segment.sample_id
        collection_id = str(segment.sample_collection_id)
        group_id = group_by_collection.get(collection_id, collection_id)
        case_key = f"{group_id}:{sample_id}"
        case_keys[str(segment.id)] = case_key
        if case_key not in groups:
            groups[case_key] = {
                "case_key": case_key,
                "sample_id": sample_id,
                "sample_collection_id": collection_id,
                "样本": segment.sample_title or "",
                "标注数": 0,
                **{label: 0 for label in theme_columns.values()},
            }
        values = groups[case_key]
        values["标注数"] += 1
        for theme_id in segment.theme_ids:
            values[theme_columns[theme_id]] += 1

    import re

    import polars as pl

    scope_label = applied["scope_label"]
    display_name = f"样本主题汇总 · {scope['title'] if scope else '全项目'}" + (
        " · 属性筛选" if applied["description"] else ""
    )
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", display_name)[:160] + ".parquet"
    with tempfile.TemporaryDirectory(prefix="sf-coding-summary-") as directory:
        path = Path(directory) / "summary.parquet"
        frame = pl.DataFrame(
            list(groups.values()),
            schema_overrides={
                "case_key": pl.String,
                "sample_id": pl.String,
                "sample_collection_id": pl.String,
                "样本": pl.String,
            },
        )
        frame.write_parquet(path)
        dataset = await dataset_service.upload_dataset(
            db, project, user.id, str(path), filename, allow_duplicate=True
        )
    meta = json.loads(dataset.preview_json or "{}")
    meta["display_name"] = display_name
    if len(collection_ids) == 1:
        meta["source_dataset_id"] = str(next(iter(collection_ids)))
    meta["coding_summary"] = {
        "coding_id": str(row.id),
        "coding_revision": row.revision,
        "task_id": str(task_id) if task_id else None,
        "theme_columns": [
            {"theme_id": str(identity), "column": label}
            for identity, label in theme_columns.items()
        ],
        "sample_segments": [
            {
                "sample_id": segment.sample_id,
                "sample_collection_id": str(segment.sample_collection_id),
                "case_key": case_keys[str(segment.id)],
                "segment_id": str(segment.id),
                "theme_ids": [str(identity) for identity in segment.theme_ids],
                "material_id": str(segment.material_id) if segment.material_id else None,
                "dataset_id": str(segment.dataset_id) if segment.dataset_id else None,
                "filename": segment.filename,
                "material_sha256": segment.material_sha256,
                "material_version": segment.material_version,
                "transcription_version": view["transcription_info"].get(str(segment.id)),
                "locator": segment.locator.model_dump(mode="json"),
            }
            for segment in associated
        ],
        "unassociated_segments": skipped,
        "excluded_transcription_segments": excluded,
        "version_scope": "current_transcription_versions",
        "case_scope": scope,
        "scope_label": scope_label,
        "attribute_scope": applied,
    }
    dataset.preview_json = json.dumps(meta, ensure_ascii=False)
    if task_id:
        live = SimpleNamespace(project=project, value={"task_context": {"id": str(task_id)}})
        await project_assistant_tasks.append(db, user, live, "dataset_ids", dataset.id)
    await db.commit()
    return {
        "id": str(dataset.id),
        "sample_count": len(groups),
        "unassociated_segments": skipped,
        "excluded_transcription_segments": excluded,
        "coding_revision": row.revision,
        "scope_label": scope_label,
    }


def advice_ranges(source, proposal):
    from app.schemas.material import TextLocation

    sources = {item["key"]: item for item in source["context"]["sources"]}
    for index, proposed in enumerate(proposal["segments"]):
        selected = sources[proposed["source_key"]]
        locator = TextLocation(
            kind="text",
            page=selected["page"],
            start=proposed["start"],
            end=proposed["end"],
            quote=proposed["quote"],
            text_sha256=selected["text_sha256"],
        )
        yield index, proposed, selected, locator


def merge_advice(document, source, proposal, request_id, citations):
    """The write and historical proof use the exact same deterministic edit."""
    document = document.model_copy(deep=True)
    keys = {str(theme.id): theme.id for theme in document.themes}
    for proposed in proposal["themes"]:
        identity = uuid.uuid5(request_id, "theme:" + proposed["key"])
        keys[proposed["key"]] = identity
        document.themes.append(
            Theme(
                id=identity,
                name=proposed["name"].strip(),
                color=proposed["color"],
                memo=proposed["memo"],
            )
        )
    for index, proposed, selected, locator in advice_ranges(source, proposal):
        themes = [keys[key] for key in proposed["theme_keys"]]
        existing = next(
            (
                segment
                for segment in document.segments
                if str(segment.material_id) == selected["material_id"]
                and segment.locator == locator
            ),
            None,
        )
        if existing:
            if source["context"].get("coding_origin_version") == 1:
                existing.origin = (
                    "legacy_ai"
                    if existing.origin in {None, "legacy_ai"}
                    else ("mixed" if existing.origin in {"manual", "mixed"} else "ai_accepted")
                )
            existing.theme_ids = list(dict.fromkeys([*existing.theme_ids, *themes]))
            if proposed["memo"]:
                existing.memo = proposed["memo"]
        else:
            if index not in citations:
                raise fail()
            document.segments.append(
                segment_from_citation(
                    citations[index],
                    themes,
                    proposed["memo"],
                    uuid.uuid5(request_id, f"segment:{index}"),
                    "ai_accepted" if source["context"].get("coding_origin_version") == 1 else None,
                )
            )
    document.undo_stack = [*document.undo_stack, source["target"]["revision"]][-100:]
    document = CodingDocument.model_validate(document.model_dump())
    if len(document.model_dump_json().encode()) > 4 * 1024 * 1024:
        raise fail("编码内容超过 4 MB，请拆分项目。", 422)
    return document


async def prove_advice(db, owner, record, saved, source, proposal, request_id):
    """Verify accepted themes, ranges, citations, memos and preserved old content."""
    from app.research.contracts import canonical_bytes

    revision = source["target"]["revision"]
    if saved.revision != revision + 1:
        raise fail()
    if revision:
        raw = await db.scalar(
            select(WorkspaceRevision.payload_json).where(
                WorkspaceRevision.record_id == record.id,
                WorkspaceRevision.revision == revision,
            )
        )
        if raw is None:
            raise fail()
        original = CodingDocument.model_validate_json(raw)
    else:
        original = CodingDocument()
    actual = CodingDocument.model_validate_json(saved.payload_json)
    citations = {}
    for index, _proposed, selected, locator in advice_ranges(source, proposal):
        identity = uuid.uuid5(request_id, f"segment:{index}")
        segment = next((item for item in actual.segments if item.id == identity), None)
        if segment is None:
            continue  # A duplicate range is merged into the already existing segment.
        citation, material = await material_service.load_citation(
            db,
            segment.citation_id,
            owner,
            record.project_id,
            uuid.UUID(selected["material_id"]),
        )
        public = material_service.citation_public(citation, material)
        if (
            not citation.reviewed
            or public["material_sha256"] != selected["material_sha256"]
            or public["material_version"] != selected["material_version"]
            or canonical_bytes(public["locator"])
            != canonical_bytes(locator.model_dump(mode="json"))
        ):
            raise fail()
        citations[index] = public
    expected = merge_advice(original, source, proposal, request_id, citations)
    if canonical_bytes(expected.model_dump(mode="json")) != canonical_bytes(
        actual.model_dump(mode="json")
    ):
        raise fail()


async def apply_advice(db, project, user, source, proposal, request_id):
    """One accepted suggestion is one reversible coding revision."""
    await acquire_write_fence(db, user.id)
    row = await current(db, project, user)
    revision = row.revision if row else 0
    if revision != source["target"]["revision"]:
        raise fail("编码已变化，请重新生成建议。")
    document = CodingDocument.model_validate_json(row.payload_json) if row else CodingDocument()
    citations = {}
    for index, _proposed, selected, locator in advice_ranges(source, proposal):
        material = await material_service.load(
            db, uuid.UUID(selected["material_id"]), user.id, project
        )
        request = CitationRequest(
            material_sha256=selected["material_sha256"],
            material_version=selected["material_version"],
            locator=locator,
            reviewed=True,
        )
        await material_service.verify_version(material)
        material_service.validate_locator(material, request)
        if not any(
            segment.material_id == material.id and segment.locator == locator
            for segment in document.segments
        ):
            citations[index] = await material_service.save_citation(
                db, material.id, user.id, request
            )
    document = merge_advice(document, source, proposal, request_id, citations)
    return await save_record(
        db,
        user,
        SaveRecord(project_id=project, expected_revision=revision, payload=document),
        row.id if row else None,
        commit=False,
        coding_write=True,
    )


async def report(db, project, user, segment_id, revision):
    from app.schemas.workspace import ReportBlock, ReportDocument
    from app.services.project_research import source

    await acquire_write_fence(db, user.id)
    row = await current(db, project, user)
    if row is None or row.revision != revision:
        raise fail("编码已变化，请重新选择选段。")
    document = CodingDocument.model_validate_json(row.payload_json)
    segment = next((item for item in document.segments if item.id == segment_id), None)
    if segment is None:
        raise fail("选段已移除，请重新选择。")
    if not segment.citation_id:
        raise fail("此信号标注可从标注面板导出或回看，尚未生成材料摘录。", 422)
    citation, material = await material_service.load_citation(
        db, segment.citation_id, user.id, expected_project_id=project
    )
    if (
        material_service.citation_public(citation, material)["citation_sha256"]
        != segment.citation_sha256
    ):
        raise fail("引用已修改，请先核对原文。")
    await material_service.verify_version(material)
    resolved = await source(
        db, project, user, "citation", segment.citation_id, segment.citation_revision
    )
    names = [item.name for item in document.themes if item.id in segment.theme_ids]
    title = " · ".join(names)[:150] or f"{segment.filename} · 备注"[:150]
    report = ReportDocument(
        title=title,
        blocks=[
            ReportBlock(
                id=uuid.uuid4(),
                title=title,
                body=segment.memo,
                source=resolved["reference"],
            )
        ],
    )
    return await save_record(db, user, SaveRecord(project_id=project, payload=report))
