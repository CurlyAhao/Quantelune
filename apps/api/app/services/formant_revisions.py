"""Compare native same-frame formants and save revised paths as media results."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from sqlalchemy import select

from app.models.dataset import Dataset, UploadStatus
from app.models.workspace import WorkspaceRecord
from app.preview.write_fence import acquire_write_fence
from app.schemas.media_runs import MediaRun
from app.services import dataset_service, project_sources, storage_service
from app.services import project_assistant_tasks as tasks
from app.services.audio_inspection import _saved_sample
from app.services.formant_path import PATH_FINDER
from app.services.multimodal_cases import (
    INSTANCE,
    invalid,
    put,
    read_json,
    read_metadata,
    runtime_config,
)
from app.services.pitch_exchange import check_package
from app.services.pitch_native import result_directory
from app.services.pitch_revisions import _copy_arrays


def _read(path, prefix):
    names = (
        "candidate_frequency",
        "candidate_bandwidth",
        "candidate_count",
        "candidate_intensity",
        "ceilings_hz",
        "selected",
        "original_selected",
        "original_frequency",
        "original_bandwidth",
        "frame_index",
        "times",
        "times_player",
    )
    with np.load(path, allow_pickle=False) as bundle:
        if f"{prefix}_formant_candidate_frequency" not in bundle:
            return None
        data = {name: bundle[f"{prefix}_formant_{name}"] for name in names}
    count, n = len(data["ceilings_hz"]), len(data["times"])
    if (
        not n
        or data["candidate_frequency"].shape != (count, n, 5)
        or data["candidate_bandwidth"].shape != (count, n, 5)
        or data["selected"].shape != (n,)
        or data["times_player"].shape != (n,)
        or not np.isfinite(data["times_player"]).all()
        or np.any(data["selected"] < 0)
        or np.any(data["selected"] >= count)
    ):
        raise invalid("这份共振峰结果的候选与帧不对应，请重新生成候选。")
    return data


async def _context(db, user, dataset, sample_id):
    source, run, entry = await _saved_sample(db, user, dataset, sample_id)
    if run["package_id"] != str(dataset.id):
        raise invalid("请从完整特征打开共振峰候选。")
    return source, run, entry


def _revision_label(revision):
    number = revision.get("number", 0)
    if number == 0:
        return "初始候选轨迹"
    return f"自动选路 {number}" if revision.get("action") == "auto" else f"选段修订 {number}"


async def _versions(db, user, source, entry):
    revision = entry.get("formant_revision") or {}
    root = revision.get("root_run_id")
    versions, latest = [], 0
    if root:
        rows = (
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
        for row in rows:
            run = MediaRun.model_validate_json(row.payload_json)
            item = run.parameters.get("formant_revision") or {}
            if (
                run.dataset_id == source.id
                and item.get("root_run_id") == root
                and str(run.parameters.get("signature", "")).startswith("formant-revision:")
            ):
                latest = max(latest, item["number"])
                if run.status == "completed" and run.package_id:
                    versions.append(
                        {
                            "package_id": run.package_id,
                            "revision": item["number"],
                            "label": _revision_label(item),
                            "created_at": run.completed_at,
                        }
                    )
    return revision, versions, latest


def _number(value):
    return float(value) if np.isfinite(value) else None


def _view(path, prefix, body):
    data = _read(path, prefix)
    if data is None:
        return {"available": False}
    times, selected = data["times_player"], data["selected"]
    position = (
        body.frame_index - 1
        if body.frame_index is not None
        else int(
            np.abs(
                times
                - (
                    body.point
                    if body.point is not None
                    else body.start
                    if body.start is not None
                    else times[0]
                )
            ).argmin()
        )
    )
    if position >= len(times):
        raise invalid(f"当前结果共 {len(times)} 帧，请选择范围内的帧。")
    positions = (
        np.flatnonzero((times >= body.start) & (times < body.end))
        if body.start is not None
        else np.arange(len(times))
    )
    # The response is a preview only. Arrays and interval measurements remain full-resolution.
    display = (
        positions[
            np.unique(np.linspace(0, len(positions) - 1, min(450, len(positions)), dtype=int))
        ]
        if len(positions)
        else positions
    )
    groups = []
    for index, ceiling in enumerate(data["ceilings_hz"]):
        f, b = data["candidate_frequency"][index], data["candidate_bandwidth"][index]
        groups.append(
            {
                "index": index,
                "ceiling_hz": float(ceiling),
                "point_frequency": [_number(v) for v in f[position, :4]],
                "point_bandwidth": [_number(v) for v in b[position, :4]],
                "tracks": [
                    [{"time": float(times[i]), "value": _number(f[i, peak])} for i in display]
                    for peak in range(4)
                ],
            }
        )
    return {
        "available": True,
        "total_frames": len(times),
        "groups": groups,
        "frame": {
            "index": position + 1,
            "time": float(times[position]),
            "selected": int(selected[position]),
        },
        "selected_in_range": sorted(set(selected[positions].tolist())),
        "range_frame_count": len(positions),
        "domain": [float(times[0]), float(times[-1])],
        "selected_tracks": [
            [
                {
                    "time": float(times[i]),
                    "value": _number(data["candidate_frequency"][selected[i], i, peak]),
                }
                for i in display
            ]
            for peak in range(4)
        ],
        "selection": {"start": body.start, "end": body.end},
        "display_stride": max(1, int(np.ceil(len(positions) / 450))),
    }


async def browse(db, user, dataset, body):
    source, _, entry = await _context(db, user, dataset, body.sample_id)
    revision, versions, _ = await _versions(db, user, source, entry)
    if revision and not any(v["package_id"] == str(dataset.id) for v in versions):
        versions.append(
            {
                "package_id": str(dataset.id),
                "revision": revision["number"],
                "label": "当前结果 · 沿用共振峰轨迹",
            }
        )
    view = await asyncio.to_thread(
        _view,
        storage_service.get_local_object_path(dataset.object_key),
        entry["array_prefix"],
        body,
    )
    return {
        **view,
        "package_id": str(dataset.id),
        "revision": revision.get("number", 0),
        "revision_label": _revision_label(revision),
        "versions": versions,
        "method": entry.get("formant_path"),
        "pathfinder": revision.get("pathfinder"),
        "pathfinder_defaults": PATH_FINDER,
        "changed_frame_count": len(revision.get("changed_frames") or []),
        "saved_selection": revision.get("selection"),
        "baseline_package_id": revision.get("baseline_package_id"),
    }


def _run_native(request, request_path, failure_message):
    config = runtime_config()
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [
            config["python"],
            "-B",
            str(Path(__file__).with_name("formant_path.py")),
            str(request_path),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "TEMP": config["temp"],
            "TMP": config["temp"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    meta_path = Path(request["output_json"])
    meta = read_json(meta_path) if meta_path.exists() else {}
    if result.returncode:
        error_file = (
            "pathfinder-error.log" if request.get("action") == "auto" else "formant-error.log"
        )
        meta_path.with_name(error_file).write_text(result.stderr, encoding="utf-8")
        reason = meta.get("error", "")
        raise invalid(
            reason if reason and any("\u4e00" <= c <= "\u9fff" for c in reason) else failure_message
        )
    with np.load(request["output_npz"], allow_pickle=False) as bundle:
        data = {key: bundle[key] for key in bundle.files}
    return data, meta


def _generate(audio, entry, parameters, output):
    offset = (entry.get("audio_alignment") or {}).get("effective_offset_seconds") or 0
    first = entry["praat_audio_first_pts"]
    origin = first - entry["audio_origin_seconds"] - offset
    request = {
        "audio_path": str(storage_service.get_local_object_path(audio.object_key)),
        "audio_sha256": audio.sha256,
        "sample_count": entry["praat_audio_sample_count"],
        "sample_rate": entry["praat_audio_sample_rate"],
        "first_pts": first,
        "player_origin": origin,
        "parameters": parameters,
        "output_npz": str(output / "candidates.npz"),
        "output_json": str(output / "formant-native.json"),
    }
    return _run_native(
        request, output / "formant-request.json", "共振峰候选生成失败，请检查上限、窗长和所选音轨。"
    )


def _automatic(data, method, output, revision):
    source = output / "pathfinder-input.npz"
    np.savez_compressed(source, **data)
    request = {
        "action": "auto",
        "input_npz": str(source),
        "method": method,
        "output_npz": str(output / "automatic-path.npz"),
        "output_json": str(output / "pathfinder-native.json"),
    }
    result, meta = _run_native(
        request, output / "pathfinder-request.json", "Praat 无法为这组已保存的候选自动选路。"
    )
    selected = result["selected"]
    changed = data["frame_index"][selected != data["selected"]]
    counts = np.bincount(selected, minlength=len(data["ceilings_hz"]))
    revision.update(
        pathfinder=meta,
        changed_frames=changed.tolist(),
        selected_candidate_counts_1_based={str(i): int(count) for i, count in enumerate(counts, 1)},
    )
    data["selected"] = selected


def _files(
    package_path,
    package_sha,
    summary_path,
    index_path,
    parameters_path,
    output,
    entry,
    body,
    revision,
    audio,
):
    check_package(package_path, package_sha)
    prefix = entry["array_prefix"]
    if body.action == "generate":
        data, method = _generate(audio, entry, body.parameters.model_dump(), output)
    else:
        data, method = _read(package_path, prefix), entry.get("formant_path")
        if data is None or not method:
            raise invalid("当前结果尚无共振峰候选，请先生成候选。")
        if body.action == "auto":
            _automatic(data, method, output, revision)
        else:
            if body.candidate_index >= len(data["ceilings_hz"]):
                raise invalid("这个候选不在当前结果中，请重新选择。")
            times = data["times_player"]
            if (
                body.start < method["player_origin"] - 1e-8
                or body.end
                > method["player_origin"] + method["sample_count"] / method["sample_rate"] + 1e-8
            ):
                raise invalid("选段超出本次测量的音轨范围，请调整开始和结束时间。")
            mask = (times >= body.start) & (times < body.end)
            if not mask.any():
                raise invalid("选段内没有测量帧，请扩大选段。")
            changed = np.flatnonzero(mask & (data["selected"] != body.candidate_index))
            if not len(changed):
                raise invalid("这个选段已经采用所选候选。")
            data["selected"] = data["selected"].copy()
            data["selected"][mask] = body.candidate_index
            revision.update(
                selection={"start": body.start, "end": body.end},
                candidate_index=body.candidate_index,
                ceiling_hz=float(data["ceilings_hz"][body.candidate_index]),
                changed_frames=(changed + 1).tolist(),
            )
    selected = data["selected"]
    f = data["candidate_frequency"][selected, np.arange(len(selected))]
    b = data["candidate_bandwidth"][selected, np.arange(len(selected))]
    replacements = {f"{prefix}_formant_{key}": value for key, value in data.items()}
    replacements.update({f"{prefix}_formant_frequency": f, f"{prefix}_formant_bandwidth": b})
    measures = {
        "formant_total_frames": len(selected),
        "formant_curve_version": _revision_label(revision),
    }
    for peak in range(4):
        for kind, values in (("f", f[:, peak]), ("b", b[:, peak])):
            name = f"{kind}{peak + 1}"
            valid = np.isfinite(values) & (values > 0)
            replacements[f"{prefix}_{name}_hz"] = np.where(valid, values, np.nan)
            replacements[f"{prefix}_{name}_valid"] = valid
            measures[f"{name}_mean_hz"] = float(values[valid].mean()) if valid.any() else None
            measures[f"{name}_valid_frames"] = int(valid.sum())
    _copy_arrays(package_path, output / "arrays.npz", replacements)
    with open(summary_path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns, rows = list(reader.fieldnames or []), list(reader)
    matches = [row for row in rows if row.get("sample_id") == body.sample_id]
    if len(matches) != 1:
        raise invalid("摘要中找不到当前样本，请重新打开同一次运行。")
    matches[0].update(measures)
    columns.extend(key for key in measures if key not in columns)
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    index = read_json(index_path)
    for item in index.get("entries", []):
        if item.get("sample", {}).get("sample_id") == body.sample_id:
            item.update(formant_path=method, formant_revision=revision)
    (output / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    parameters = read_json(parameters_path) if parameters_path else {}
    parameters["formant_revision"] = revision
    parameters["formant_path"] = method
    (output / "parameters.json").write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return method


async def save(db, user, dataset, body):
    source, original, entry = await _context(db, user, dataset, body.sample_id)
    await acquire_write_fence(db, user.id)
    prior, _, latest = await _versions(db, user, source, entry)
    identity = uuid.uuid4()
    generating = body.action == "generate"
    revision = {
        "number": 0 if generating else latest + 1,
        "root_run_id": str(identity) if generating else prior.get("root_run_id"),
        "parent_package_id": str(dataset.id),
        "parent_sha256": dataset.sha256,
        "baseline_package_id": str(dataset.id) if generating else prior.get("baseline_package_id"),
        "sample_id": body.sample_id,
        "action": body.action,
        "created_at": datetime.now(UTC).isoformat(),
    }
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

    def artifact(kind):
        return next(
            (
                item
                for item in items
                if read_metadata(item).get("media_result", {}).get("kind") == kind
            ),
            None,
        )

    audio = None
    if generating:
        filename = Path(entry.get("praat_audio_file") or "").name
        matches = [
            item
            for item in items
            if item.original_filename == filename
            and read_metadata(item).get("media_result", {}).get("kind") == "media"
        ]
        if len(matches) != 1 or matches[0].sha256 != entry.get("praat_audio_sha256"):
            raise invalid("找不到本次 Praat 测量音轨，请从原材料重新计算共振峰。")
        audio = matches[0]
    output = result_directory(source, identity)
    auto = body.action == "auto"
    title = (
        "共振峰候选"
        if generating
        else f"共振峰自动选路 {revision['number']}"
        if auto
        else f"共振峰修订 {revision['number']}"
    )
    progress = (
        "正在生成共振峰候选" if generating else "正在自动选路" if auto else "正在保存选段修订"
    )
    run = MediaRun(
        title=f"{title} · {(entry.get('sample') or {}).get('title', body.sample_id)}",
        dataset_id=source.id,
        task_id=original.get("task_id"),
        instance=INSTANCE,
        status="running",
        started_at=revision["created_at"],
        progress=progress,
        parameters={
            **original["parameters"],
            "signature": f"formant-revision:{identity}",
            "formant_revision": revision,
        },
        execution=original.get("execution") or {},
        artifact_directory=str(output),
    )
    await put(db, user, source.project_id, identity, run)
    await db.commit()
    try:
        method = await asyncio.to_thread(
            _files,
            storage_service.get_local_object_path(dataset.object_key),
            dataset.sha256,
            storage_service.get_local_object_path(artifact("summary_csv").object_key),
            storage_service.get_local_object_path(artifact("index_json").object_key),
            storage_service.get_local_object_path(artifact("parameters_json").object_key)
            if artifact("parameters_json")
            else None,
            output,
            entry,
            body,
            revision,
            audio,
        )
        await acquire_write_fence(db, user.id)
        new_rows = []
        for kind, filename, label in [
            ("summary_csv", "summary.csv", "特征摘要.csv"),
            ("arrays_npz", "arrays.npz", "完整特征.npz"),
            ("index_json", "index.json", "样本与特征索引.json"),
            ("parameters_json", "parameters.json", "处理参数.json"),
        ]:
            path = output / filename
            saved = (
                await dataset_service.upload_dataset(
                    db, source.project_id, user.id, str(path), label, allow_duplicate=True
                )
                if kind == "summary_csv"
                else await project_sources.store(
                    db, source.project_id, user.id, str(path), label, f"特征/{identity.hex}/{label}"
                )
            )
            saved.preview_json = json.dumps(
                {
                    **read_metadata(saved),
                    "source_dataset_id": str(source.id),
                    "display_name": f"{run.title} · {label.rsplit('.', 1)[0]}",
                    "media_result": {
                        "run_id": str(identity),
                        "collection_id": str(source.id),
                        "kind": kind,
                    },
                },
                ensure_ascii=False,
            )
            new_rows.append(saved)
            if kind == "summary_csv":
                run.summary_id = str(saved.id)
            if kind == "arrays_npz":
                run.package_id = str(saved.id)
            if kind == "index_json":
                run.index_id = str(saved.id)
        run.result_ids = [str(item.id) for item in new_rows] + [
            str(item.id)
            for item in items
            if read_metadata(item).get("media_result", {}).get("kind") == "media"
        ]
        run.parameters.update(formant_revision=revision, formant_path=method)
        if run.task_id:
            live = SimpleNamespace(
                project=source.project_id, value={"task_context": {"id": str(run.task_id)}}
            )
            for item in new_rows:
                await tasks.append(db, user, live, "dataset_ids", item.id)
        run.status, run.progress, run.completed_at = (
            "completed",
            title + "已保存",
            datetime.now(UTC).isoformat(),
        )
        await put(db, user, source.project_id, identity, run)
        await db.commit()
    except Exception as error:
        await db.rollback()
        await db.refresh(user)
        await db.refresh(source)
        (output / "save-error.log").write_text(str(error), encoding="utf-8")
        run.status, run.error_message, run.completed_at = (
            "failed",
            getattr(error, "detail", None) or "共振峰处理失败，请检查工作区空间后重试。",
            datetime.now(UTC).isoformat(),
        )
        await put(db, user, source.project_id, identity, run)
        await db.commit()
        raise invalid(run.error_message) from error
    return {
        "package_id": run.package_id,
        "summary_id": run.summary_id,
        "run_id": str(identity),
        "revision": revision["number"],
    }
