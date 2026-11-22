"""Agent entry points for the same local transcription service as the workspace."""

import asyncio
import uuid
from typing import Literal

from app.database import async_session_factory
from app.models.user import User
from app.services import multimodal_cases, storage_service, transcription


def transcription_tools(live):
    from app.research.project_chat_tools import attach_result, json_text, selected_dataset

    async def transcribe_media(
        dataset_id: str = "",
        sample_id: str = "",
        audio_source: Literal["video", "independent"] | None = None,
        language: Literal["auto", "en", "zh"] = "auto",
        start: float | None = None,
        end: float | None = None,
        force: bool = False,
    ) -> str:
        """Transcribe a selected WAV or MP4 locally with Whisper. For the current paired
        sample use the collection ID (or omit dataset_id). To use the saved
        selection, omit audio_source, start and end; only override an option the user
        explicitly changed. start/end are on the video timeline for a pair.
        Independent WAV uses the saved signed offset; provide an end for a paired
        selection. File association or content review does not establish
        synchronization. Result has editable timed text and separate TXT/SRT
        datasets. Existing matching results are reused unless force is true. Never
        claim the model heard audio without this tool.
        """
        task_id = (live.value.get("task_context") or {}).get("id")
        mapped_range = None
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            dataset = await selected_dataset(db, live, dataset_id)
            if dataset.media_result:
                dataset = await selected_dataset(db, live, dataset.media_result["collection_id"])
            # The primary video ID is also the sample ID. Resolving it as a raw MP4
            # would discard the active pair and silently use its embedded soundtrack.
            active_id = live.value.get("dataset_id")
            if active_id and not dataset.media_case and str(dataset.id) != active_id:
                active_dataset = await selected_dataset(db, live, active_id)
                if active_dataset.media_result:
                    active_dataset = await selected_dataset(
                        db, live, active_dataset.media_result["collection_id"]
                    )
                member_id = str(dataset.id)
                members = [
                    item
                    for item in (active_dataset.media_case or {}).get("samples", [])
                    if member_id in (item.get("video_id"), item.get("audio_id"))
                ]
                if members:
                    saved = (
                        multimodal_cases.read_metadata(active_dataset)
                        .get("sample_selection", {})
                        .get(task_id or "project", {})
                    )
                    wanted = sample_id or saved.get("sample_id")
                    member = next((item for item in members if item["sample_id"] == wanted), None)
                    if member is None and len(members) == 1 and not sample_id:
                        member = members[0]
                    if member is None:
                        return json_text(
                            {"status": "needs_input", "reason": "请指定此文件对应的配对样本。"}
                        )
                    dataset = active_dataset
                    sample_id = member["sample_id"]
            if not dataset.media_case and start is None and end is None:
                from app.services.project_assistant import material_selection

                current = material_selection(dataset, str(task_id or "project"))
                start, end = current.get("start"), current.get("end")
            if dataset.media_case:
                info = await multimodal_cases.view(db, user, dataset.id, task_id or "project")
                selected_id = sample_id or info.get("selection", {}).get("sample_id")
                if not selected_id and len(info["samples"]) == 1:
                    selected_id = info["samples"][0]["sample_id"]
                sample = next(
                    (item for item in info["samples"] if item["sample_id"] == selected_id), None
                )
                if not sample:
                    return json_text({"status": "needs_input", "reason": "请选择需要转录的样本。"})
                selection = info.get("selection") or {}
                if audio_source is None and selection.get("sample_id") == selected_id:
                    audio_source = selection.get("audio_source")
                audio_source = audio_source or (
                    "video" if sample.get("video_id") else "independent"
                )
                for file_key, id_key in (("video_av", "video_id"), ("audio", "audio_id")):
                    member_id = sample.get(id_key)
                    pinned = sample.get("files", {}).get(file_key, {}).get("sha256")
                    if member_id and pinned:
                        member = await selected_dataset(db, live, str(member_id))
                        path = storage_service.get_local_object_path(member.object_key).resolve()
                        if (
                            member.sha256 != pinned
                            or not path.is_file()
                            or transcription.path_hash(path) != pinned
                        ):
                            return json_text(
                                {
                                    "status": "needs_input",
                                    "reason": "配对文件已变化，请重新关联材料后转录。",
                                }
                            )
                if start is None and end is None and selection.get("sample_id") == selected_id:
                    start, end = selection.get("start"), selection.get("end")
                video_start, video_end = start or 0, end
                alignment = sample.get("audio_alignment") or {}
                offset = (
                    alignment.get("offset_seconds")
                    if sample.get("video_id") and audio_source == "independent"
                    else None
                )
                if sample.get("video_id") and audio_source == "independent":
                    from app.routers.project_sources import source_material

                    video_info = await source_material(uuid.UUID(sample["video_id"]), db, user)
                    audio_info = (
                        await source_material(uuid.UUID(sample["audio_id"]), db, user)
                        if sample.get("audio_id")
                        else None
                    )
                    if not audio_info:
                        return json_text(
                            {"status": "needs_input", "reason": "当前视频尚未关联独立 WAV。"}
                        )
                    video_duration = video_info["metadata"].get("duration_seconds")
                    audio_duration = audio_info["metadata"].get("duration_seconds")
                    if video_end is None:
                        video_end = video_duration
                    if video_end is None:
                        return json_text(
                            {"status": "needs_input", "reason": "请指定视频选段结束秒数。"}
                        )
                    if video_duration and video_end > video_duration + 0.001:
                        return json_text(
                            {"status": "needs_input", "reason": "选段结束时间超过视频时长。"}
                        )
                    if video_start < 0 or video_end <= video_start:
                        return json_text(
                            {"status": "needs_input", "reason": "请检查视频选段的起止秒数。"}
                        )
                    effective_offset = float(offset) if offset is not None else 0.0
                    start = video_start + effective_offset
                    end = video_end + effective_offset if video_end is not None else None
                    if start < 0 or (audio_duration and end > audio_duration + 0.001):
                        return json_text(
                            {
                                "status": "needs_input",
                                ("reason"): (
                                    "视频选段换算为独立音频时间后超出范围，请调整选段或偏移。"
                                ),
                            }
                        )
                    mapped_range = {
                        "collection_id": str(dataset.id),
                        "sample_id": selected_id,
                        "video_id": str(sample["video_id"]),
                        "video_sha256": video_info["sha256"],
                        "audio_id": str(sample["audio_id"]),
                        "audio_sha256": audio_info["sha256"],
                        "video_start": video_start,
                        "video_end": video_end,
                        "audio_start": start,
                        "audio_end": end,
                        "offset_seconds": offset,
                        "convention": "audio_seconds = video_seconds + offset_seconds",
                        "content_reviewed": bool(alignment.get("content_reviewed", False)),
                    }
                source_id = (
                    sample.get("audio_id")
                    if audio_source == "independent"
                    else sample.get("video_id") or sample.get("audio_id")
                )
                if not source_id:
                    return json_text(
                        {
                            "status": "needs_input",
                            "reason": "所选样本没有对应的音频，请选择可用材料。",
                        }
                    )
                dataset = await selected_dataset(db, live, str(source_id))
            source_id = dataset.id
            source_name = dataset.original_filename
            result = await transcription.start(
                db,
                user,
                dataset,
                transcription.TranscriptionRequest(
                    task_id=task_id,
                    language=language,
                    start=start if start is not None else 0,
                    end=end,
                    force=force,
                ),
                paired_selection=mapped_range,
            )
            run_id = uuid.UUID(result["id"])
        while result["status"] in {"queued", "running"}:
            live.check()
            live.progress(result["progress"] or "正在本地转录…")
            await asyncio.sleep(1)
            async with async_session_factory() as db:
                user = await db.get(User, live.owner)
                source = await selected_dataset(db, live, str(source_id))
                result = await transcription.get(db, user, source, run_id)
        if result["status"] != "completed":
            return json_text(
                {
                    "status": result["status"],
                    "error": result["error_message"],
                    "run_id": result["id"],
                }
            )
        reference = attach_result(live, "dataset", result["text_id"], "转录文字", result["text_id"])
        requested_end = result["request"].get("end")
        actual_end = result["request"].get("actual_end")
        truncated = (
            requested_end is not None
            and actual_end is not None
            and actual_end < requested_end - 0.05
        )
        return json_text(
            {
                "status": "partial" if truncated else "completed",
                "reason": "源音频短于所选范围，转录只覆盖实际范围。" if truncated else None,
                "run_id": result["id"],
                "source_id": str(source_id),
                "source_name": source_name,
                "audio_source": audio_source,
                "segment_count": len(result["segments"]),
                "text_id": result["text_id"],
                "srt_id": result["srt_id"],
                "result": reference,
                "reused": result.get("reused", False),
                "mapped_range": mapped_range,
                "source_range": {
                    "start": result["request"]["start"],
                    "end": result["request"].get("actual_end"),
                },
                "versions": result["versions"],
            }
        )

    async def read_transcription_revision(
        source_id: str, run_id: str, text_id: str, segment_ids: list[int] | None = None
    ) -> str:
        """Open an exact saved transcript revision by original media dataset, run ID and
        immutable TXT dataset ID. Optional segment IDs select timed locations. Return
        only IDs, timestamps and a local result link, never raw transcript text;
        never substitute the current revision.
        """
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            source = await selected_dataset(db, live, source_id)
            revision = await transcription.version(
                db, user, source, uuid.UUID(run_id), uuid.UUID(text_id)
            )
            if not revision["annotatable"]:
                return json_text({"status": "unavailable", "reason": "无法确认转录修订身份。"})
            segments = revision["segments"]
            if segment_ids is not None:
                requested = set(segment_ids)
                segments = [segment for segment in segments if segment["id"] in requested]
                if len(segments) != len(requested):
                    return json_text(
                        {"status": "needs_input", "reason": "转录修订中没有全部所选片段。"}
                    )
            if len(segments) > 100:
                return json_text({"status": "needs_input", "reason": "请指定不超过 100 个片段。"})
            reference = attach_result(
                live, "dataset", text_id, f"转录修订 {revision['version']}", text_id
            )
            positions = [
                {key: segment[key] for key in ("id", "start", "end")} for segment in segments
            ]
            return json_text(
                {
                    "status": "completed",
                    "source_id": source_id,
                    "run_id": run_id,
                    "text_id": text_id,
                    "version": revision["version"],
                    "segments": positions,
                    "result": reference,
                }
            )

    return [transcribe_media, read_transcription_revision]
