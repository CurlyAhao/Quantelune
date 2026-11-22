"""Spectral measurements use original samples and the saved media clock."""

import asyncio
import json
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.models.workspace import WorkspaceRecord
from app.preview.workspace import load_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.audio_spectrum import SpectrumRun
from app.services import dataset_service, local_workspaces, project_sources, storage_service
from app.services.multimodal_cases import invalid, put, read_json, read_metadata, runtime_config


async def source_context(db, user, dataset, package_id=None, sample_id=None):
    from app.services.audio_inspection import _saved_sample

    entry = None
    if package_id:
        package = await dataset_service.get_dataset_with_access(db, package_id, user.id)
        if package.project_id != dataset.project_id:
            raise invalid("当前结果不属于此项目。")
        _, run, entry = await _saved_sample(db, user, package, sample_id)
        if str(dataset.id) not in run["result_ids"]:
            raise invalid("播放器和处理结果不对应，请重新打开当前样本。")
        if dataset.original_filename != Path(entry.get("audio_file") or "").name:
            raise invalid("当前音轨不属于所选样本。")
    elif read_metadata(dataset).get("media_result", {}).get("kind") == "media":
        from app.services.multimodal_cases import get_run

        meta = read_metadata(dataset)["media_result"]
        collection = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(meta["collection_id"]), user.id
        )
        run = await get_run(db, user, collection, uuid.UUID(meta["run_id"]))
        index = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(run["index_id"]), user.id
        )
        matches = [
            e
            for e in read_json(storage_service.get_local_object_path(index.object_key)).get(
                "entries", []
            )
            if Path(e.get("audio_file") or "").name == dataset.original_filename
        ]
        if len(matches) != 1 or str(dataset.id) not in run["result_ids"]:
            raise invalid("找不到派生音轨对应的原声源，请回原录音查看。")
        entry = matches[0]
    if not entry:
        return dataset, {"offset": 0.0, "audio_origin": None, "range": None}
    sample = entry["sample"]
    independent = entry.get("audio_selection") == "independent"
    identity = (
        sample.get("audio_id") if independent else sample.get("video_id") or sample.get("audio_id")
    )
    if not identity:
        raise invalid("旧结果没有可确认的原声源，请回原录音取频谱。")
    original = await dataset_service.get_dataset_with_access(db, uuid.UUID(identity), user.id)
    expected = (sample.get("files", {}).get("audio" if independent else "video_av") or {}).get(
        "sha256"
    )
    if not expected:
        expected = entry.get("praat_audio_source_sha256")
    if original.project_id != dataset.project_id or not expected or original.sha256 != expected:
        raise invalid("原声源版本与此结果不一致，请打开该结果使用的原版本。")
    segment = entry.get("segment") or {}
    return original, {
        "offset": (entry.get("audio_alignment") or {}).get("effective_offset_seconds") or 0.0,
        "audio_origin": entry.get("audio_origin_seconds"),
        "input_origin": segment.get("first_audio_pts_seconds", 0)
        - (entry.get("audio_origin_seconds") or 0),
        "preprocessing": entry.get("audio_preprocessing"),
        "range": [segment["player_start"], segment["player_end"]]
        if "player_end" in segment
        else None,
    }


async def listing(db, user, dataset, package_id=None, sample_id=None):
    source, clock = await source_context(db, user, dataset, package_id, sample_id)
    rows = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == source.project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "audio_spectrum",
            )
            .order_by(WorkspaceRecord.created_at.desc())
        )
    ).all()
    return {
        "clock": clock,
        "runs": [
            {"id": str(row.id), **value.model_dump(mode="json")}
            for row in rows
            if (value := SpectrumRun.model_validate_json(row.payload_json)).source_dataset_id
            == source.id
            and value.source_sha256 == source.sha256
        ],
    }


async def get(db, user, dataset, identity):
    record = await load_record(db, identity, user, dataset.project_id)
    if record.kind != "audio_spectrum":
        raise invalid("找不到这次频谱测量。", 404)
    return SpectrumRun.model_validate_json(record.payload_json)


async def detail(db, user, dataset, identity):
    run = await get(db, user, dataset, identity)
    result = {"id": str(identity), **run.model_dump(mode="json")}
    if run.status == "completed":
        data = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(run.artifact_ids["spectrum.npz"]), user.id
        )
        with np.load(
            storage_service.get_local_object_path(data.object_key), allow_pickle=False
        ) as arrays:
            if "density_pcm2_per_hz" in arrays:
                times, frequencies, values = (
                    arrays["time_seconds"],
                    arrays["frequency_hz"],
                    arrays["density_pcm2_per_hz"],
                )
                ti = np.unique(np.linspace(0, len(times) - 1, min(700, len(times)), dtype=int))
                fi = np.unique(
                    np.linspace(0, len(frequencies) - 1, min(500, len(frequencies)), dtype=int)
                )
                db_values = 10 * np.log10(np.maximum(values[np.ix_(fi, ti)].T, 1e-300))
                result.update(
                    times=times[ti].tolist(),
                    frequencies=frequencies[fi].tolist(),
                    db=db_values.tolist(),
                    color_max_db=float(db_values.max()),
                    display_time_stride=max(1, int(np.ceil(len(times) / 700))),
                    display_frequency_stride=max(1, int(np.ceil(len(frequencies) / 500))),
                )
            else:
                result["frequencies"] = arrays["frequency_hz"].tolist()
                result["power"] = arrays["complex_magnitude_squared"].tolist()
                result["amplitude"] = arrays["amplitude"].tolist()
    return result


async def calculate(db, user, dataset, body):
    source, clock = await source_context(db, user, dataset, body.package_id, body.sample_id)
    project_id = source.project_id
    first, last = (
        (body.point - body.window_ms / 2000, body.point + body.window_ms / 2000)
        if body.mode == "cursor"
        else (body.start, body.end)
    )
    if clock["range"]:
        if last <= clock["range"][0] or first >= clock["range"][1]:
            raise invalid("所选时间不在当前音轨内，请重新选段。")
        first, last = max(first, clock["range"][0]), min(last, clock["range"][1])
    root = local_workspaces.project_root(source.project_id)
    if root is None:
        raise invalid("请先打开此材料所在的工作区文件夹。")
    identity = uuid.uuid4()
    output = root / ".statforge" / "storage" / "audio-spectra" / identity.hex
    local_workspaces.checked_path(output)
    output.mkdir(parents=True, exist_ok=False)
    config = runtime_config()
    request = {
        **body.model_dump(mode="json"),
        "source": str(storage_service.get_local_object_path(source.object_key)),
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
    label = (
        "声谱图"
        if body.mode == "spectrogram"
        else ("光标频谱" if body.mode == "cursor" else "选段频谱")
    )
    run = SpectrumRun(
        title=f"{label} · {first:.3f}–{last:.3f} 秒",
        source_dataset_id=source.id,
        source_sha256=source.sha256,
        context_dataset_id=dataset.id,
        parameters=body.model_dump(mode="json"),
        started_at=datetime.now(UTC).isoformat(),
        artifact_directory=str(output),
    )
    await put(db, user, source.project_id, identity, run)
    await db.commit()
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            config["python"],
            "-B",
            str(Path(__file__).with_name("audio_spectrum_worker.py")),
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
            raise invalid(failure.get("message") or "本地频谱程序未完成，请检查 Praat 运行环境。")
        await acquire_write_fence(db, user.id)
        run.method = read_json(output / "method.json")
        names = (
            ("measurement.wav", "spectrum.npz", "method.json")
            if body.mode == "spectrogram"
            else ("measurement.wav", "windowed.wav", "spectrum.npz", "spectrum.csv", "method.json")
        )
        for name in names:
            saved = await project_sources.store(
                db,
                source.project_id,
                user.id,
                str(output / name),
                name,
                f"频谱/{identity.hex}/{name}",
            )
            saved.preview_json = json.dumps(
                {
                    **read_metadata(saved),
                    "source_dataset_id": str(source.id),
                    "display_name": run.title + " · " + name,
                    "spectrum_result": {"run_id": str(identity), "kind": name},
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
            getattr(error, "detail", None) or "频谱保存失败，请检查工作区空间后重试。"
        )
        (output / "save-error.log").write_text(str(error), encoding="utf-8")
        await put(db, user, project_id, identity, run)
        await db.commit()
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
    return await detail(db, user, dataset, identity)


async def query(db, user, dataset, identity, body):
    run = await get(db, user, dataset, identity)
    if run.status != "completed":
        raise invalid("请打开已完成的频谱测量。")
    data = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(run.artifact_ids["spectrum.npz"]), user.id
    )
    config = runtime_config()
    with tempfile.TemporaryDirectory(prefix="spectral-query-", dir=config["temp"]) as temporary:
        output = Path(temporary)
        request = {
            **body.model_dump(),
            "operation": "query",
            "arrays": str(storage_service.get_local_object_path(data.object_key)),
            "output": temporary,
        }
        path = output / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        process = await asyncio.create_subprocess_exec(
            config["python"],
            "-B",
            str(Path(__file__).with_name("audio_spectrum_worker.py")),
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "TEMP": config["temp"],
                "TMP": config["temp"],
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        try:
            await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode:
            failure = (
                read_json(output / "failure.json") if (output / "failure.json").is_file() else {}
            )
            raise invalid(failure.get("message") or "无法读取这份频谱，请重新打开已保存的测量。")
        value = read_json(output / "query.json")
    if not body.save:
        return {"query": value}
    root = local_workspaces.project_root(dataset.project_id)
    if root is None:
        raise invalid("请先打开此材料所在的工作区文件夹。")
    new_id = uuid.uuid4()
    output = root / ".statforge" / "storage" / "audio-spectra" / new_id.hex
    local_workspaces.checked_path(output)
    output.mkdir(parents=True, exist_ok=False)
    label = (
        f"声谱点读 · {value['time']:.6g} 秒 / {value['frequency']:.6g} Hz"
        if "time" in value
        else f"频带查询 · {value['lower_hz']:.6g}–{value['upper_hz']:.6g} Hz"
    )
    method = {**run.method, "query": value}
    (output / "method.json").write_text(
        json.dumps(method, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    await acquire_write_fence(db, user.id)
    saved = await project_sources.store(
        db,
        dataset.project_id,
        user.id,
        str(output / "method.json"),
        "method.json",
        f"频谱/{new_id.hex}/method.json",
    )
    saved.preview_json = json.dumps(
        {
            **read_metadata(saved),
            "source_dataset_id": str(run.source_dataset_id),
            "display_name": label,
            "spectrum_result": {"run_id": str(new_id), "kind": "method.json"},
        },
        ensure_ascii=False,
    )
    revised = run.model_copy(
        update={
            "title": label,
            "started_at": datetime.now(UTC).isoformat(),
            "parameters": {
                **run.parameters,
                "parent_run_id": str(identity),
                "query": body.model_dump(),
            },
            "method": method,
            "artifact_directory": str(output),
            "artifact_ids": {**run.artifact_ids, "method.json": str(saved.id)},
        }
    )
    await put(db, user, dataset.project_id, new_id, revised)
    await db.commit()
    return {"query": value, "run": await detail(db, user, dataset, new_id)}


async def export(db, user, dataset, identity):
    run = await get(db, user, dataset, identity)
    if run.status != "completed":
        raise invalid("这次频谱尚未完成，无法导出。")
    output = Path(run.artifact_directory)
    local_workspaces.checked_path(output)
    path = output / "spectrum-export.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, item in run.artifact_ids.items():
            data = await dataset_service.get_dataset_with_access(db, uuid.UUID(item), user.id)
            archive.write(storage_service.get_local_object_path(data.object_key), name)
    return path
