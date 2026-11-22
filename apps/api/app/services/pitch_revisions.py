"""Native candidate views and immutable pitch-path revisions of saved media runs."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
from sqlalchemy import select

from app.models.dataset import Dataset, UploadStatus
from app.models.workspace import WorkspaceRecord
from app.preview.write_fence import acquire_write_fence
from app.schemas.media_runs import MediaRun
from app.services import (
    dataset_service,
    pitch_exchange,
    project_sources,
    storage_service,
)
from app.services import project_assistant_tasks as tasks
from app.services.audio_inspection import _saved_sample
from app.services.multimodal_cases import INSTANCE, invalid, put, read_json, read_metadata
from app.services.praat_pitch import pitch_parameters


def _summary(values):
    values = np.asarray(values, dtype=np.float64)
    voiced = values[np.isfinite(values) & (values > 0)]
    return {
        "mean": float(voiced.mean()) if voiced.size else None,
        "cv": float(voiced.std(ddof=1) / voiced.mean()) if voiced.size >= 2 else None,
        "range": float(np.ptp(voiced)) if voiced.size else None,
        "valid_count": int(voiced.size),
        "total_count": int(values.size),
    }


def _number(value):
    return float(value) if np.isfinite(value) else None


def _read(path, prefix):
    with np.load(path, allow_pickle=False) as bundle:
        names = [
            "frame_index",
            "frame_intensity",
            "candidate_count",
            "candidate_frequency",
            "candidate_strength",
            "original_selected",
            "selected",
            "original_hz",
            "original_valid",
            "hz",
            "valid",
            "ceiling",
            "times",
            "times_player",
        ]
        if any(f"{prefix}_pitch_{name}" not in bundle for name in names):
            return None
        data = {name: bundle[f"{prefix}_pitch_{name}"] for name in names}
    count = len(data["times"])
    if (
        not count
        or data["candidate_frequency"].ndim != 2
        or data["candidate_frequency"].shape != data["candidate_strength"].shape
        or data["candidate_frequency"].shape[0] != count
        or any(
            data[name].shape != (count,)
            for name in names
            if name not in {"ceiling", "candidate_frequency", "candidate_strength"}
        )
        or not np.isfinite(data["times_player"]).all()
    ):
        raise invalid("这份结果的候选帧不完整，请从原材料重新计算。")
    return data


async def _context(db, user, dataset, sample_id):
    source, run, entry = await _saved_sample(db, user, dataset, sample_id)
    package = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(run["package_id"]), user.id
    )
    if dataset.id != package.id:
        raise invalid("请从完整特征打开基频候选。")
    return source, run, entry, package


async def _versions(db, user, source, package, run):
    revision = run["parameters"].get("pitch_revision") or {}
    root = revision.get("root_package_id") or str(package.id)
    root_package = await dataset_service.get_dataset_with_access(db, uuid.UUID(root), user.id)
    if root_package.project_id != source.project_id:
        raise invalid("原始基频结果不属于当前项目。")
    versions = [
        {
            "package_id": root,
            "revision": 0,
            "label": "原始结果",
            "created_at": root_package.created_at.isoformat(),
        }
    ]
    latest_number = 0
    records = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == source.project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "media_run",
            )
            .order_by(WorkspaceRecord.created_at)
        )
    ).all()
    for row in records:
        candidate = MediaRun.model_validate_json(row.payload_json)
        item = candidate.parameters.get("pitch_revision") or {}
        if not str(candidate.parameters.get("signature", "")).startswith("pitch-revision:"):
            # Other acoustic revisions preserve this metadata without creating a pitch path.
            continue
        if candidate.dataset_id == source.id and item.get("root_package_id") == root:
            latest_number = max(latest_number, item["number"])
        if (
            candidate.dataset_id == source.id
            and candidate.status == "completed"
            and item.get("root_package_id") == root
            and candidate.package_id
        ):
            versions.append(
                {
                    "package_id": candidate.package_id,
                    "revision": item["number"],
                    ("label"): f"{
                        ('重选')
                        if item.get(
                            'acti\
on'
                        )
                        == ('reselect')
                        else ('修订')
                    } {item[('number')]}",
                    "created_at": candidate.completed_at,
                }
            )
    return root, revision, versions, latest_number


def _view(path, prefix, body):
    data = _read(path, prefix)
    if data is None:
        return {"available": False}
    times = data["times_player"]
    if body.frame_index is not None:
        position = body.frame_index - 1
        if position >= len(times):
            raise invalid(f"这份基频结果共 {len(times)} 帧，请选择范围内的帧。")
    else:
        point = (
            body.point
            if body.point is not None
            else (body.start if body.start is not None else times[0])
        )
        position = int(np.abs(times - point).argmin())
    ceiling = float(data["ceiling"])
    count = int(data["candidate_count"][position])
    candidates = []
    for index in range(count):
        frequency = float(data["candidate_frequency"][position, index])
        strength = float(data["candidate_strength"][position, index])
        candidates.append(
            {
                "index": index,
                "frequency": frequency,
                "strength": strength,
                "voiced": 0 < frequency <= ceiling,
                "selectable": np.isfinite(frequency).item() and 0 <= frequency <= ceiling,
            }
        )
    visible = (
        np.flatnonzero((times >= body.start) & (times <= body.end))
        if body.start is not None
        else np.arange(len(times))
    )
    # Only display a small neighbourhood of exact frames. No downsampling of saved candidates.
    center = int(np.searchsorted(visible, position))
    left = max(0, min(center - 40, len(visible) - 80))
    visible = visible[left : left + 80]
    return {
        "available": True,
        "total_frames": len(times),
        "pitch_ceiling": ceiling,
        "summary": _summary(data["hz"]),
        "original_summary": _summary(data["original_hz"]),
        "frame": {
            "index": position + 1,
            "time": float(data["times"][position]),
            "player_time": float(times[position]),
            "intensity": float(data["frame_intensity"][position]),
            "original_selected": int(data["original_selected"][position]),
            "selected": int(data["selected"][position]),
            "original_hz": _number(data["original_hz"][position]),
            "value": _number(data["hz"][position]),
            "candidates": candidates,
        },
        "frames": [
            {
                "index": int(i) + 1,
                "time": float(data["times"][i]),
                "player_time": float(times[i]),
                "value": _number(data["hz"][i]),
            }
            for i in visible
        ],
    }


async def browse(db, user, dataset, body):
    source, run, entry, package = await _context(db, user, dataset, body.sample_id)
    root, revision, versions, _ = await _versions(db, user, source, package, run)
    if not any(v["package_id"] == str(package.id) for v in versions):
        versions.append(
            {
                "package_id": str(package.id),
                "revision": revision.get("number", 0),
                "label": "当前结果 · 沿用基频轨迹",
                "created_at": package.created_at.isoformat(),
            }
        )
    view = await asyncio.to_thread(
        _view,
        storage_service.get_local_object_path(package.object_key),
        entry["array_prefix"],
        body,
    )
    defaults = pitch_parameters(run["parameters"])
    path_parameters = {
        key: defaults[key]
        for key in (
            "silence_threshold",
            "voicing_threshold",
            "octave_cost",
            "octave_jump_cost",
            "voiced_unvoiced_cost",
        )
    }
    path_parameters.update(revision.get("path_parameters") or {})
    path_parameters["ceiling"] = view.get("pitch_ceiling", defaults["pitch_ceiling"])
    engine = revision.get("path_engine")
    return {
        **view,
        "package_id": str(package.id),
        "root_package_id": root,
        "path_parameters": path_parameters,
        "path_engine": f"Praat {engine['praat_version']} · Parselmouth {engine['version']}"
        if engine
        else None,
        "parent_package_id": revision.get("parent_package_id"),
        "revision": revision.get("number", 0),
        "method": {
            "name": run["parameters"].get("pitch_method", "raw_autocorrelation"),
            "engine": run.get("execution", {}).get("pitch"),
            "parameters": {
                key: value
                for key, value in run["parameters"].items()
                if key.startswith("pitch_") and key != "pitch_revision"
            },
        },
        "versions": versions,
    }


def _copy_arrays(source, target, replacements):
    """Stream unchanged arrays; preserve every other modality and the native candidate matrices."""
    with (
        zipfile.ZipFile(source) as old,
        zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as new,
    ):
        for info in old.infolist():
            if info.filename.removesuffix(".npy") in replacements:
                continue
            with (
                old.open(info) as incoming,
                new.open(info.filename, "w", force_zip64=True) as outgoing,
            ):
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        for name, array in replacements.items():
            with new.open(name + ".npy", "w", force_zip64=True) as stream:
                np.lib.format.write_array(stream, np.asarray(array), allow_pickle=False)


def _revised_files(
    package_path,
    expected_sha,
    summary_path,
    index_path,
    parameters_path,
    output,
    entry,
    body,
    parent_path,
    parent_sha,
    revision,
    original_parameters,
):
    with open(package_path, "rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha:
            raise invalid("完整特征文件已变化，请重新打开保存的结果。")
    prefix = entry["array_prefix"]
    data = _read(package_path, prefix)
    if data is None:
        raise invalid("这次旧结果没有保存候选，请用原材料重新计算基频。")
    selected = data["selected"].copy()
    ceiling = float(data["ceiling"])
    if body.action == "undo":
        with open(parent_path, "rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != parent_sha:
                raise invalid("上一个修订文件已变化，请打开原始结果。")
        previous = _read(parent_path, prefix)
        if previous is None or not np.array_equal(previous["frame_index"], data["frame_index"]):
            raise invalid("上一个修订的基频帧不可用，请打开原始结果。")
        selected = previous["selected"].copy()
        ceiling = float(previous["ceiling"])
    elif body.action == "reselect":
        clock = pitch_exchange.clock_for(data, entry, original_parameters)
        selected, metadata = pitch_exchange.reselect(
            package_path, prefix, clock, body.parameters.model_dump(), output
        )
        ceiling = metadata["ceiling"]
        revision.update(
            path_parameters=body.parameters.model_dump(),
            path_engine={key: metadata[key] for key in ("engine", "version", "praat_version")},
            pitch_clock=clock,
        )
    else:
        for change in body.changes:
            frame, candidate = change.frame_index - 1, change.candidate_index
            if frame >= len(selected) or candidate >= int(data["candidate_count"][frame]):
                raise invalid("所选候选已不可用，请刷新当前帧后重选。")
            frequency = data["candidate_frequency"][frame, candidate]
            if not np.isfinite(frequency) or not 0 <= frequency <= float(data["ceiling"]):
                raise invalid("这个候选高于本次基频上限，请选择范围内的候选或无声。")
            selected[frame] = candidate
    changed = np.flatnonzero(selected != data["selected"])
    if not changed.size and ceiling == float(data["ceiling"]) and body.action == "edit":
        raise invalid("当前帧已经使用这个选择。")
    frequency = data["candidate_frequency"][np.arange(len(selected)), selected]
    valid = np.isfinite(frequency) & (frequency > 0) & (frequency <= ceiling)
    hz = np.where(valid, frequency, np.nan)
    revision["changes"] = [
        {
            "frame_index": int(i) + 1,
            "time": float(data["times"][i]),
            "player_time": float(data["times_player"][i]),
            "from_candidate": int(data["selected"][i]),
            "to_candidate": int(selected[i]),
        }
        for i in changed
    ]
    summary = _summary(hz)
    revision["summary"] = summary
    replacements = {
        f"{prefix}_pitch_selected": selected,
        f"{prefix}_pitch_hz": hz,
        f"{prefix}_pitch_ceiling": np.asarray(ceiling),
        f"{prefix}_pitch_valid": valid,
        f"{prefix}_pitch_revision_number": np.array(revision["number"]),
        f"{prefix}_pitch_original_package_id": np.array(revision["root_package_id"]),
        f"{prefix}_pitch_parent_package_id": np.array(revision["parent_package_id"]),
    }
    _copy_arrays(package_path, output / "arrays.npz", replacements)
    with open(summary_path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns, rows = list(reader.fieldnames or []), list(reader)
    matches = [row for row in rows if row.get("sample_id") == body.sample_id]
    if len(matches) != 1:
        raise invalid("摘要中未找到当前样本，请重新打开同一次处理的完整特征。")
    matches[0].update(
        pitch_mean_hz=summary["mean"],
        pitch_cv=summary["cv"],
        pitch_range_hz=summary["range"],
        pitch_valid_frames=summary["valid_count"],
        pitch_total_frames=summary["total_count"],
        pitch_curve_version=f"修订 {revision['number']}",
    )
    if "pitch_curve_version" not in columns:
        columns.append("pitch_curve_version")
    for row in rows:
        row.setdefault("pitch_curve_version", "原始结果")
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    index = read_json(index_path)
    index["pitch_revision"] = revision
    for item in index.get("entries", []):
        if item.get("sample", {}).get("sample_id") == body.sample_id:
            item["pitch_revision"] = revision
            if revision.get("pitch_clock"):
                item["pitch_clock"] = revision["pitch_clock"]
    (output / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    parameters = read_json(parameters_path) if parameters_path else {}
    parameters["pitch_revision"] = revision
    if revision.get("path_parameters"):
        parameters.setdefault("praat", {})["path_finder"] = revision["path_parameters"]
        parameters["praat"]["path_engine"] = revision.get("path_engine")
    else:
        parameters.get("praat", {}).pop("path_finder", None)
        parameters.get("praat", {}).pop("path_engine", None)
    (output / "parameters.json").write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def save(db, user, dataset, body):
    source, original, entry, package = await _context(db, user, dataset, body.sample_id)
    await acquire_write_fence(db, user.id)
    root, prior, versions, latest_number = await _versions(db, user, source, package, original)
    parent_path, parent_sha = None, None
    if body.action == "undo":
        if not prior.get("parent_package_id"):
            raise invalid("当前为原始结果，没有可撤销的候选修订。")
        if prior.get("sample_id") != body.sample_id:
            raise invalid("请先切换到上次修改的样本，再撤销该修订。")
        parent = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(prior["parent_package_id"]), user.id
        )
        parent_meta = read_metadata(parent).get("media_result") or {}
        if parent_meta.get("collection_id") != str(source.id):
            raise invalid("上一个修订不属于当前材料组。")
        if parent.sha256 != prior.get("parent_sha256"):
            raise invalid("上一个修订的记录已变化，请打开原始结果。")
        parent_path = storage_service.get_local_object_path(parent.object_key)
        parent_sha = parent.sha256
    identity = uuid.uuid4()
    number = latest_number + 1
    revision = {
        "number": number,
        "root_package_id": root,
        "parent_package_id": str(package.id),
        "parent_run_id": original["id"],
        "parent_sha256": package.sha256,
        "sample_id": body.sample_id,
        "action": body.action,
        "created_at": datetime.now(UTC).isoformat(),
    }
    root_dataset = await dataset_service.get_dataset_with_access(db, uuid.UUID(root), user.id)
    revision["candidate_package_id"] = root
    revision["candidate_package_sha256"] = root_dataset.sha256
    if body.action == "undo":
        parent_run = await _context(db, user, parent, body.sample_id)
        restored = parent_run[1]["parameters"].get("pitch_revision") or {}
        for key in ("path_parameters", "path_engine", "pitch_clock"):
            if key in restored:
                revision[key] = restored[key]
    elif prior.get("path_parameters") and body.action == "edit":
        for key in ("path_parameters", "path_engine", "pitch_clock"):
            if key in prior:
                revision[key] = prior[key]
    from app.services.pitch_native import result_directory

    output = result_directory(source, identity)
    run = MediaRun(
        title=f"基频修订 {number} · {(entry.get('sample') or {}).get('title', body.sample_id)}",
        dataset_id=source.id,
        task_id=original.get("task_id"),
        instance=INSTANCE,
        status="running",
        started_at=revision["created_at"],
        progress="正在保存基频修订",
        parameters={
            **original["parameters"],
            "signature": f"pitch-revision:{identity}",
            "pitch_revision": revision,
        },
        execution=original.get("execution") or {},
        artifact_directory=str(output),
    )
    await put(db, user, source.project_id, identity, run)
    await db.commit()
    try:
        summary = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(original["summary_id"]), user.id
        )
        index = await dataset_service.get_dataset_with_access(
            db, uuid.UUID(original["index_id"]), user.id
        )
        ids = [uuid.UUID(item) for item in original["result_ids"]]
        items = (
            await db.scalars(
                select(Dataset).where(
                    Dataset.project_id == source.project_id,
                    Dataset.id.in_(ids),
                    Dataset.upload_status != UploadStatus.DELETED,
                )
            )
        ).all()
        parameters = next(
            (
                item
                for item in items
                if read_metadata(item).get("media_result", {}).get("kind") == "parameters_json"
            ),
            None,
        )
        await asyncio.to_thread(
            _revised_files,
            storage_service.get_local_object_path(package.object_key),
            package.sha256,
            storage_service.get_local_object_path(summary.object_key),
            storage_service.get_local_object_path(index.object_key),
            storage_service.get_local_object_path(parameters.object_key) if parameters else None,
            output,
            entry,
            body,
            parent_path,
            parent_sha,
            revision,
            original["parameters"],
        )
        await acquire_write_fence(db, user.id)
        new_rows = []
        for key, filename, label in [
            ("summary_csv", "summary.csv", "特征摘要.csv"),
            ("arrays_npz", "arrays.npz", "完整特征.npz"),
            ("index_json", "index.json", "样本与特征索引.json"),
            ("parameters_json", "parameters.json", "处理参数.json"),
        ]:
            path = output / filename
            if key == "summary_csv":
                saved = await dataset_service.upload_dataset(
                    db, source.project_id, user.id, str(path), label, allow_duplicate=True
                )
            else:
                saved = await project_sources.store(
                    db, source.project_id, user.id, str(path), label, f"特征/{identity.hex}/{label}"
                )
            saved.preview_json = json.dumps(
                {
                    **read_metadata(saved),
                    "source_dataset_id": str(source.id),
                    "display_name": f"{run.title} · {label.rsplit('.', 1)[0]}",
                    "media_result": {
                        "run_id": str(identity),
                        "collection_id": str(source.id),
                        "kind": key,
                    },
                },
                ensure_ascii=False,
            )
            new_rows.append(saved)
            if key == "summary_csv":
                run.summary_id = str(saved.id)
            if key == "arrays_npz":
                run.package_id = str(saved.id)
            if key == "index_json":
                run.index_id = str(saved.id)
        # Playback refers to the same saved sound and frames; a pitch edit never regenerates media.
        run.result_ids = [str(item.id) for item in new_rows] + [
            str(item.id)
            for item in items
            if read_metadata(item).get("media_result", {}).get("kind") == "media"
        ]
        run.parameters["pitch_revision"] = revision
        if run.task_id:
            live = SimpleNamespace(
                project=source.project_id, value={"task_context": {"id": str(run.task_id)}}
            )
            for item in new_rows:
                await tasks.append(db, user, live, "dataset_ids", item.id)
        run.status, run.progress, run.completed_at = (
            "completed",
            f"基频修订 {number} 已保存",
            datetime.now(UTC).isoformat(),
        )
        await put(db, user, source.project_id, identity, run)
        await db.commit()
    except Exception as error:
        await db.rollback()
        await db.refresh(user)
        await db.refresh(source)
        (output / "save-error.log").write_text(str(error), encoding="utf-8")
        run.status = "failed"
        run.error_message = (
            getattr(error, "detail", None) or "基频修订未保存，请检查工作区空间后重试。"
        )
        run.completed_at = datetime.now(UTC).isoformat()
        await put(db, user, source.project_id, identity, run)
        await db.commit()
        raise invalid(run.error_message) from error
    return {
        "package_id": run.package_id,
        "summary_id": run.summary_id,
        "run_id": str(identity),
        "revision": number,
    }
