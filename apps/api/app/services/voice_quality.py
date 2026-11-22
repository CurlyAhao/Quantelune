"""Saved Praat voice measurements using the existing media source and run storage."""

import asyncio
import json
import os
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.models.workspace import WorkspaceRecord
from app.preview.workspace import load_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.voice_quality import VoiceQualityRun
from app.services import dataset_service, local_workspaces, project_sources, storage_service
from app.services.audio_spectrum import source_context
from app.services.multimodal_cases import invalid, put, read_json, read_metadata, runtime_config


async def listing(db, user, dataset, package_id=None, sample_id=None):
    source, clock = await source_context(db, user, dataset, package_id, sample_id)
    rows = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == source.project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "voice_quality",
            )
            .order_by(WorkspaceRecord.created_at.desc())
        )
    ).all()
    return {
        "clock": clock,
        "runs": [
            {"id": str(row.id), **value.model_dump(mode="json")}
            for row in rows
            if (value := VoiceQualityRun.model_validate_json(row.payload_json)).source_dataset_id
            == source.id
            and value.source_sha256 == source.sha256
        ],
    }


async def get(db, user, dataset, identity):
    record = await load_record(db, identity, user, dataset.project_id)
    if record.kind != "voice_quality":
        raise invalid("找不到这次声音质量测量。", 404)
    return VoiceQualityRun.model_validate_json(record.payload_json)


async def detail(db, user, dataset, identity):
    run = await get(db, user, dataset, identity)
    result = {"id": str(identity), **run.model_dump(mode="json")}
    if run.status == "completed":
        data = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(run.artifact_ids["voice-quality.npz"]), user.id
        )
        with np.load(
            storage_service.get_local_object_path(data.object_key), allow_pickle=False
        ) as arrays:
            result["pulse_times_original"] = arrays["pulse_times_original"].tolist()
            result["hnr_times_original"] = arrays["hnr_times_original"].tolist()
            result["hnr_db"] = [float(x) if np.isfinite(x) else None for x in arrays["hnr_db"]]
    return result


async def calculate(db, user, dataset, body):
    source, clock = await source_context(db, user, dataset, body.package_id, body.sample_id)
    project_id = source.project_id
    first, last = body.start, body.end
    if clock["range"] and (first < clock["range"][0] - 0.001 or last > clock["range"][1] + 0.001):
        raise invalid("所选时间超出当前音轨，请重新选段。")
    root = local_workspaces.project_root(project_id)
    if root is None:
        raise invalid("请先打开此材料所在的工作区文件夹。")
    identity = uuid.uuid4()
    output = root / ".statforge" / "storage" / "voice-quality" / identity.hex
    local_workspaces.checked_path(output)
    output.mkdir(parents=True, exist_ok=False)
    config = runtime_config()
    request = {
        **body.model_dump(mode="json"),
        "source": str(storage_service.get_local_object_path(source.object_key)),
        "source_name": source.original_filename,
        "source_sha256": source.sha256,
        "output": str(output),
        "original_start": first + clock["offset"],
        "original_end": last + clock["offset"],
        "audio_origin": clock["audio_origin"],
        "offset": clock["offset"],
        "ffmpeg": config["options"]["ffmpeg_path"],
        "ffprobe": config["options"]["ffprobe_path"],
    }
    request_path = output / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    run = VoiceQualityRun(
        title=f"声音质量 · {first:.3f}–{last:.3f} 秒",
        source_dataset_id=source.id,
        source_sha256=source.sha256,
        context_dataset_id=dataset.id,
        parameters=body.model_dump(mode="json"),
        started_at=datetime.now(UTC).isoformat(),
        artifact_directory=str(output),
    )
    await put(db, user, project_id, identity, run)
    await db.commit()
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            config["python"],
            "-B",
            str(Path(__file__).with_name("voice_quality_worker.py")),
            str(request_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "TEMP": config["temp"],
                "TMP": config["temp"],
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
            },
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        _, stderr = await process.communicate()
        (output / "process.log").write_bytes(stderr)
        if process.returncode:
            failure = (
                read_json(output / "failure.json") if (output / "failure.json").is_file() else {}
            )
            raise invalid(failure.get("message") or "声音质量计算失败，请检查本地 Praat 运行环境。")
        await acquire_write_fence(db, user.id)
        run.method, run.summary = (
            read_json(output / "method.json"),
            read_json(output / "summary.json"),
        )
        for name in (
            "measurement.wav",
            "voice-quality.npz",
            "pulses.csv",
            "hnr.csv",
            "summary.csv",
            "method.json",
        ):
            if name == "summary.csv":
                saved = await dataset_service.upload_dataset(
                    db,
                    project_id,
                    user.id,
                    str(output / name),
                    run.title + ".csv",
                    allow_duplicate=True,
                )
            else:
                saved = await project_sources.store(
                    db,
                    project_id,
                    user.id,
                    str(output / name),
                    name,
                    f"声音质量/{identity.hex}/{name}",
                )
            saved.preview_json = json.dumps(
                {
                    **read_metadata(saved),
                    "source_dataset_id": str(source.id),
                    "display_name": run.title
                    + (" · 摘要" if name == "summary.csv" else " · " + name),
                    "voice_quality_result": {"run_id": str(identity), "kind": name},
                },
                ensure_ascii=False,
            )
            run.artifact_ids[name] = str(saved.id)
        run.status = "completed"
        await put(db, user, project_id, identity, run)
        await db.commit()
    except Exception as error:
        await db.rollback()
        await db.refresh(user)
        await db.refresh(dataset)
        run.status = "failed"
        run.error_message = (
            getattr(error, "detail", None) or "声音质量保存失败，请检查工作区空间后重试。"
        )
        (output / "save-error.log").write_text(str(error), encoding="utf-8")
        await put(db, user, project_id, identity, run)
        await db.commit()
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
    return await detail(db, user, dataset, identity)


async def export(db, user, dataset, identity):
    run = await get(db, user, dataset, identity)
    if run.status != "completed":
        raise invalid("这次测量尚未完成，无法导出。")
    output = Path(run.artifact_directory)
    local_workspaces.checked_path(output)
    path = output / "voice-quality-export.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, item in run.artifact_ids.items():
            data = await dataset_service.get_dataset_with_access(db, uuid.UUID(item), user.id)
            archive.write(storage_service.get_local_object_path(data.object_key), name)
    return path
