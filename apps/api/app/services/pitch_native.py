"""Open native Pitch files in the existing saved media-result views."""

from __future__ import annotations

import asyncio
import csv
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from app.config import settings
from app.preview.write_fence import acquire_write_fence
from app.schemas.media_runs import MediaAssociation, MediaAssociationItem, MediaRun
from app.services import dataset_service, pitch_exchange, project_sources, storage_service
from app.services import local_workspaces as local
from app.services.multimodal_cases import INSTANCE, associate, invalid, put, read_metadata


def result_directory(source, identity):
    root = local.project_root(source.project_id)
    if root is None:
        root = Path(settings.local_storage_path).resolve() / "projects" / str(source.project_id)
    if root.drive.upper() == "C:":
        raise invalid("请在 G 或 H 盘打开本地工作区。")
    output = root / ".statforge" / "storage" / "media-runs" / identity.hex
    local.checked_path(output)
    output.mkdir(parents=True, exist_ok=False)
    return output


def read_files(source_path, expected, output, source):
    from app.services.pitch_revisions import _summary

    pitch_exchange.check_package(source_path, expected)
    metadata = pitch_exchange.native(
        "read", {"input_path": str(source_path), "output_npz": str(output / "native.npz")}, output
    )
    clock = {key: metadata[key] for key in ("xmin", "xmax", "dx", "x1")}
    with np.load(output / "native.npz", allow_pickle=False) as bundle:
        arrays = {key: bundle[key] for key in bundle.files}
    if not len(arrays["times"]):
        raise invalid("这份 Pitch 对象没有基频帧。")
    arrays["times_player"] = arrays["times"].copy()
    np.savez_compressed(
        output / "arrays.npz",
        **{f"sample_0000_pitch_{key}": value for key, value in arrays.items()},
    )
    summary = _summary(arrays["hz"])
    row = {
        "sample_id": str(source.id),
        "sample": source.original_filename,
        **{
            "pitch_" + key: value
            for key, value in zip(
                ("mean_hz", "cv", "range_hz", "valid_frames", "total_frames"), summary.values()
            )
        },
    }
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    entry = {
        "sample": {"sample_id": str(source.id), "title": source.original_filename},
        "array_prefix": "sample_0000",
        "pitch_clock": clock,
        "segment": {"player_start": clock["xmin"], "player_end": clock["xmax"]},
    }
    index = {
        "kind": "media",
        "native_pitch": {"source_id": str(source.id), "sha256": expected, "clock": clock},
        "entries": [entry],
    }
    (output / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    methods = {
        "praat": {
            "pitch_method": "native_object",
            "pitch_ceiling_hz": metadata["ceiling"],
            "time_step_seconds": clock["dx"],
        },
        "native_pitch": {
            **index["native_pitch"],
            "reader": {key: metadata[key] for key in ("engine", "version", "praat_version")},
        },
    }
    (output / "parameters.json").write_text(
        json.dumps(methods, ensure_ascii=False), encoding="utf-8"
    )
    return metadata


async def open_object(db, user, source):
    if source.file_format != "pitch":
        raise invalid("请选择 Praat Pitch 文件。")
    await acquire_write_fence(db, user.id)
    meta = read_metadata(source)
    existing = meta.get("pitch_object") or {}
    if existing.get("sha256") == source.sha256 and existing.get("package_id"):
        saved = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(existing["package_id"]), user.id
        )
        return {"package_id": str(saved.id)}
    identity = uuid.uuid4()
    output = result_directory(source, identity)
    started = datetime.now(UTC).isoformat()
    run = MediaRun(
        title=f"Pitch · {source.original_filename}",
        dataset_id=source.id,
        instance=INSTANCE,
        status="running",
        started_at=started,
        progress="正在读取基频",
        artifact_directory=str(output),
        parameters={
            "steps": ["pitch"],
            "pitch_method": "native_object",
            "pitch_source_id": str(source.id),
            "pitch_source_sha256": source.sha256,
        },
    )
    await put(db, user, source.project_id, identity, run)
    await db.commit()
    try:
        metadata = await asyncio.to_thread(
            read_files,
            storage_service.get_local_object_path(source.object_key),
            source.sha256,
            output,
            source,
        )
        await acquire_write_fence(db, user.id)
        for key, filename, label in [
            ("summary_csv", "summary.csv", "基频摘要.csv"),
            ("arrays_npz", "arrays.npz", "完整基频.npz"),
            ("index_json", "index.json", "基频索引.json"),
            ("parameters_json", "parameters.json", "基频参数.json"),
        ]:
            path = output / filename
            row = (
                await dataset_service.upload_dataset(
                    db, source.project_id, user.id, str(path), label, allow_duplicate=True
                )
                if key == "summary_csv"
                else await project_sources.store(
                    db, source.project_id, user.id, str(path), label, f"基频/{identity.hex}/{label}"
                )
            )
            row.preview_json = json.dumps(
                {
                    **read_metadata(row),
                    "source_dataset_id": str(source.id),
                    "display_name": f"{source.original_filename} · {label.rsplit('.', 1)[0]}",
                    "media_result": {
                        "run_id": str(identity),
                        "collection_id": str(source.id),
                        "kind": key,
                    },
                },
                ensure_ascii=False,
            )
            run.result_ids.append(str(row.id))
            if key == "summary_csv":
                run.summary_id = str(row.id)
            if key == "arrays_npz":
                run.package_id = str(row.id)
            if key == "index_json":
                run.index_id = str(row.id)
        run.status, run.progress, run.completed_at = (
            "completed",
            "基频已读取",
            datetime.now(UTC).isoformat(),
        )
        run.execution = {
            "pitch_reader": {key: metadata[key] for key in ("engine", "version", "praat_version")}
        }
        run.parameters.update(pitch_ceiling_hz=metadata["ceiling"], praat_time_step=metadata["dx"])
        meta["pitch_object"] = {
            "sha256": source.sha256,
            "package_id": run.package_id,
            "run_id": str(identity),
        }
        source.preview_json = json.dumps(meta, ensure_ascii=False)
        await put(db, user, source.project_id, identity, run)
        await db.commit()
    except Exception as error:
        await db.rollback()
        await db.refresh(user)
        await db.refresh(source)
        run.status, run.completed_at = "failed", datetime.now(UTC).isoformat()
        run.error_message = (
            getattr(error, "detail", None) or "无法读取这份 Pitch 文件，请检查文件是否完整。"
        )
        (output / "read-error.log").write_text(str(error), encoding="utf-8")
        await put(db, user, source.project_id, identity, run)
        await db.commit()
        raise invalid(run.error_message) from error
    return {"package_id": run.package_id}


async def link_audio(db, user, package, audio_id, offset):
    meta = read_metadata(package).get("media_result") or {}
    source = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(meta["collection_id"]), user.id
    )
    if source.file_format != "pitch":
        raise invalid("材料关联用于已导入的 Pitch 对象。")
    # The ordinary association service preserves file versions and immutable groups.
    saved = await associate(
        db,
        user,
        source.id,
        MediaAssociation(
            samples=[
                MediaAssociationItem(
                    audio_id=audio_id,
                    pitch_id=source.id,
                    pitch_offset_seconds=offset,
                    member_ids=[source.id],
                )
            ]
        ),
    )
    await acquire_write_fence(db, user.id)
    source_meta = read_metadata(source)
    source_meta["pitch_association_id"] = saved["id"]
    source.preview_json = json.dumps(source_meta, ensure_ascii=False)
    await db.commit()
    return saved


async def association(db, user, source):
    identity = read_metadata(source).get("pitch_association_id")
    if not identity:
        return None
    group = await dataset_service.get_dataset_with_access(db, uuid.UUID(identity), user.id)
    samples = (group.media_case or {}).get("samples", [])
    sample = next((value for value in samples if value.get("pitch_id") == str(source.id)), None)
    if (
        group.project_id != source.project_id
        or not sample
        or sample["files"]["pitch"]["sha256"] != source.sha256
    ):
        raise invalid("关联的 Pitch 版本已变化，请重新选择原音轨。")
    audio = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(sample["audio_id"]), user.id
    )
    if audio.project_id != source.project_id or audio.sha256 != sample["files"]["audio"]["sha256"]:
        raise invalid("关联音轨的版本已变化，请重新选择原音轨。")
    return {
        "group_id": str(group.id),
        "audio_id": str(audio.id),
        "name": audio.original_filename,
        "offset_seconds": sample["pitch_alignment"]["offset_seconds"],
    }
