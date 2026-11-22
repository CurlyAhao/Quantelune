"""Small PhysioCGM adapter. Original clocks and samples are never resampled."""

import hashlib
import json
import pickle
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.exceptions import StatForgeError
from app.models.dataset import UploadStatus
from app.services import dataset_service, project_sources, storage_service

SCHEMA = "statforge-physiocgm-v1"
ZEPHYR = {
    "ECG": ["EcgWaveform"],
    "Summary": ["HR", "BR", "Posture", "Activity", "HRConfidence", "ECGNoise"],
    "Accel": ["Vertical", "Lateral", "Sagittal"],
    "Breathing": ["BreathingWaveform"],
}
E4 = {"ACC": ["x", "y", "z"], "BVP": ["BVP"], "HR": ["HR"], "EDA": ["EDA"], "TEMP": ["TEMP"]}


def invalid(message):
    return StatForgeError(422, "PHYSIOCGM_INPUT", message)


class Options(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    timezone: str | None = None
    start: float | None = None
    end: float | None = None
    e4_offset: float = Field(default=0, ge=-86400, le=86400)
    cgm_offset: float = Field(default=0, ge=-86400, le=86400)
    summary_tolerance: float = Field(default=1, gt=0, le=60)
    cgm_tolerance: float = Field(default=330, gt=0, le=3600)
    window: int | None = Field(default=None, ge=0)
    task_id: uuid.UUID | None = None
    window_seconds: float = Field(default=30, gt=0)
    step_seconds: float | None = Field(default=None, gt=0)
    hapt_window_seconds: float = Field(default=2.56, gt=0)
    hapt_step_seconds: float = Field(default=1.28, gt=0)
    hapt_index_base: int = Field(default=1, ge=0, le=1)
    hapt_end_inclusive: bool = True


async def discover(db, project, owner, root, rows):
    """Group a subject's explicitly selected local files, without copying samples."""
    subjects = [root] if (root / "zephyr").is_dir() else [p.parent for p in root.glob("*/zephyr")]
    for subject in subjects:
        if not (subject / "cgm.csv").is_file():
            continue
        for session in sorted((subject / "zephyr").iterdir()):
            if not session.is_dir() or session.is_symlink():
                continue
            ecg = list(session.glob("*_ECG.csv"))
            summary = list(session.glob("*_SummaryEnhanced.csv")) or list(
                session.glob("*_Summary.csv")
            )
            if len(ecg) != 1 or len(summary) != 1:
                continue
            identity = session.relative_to(root).as_posix()
            if any(
                json.loads(r.preview_json or "{}").get("physiocgm_session") == identity
                for r in rows
            ):
                continue
            files = {
                "zephyr": {},
                "e4": {},
                "cgm": (subject / "cgm.csv").relative_to(root).as_posix(),
            }
            for kind in ZEPHYR:
                found = summary if kind == "Summary" else list(session.glob(f"*_{kind}.csv"))
                files["zephyr"][kind] = [p.relative_to(root).as_posix() for p in found]
            for kind in E4:
                files["e4"][kind] = [
                    p.relative_to(root).as_posix()
                    for p in sorted((subject / "e4").rglob(f"{kind}.csv"))
                ]
            with tempfile.TemporaryDirectory(prefix="sf-physio-") as temporary:
                path = Path(temporary) / "session.physiocgm"
                path.write_text(
                    json.dumps(
                        {
                            "schema": SCHEMA,
                            "subject": subject.name,
                            "session": session.name,
                            "files": files,
                        }
                    ),
                    encoding="utf-8",
                )
                row = await project_sources.store(
                    db,
                    project.id,
                    owner,
                    str(path),
                    f"{subject.name} · {session.name}.physiocgm",
                    f"会话/{subject.name}/{session.name}.physiocgm",
                )
                row.preview_json = json.dumps(
                    {
                        "raw_file": True,
                        "signal_session": True,
                        "physiocgm_session": identity,
                        "source_path": row.source_path,
                    }
                )
                row.upload_status = UploadStatus.READY
                rows.append(row)


def local_times(values, timezone, *, dayfirst=False):
    times = pd.to_datetime(values, format="mixed", dayfirst=dayfirst, errors="raise")
    if times.dt.tz is None:
        if not timezone:
            raise invalid("请设置 Zephyr 与 CGM 记录使用的时区；E4 已使用 UTC 时间。")
        try:
            ZoneInfo(timezone)
            times = times.dt.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
        except Exception as error:
            raise invalid(
                "时区或夏令时时间无法确定，请使用有效 IANA 时区（例如 Ameri"
                "ca/Chicago），并核对记录时间。"
            ) from error
    return times.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").to_numpy() / 1e9


def source_path(root, relative):
    from app.services.local_workspaces import checked_path

    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise invalid("会话文件不在当前工作文件夹中。")
    checked_path(path)
    if not path.is_file():
        raise invalid(f"缺少文件：{relative}。请将原文件放回工作文件夹后重试。")
    return path


def frame_series(frame, columns):
    return {
        "Time": frame.Time.to_numpy(dtype=float),
        **{
            c: pd.to_numeric(frame[c], errors="coerce").to_numpy(dtype=float)
            for c in columns
            if c in frame
        },
    }


def load(source, options):
    from app.services import bidmc, hapt

    path = storage_service.get_local_object_path(source.object_key)
    if source.file_format in {"pkl", "pickle"}:
        raise invalid("请通过受限信号预览打开分段 PKL。")
    from app.services.local_workspaces import project_root

    root = project_root(source.project_id)
    if root is None:
        raise invalid("请先打开原始数据所在的本地文件夹。")
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    if descriptor.get("schema") == bidmc.SCHEMA:
        return bidmc.load(root, descriptor)
    if descriptor.get("schema") == hapt.SCHEMA:
        return hapt.load(root, descriptor, options)
    signals = {"zephyr": {}, "e4": {}}
    for kind, columns in ZEPHYR.items():
        frames = []
        for relative in descriptor["files"]["zephyr"][kind]:
            frame = pd.read_csv(source_path(root, relative))
            if "Time" not in frame:
                raise invalid(f"{relative} 缺少 Time 列。")
            frame["Time"] = local_times(frame.Time, options.timezone, dayfirst=True)
            frames.append(frame)
        signals["zephyr"][kind] = (
            frame_series(pd.concat(frames).sort_values("Time"), columns) if frames else None
        )
    ecg = signals["zephyr"].get("ECG")
    summary = signals["zephyr"].get("Summary")
    if not ecg or "EcgWaveform" not in ecg or not len(ecg["Time"]):
        raise invalid("ECG 文件没有有效 Time / EcgWaveform 数据。")
    if not summary or not all(c in summary for c in ["HR", "HRConfidence", "ECGNoise"]):
        raise invalid("Summary 文件需要 Time、HR、HRConfidence、ECGNoise 列。")
    for kind, columns in E4.items():
        frames = []
        for relative in descriptor["files"]["e4"][kind]:
            raw = pd.read_csv(source_path(root, relative), header=None)
            if len(raw) < 3 or raw.shape[1] != len(columns):
                raise invalid(
                    f"{relative} 需要开始时间、采样率两行，随后为 {len(columns)} 个测量列。"
                )
            epoch, rate = float(raw.iloc[0, 0]), float(raw.iloc[1, 0])
            if not np.isfinite(epoch) or not np.isfinite(rate) or rate <= 0:
                raise invalid(f"{relative} 开始时间或采样率无效。")
            times = epoch + np.arange(len(raw) - 2, dtype=float) / rate + options.e4_offset
            keep = (times >= ecg["Time"][0]) & (times <= ecg["Time"][-1])
            if keep.any():
                frame = pd.DataFrame(raw.iloc[2:].to_numpy()[keep], columns=columns)
                frame["Time"] = times[keep]
                frames.append(frame)
        signals["e4"][kind] = (
            frame_series(pd.concat(frames).sort_values("Time"), columns) if frames else None
        )
    cgm = pd.read_csv(source_path(root, descriptor["files"]["cgm"]))
    required = ["Event Type", "Timestamp (YYYY-MM-DDThh:mm:ss)", "Glucose Value (mg/dL)", "Index"]
    if not all(c in cgm for c in required):
        raise invalid("cgm.csv 缺少作者格式的 Event Type、Timestamp、Glucose Value 或 Index 列。")
    cgm = cgm[cgm["Event Type"] == "EGV"].copy()
    cgm["Time"] = local_times(cgm[required[1]], options.timezone) + options.cgm_offset
    cgm["glucose"] = pd.to_numeric(
        cgm[required[2]].replace({"Low": "40", "High": "400"}), errors="coerce"
    )
    cgm["reading"] = cgm[required[2]].astype(str)
    cgm = cgm.sort_values("Time")
    if cgm.Index.duplicated().any():
        raise invalid("cgm.csv 中 EGV 的 Index 重复，请选择同一受试者的一份导出文件。")
    return {
        "schema": SCHEMA,
        "subject": descriptor["subject"],
        "session": descriptor["session"],
        "timezone": options.timezone,
        "signals": signals,
        "cgm": cgm[["Time", "glucose", "Index", "reading"]].to_dict("records"),
        "options": options.model_dump(mode="json"),
    }


def _load_package(path):
    """Only called within the bounded parser worker."""
    from app.services import bidmc, hapt
    from app.services.multimodal_import import load as restricted_load

    result = restricted_load(path)
    if not isinstance(result, dict) or result.get("schema") not in {
        SCHEMA,
        bidmc.SCHEMA,
        hapt.SCHEMA,
    }:
        raise invalid("这不是 Quantelune 原始信号窗口 PKL，请使用特征 PKL 入口。")
    return result


def _package_worker(operation, source, options, destination=None):
    from app.services.parser_control import run

    path = storage_service.get_local_object_path(source.object_key)
    try:
        result = run(
            operation,
            str(path),
            timeout=120,
            options={"signal_options": options.model_dump(mode="json"), "destination": destination},
        )
    except Exception:
        raise invalid("信号 PKL 解析超时或资源不足，请检查文件后重试。") from None
    if "error" in result:
        raise invalid(result["error"])
    return result


def source_info(source, options):
    if source.file_format in {"pkl", "pickle"}:
        return _package_worker("signal_info", source, options)
    data = load(source, options)
    return {"schema": data.get("schema"), "packaged": "windows" in data}


def source_preview(source, options):
    if source.file_format in {"pkl", "pickle"}:
        return _package_worker("signal_preview", source, options)
    return preview(load(source, options), options)


def bounds(signals):
    series = [
        s
        for device in signals.values()
        for s in device.values()
        if s is not None and len(s["Time"])
    ]
    if not series:
        raise invalid("选段内没有信号，请扩大时间范围。")
    return min(s["Time"][0] for s in series), max(s["Time"][-1] for s in series)


def align(data, options):
    signals = data["signals"]
    ecg = pd.DataFrame(signals["zephyr"]["ECG"]).sort_values("Time")
    summary = pd.DataFrame(signals["zephyr"]["Summary"])[
        ["Time", "HR", "HRConfidence", "ECGNoise"]
    ].sort_values("Time")
    if options.start is not None:
        ecg = ecg[ecg.Time >= options.start]
    if options.end is not None:
        ecg = ecg[ecg.Time <= options.end]
    if ecg.empty or not data["cgm"]:
        return []
    cgm = pd.DataFrame(data["cgm"]).rename(columns={"Time": "CGMTime"}).sort_values("CGMTime")
    paired = pd.merge_asof(
        ecg, summary, on="Time", direction="nearest", tolerance=options.summary_tolerance
    )
    paired = pd.merge_asof(
        paired,
        cgm,
        left_on="Time",
        right_on="CGMTime",
        direction="forward",
        tolerance=options.cgm_tolerance,
    )
    paired = paired.dropna(
        subset=["EcgWaveform", "HR", "HRConfidence", "ECGNoise", "Index", "glucose"]
    )
    windows = []
    for index, frame in paired.groupby("Index", sort=False):
        windows.append(
            {
                "Index": str(index),
                "Timestamp": float(frame.CGMTime.iloc[0]),
                "glucose": float(frame.glucose.iloc[0]),
                "reading": str(frame.reading.iloc[0]),
                "start": float(frame.Time.min()),
                "end": float(frame.Time.max()),
                "matched_ecg_samples": len(frame),
            }
        )
    return windows


def slice_signals(signals, start, end):
    result = {}
    for device, streams in signals.items():
        result[device] = {}
        for kind, series in streams.items():
            if series is None:
                result[device][kind] = None
                continue
            mask = (series["Time"] >= start) & (series["Time"] <= end)
            result[device][kind] = (
                {k: np.asarray(v)[mask] for k, v in series.items()} if mask.any() else None
            )
    return result


def source_fingerprint(source, options=None):
    """Identity of the actual measurements, including a session's linked files."""
    path = storage_service.get_local_object_path(source.object_key)
    digest = hashlib.sha256()
    if source.file_format == "physiocgm" and options is not None:
        digest.update(
            json.dumps(
                {key: getattr(options, key) for key in ("timezone", "e4_offset", "cgm_offset")},
                sort_keys=True,
            ).encode()
        )
    paths = [("source", path)]
    if source.file_format in {"bidmc", "physiocgm", "hapt"}:
        from app.services.local_workspaces import project_root

        root = project_root(source.project_id)
        descriptor = json.loads(path.read_text(encoding="utf-8"))

        def relatives(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from relatives(child)
            elif isinstance(value, list):
                for child in value:
                    yield from relatives(child)

        paths.extend(
            (relative, source_path(root, relative))
            for relative in sorted(set(relatives(descriptor["files"])))
        )
    for name, file in paths:
        digest.update(name.encode("utf-8"))
        with file.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


async def read_view(db, user, source, options):
    import anyio

    from app.services import project_assistant_tasks as tasks

    if options.task_id:
        await tasks.context(db, source.project_id, user, str(options.task_id))
    result = await anyio.to_thread.run_sync(source_preview, source, options)
    result["source_fingerprint"] = await anyio.to_thread.run_sync(
        source_fingerprint, source, options
    )
    return result


async def apply_view(db, user, source, options):
    """Apply and persist the same task-local view for UI and conversational tools."""
    from app.preview.write_fence import acquire_write_fence

    result = await read_view(db, user, source, options)
    await acquire_write_fence(db, user.id)
    await db.refresh(source)
    metadata = json.loads(source.preview_json or "{}")
    snapshot = {
        **options.model_dump(mode="json"),
        "start": result["selection"][0],
        "end": result["selection"][1],
    }
    metadata.setdefault("signal_options", {})[str(options.task_id or "project")] = snapshot
    source.preview_json = json.dumps(metadata)
    await db.commit()
    return result, snapshot


def preview(data, options):
    from app.services import bidmc, hapt

    if data.get("schema") == bidmc.SCHEMA:
        return bidmc.preview(data, options)
    if data.get("schema") == hapt.SCHEMA:
        return hapt.preview(data, options)
    packaged = "windows" in data
    windows = data["windows"] if packaged else []
    if packaged:
        index = options.window or 0
        if index >= len(windows):
            raise invalid("窗口不存在，请重新选择。")
        chosen = windows[index]
        signals = {d: chosen[d] for d in ("zephyr", "e4")}
        cgm = [
            {"Time": chosen["Timestamp"], "glucose": chosen["glucose"], "Index": chosen["Index"]}
        ]
    else:
        signals, cgm = data["signals"], data["cgm"]
    low, high = bounds(signals)
    start = options.start if options.start is not None else low
    end = options.end if options.end is not None else min(high, low + 600)
    if start >= end:
        raise invalid("结束时间必须晚于开始时间。")
    if not packaged:
        windows = align(data, options.model_copy(update={"start": start, "end": end}))
    tracks = []
    for device, streams in signals.items():
        for kind, series in streams.items():
            if series is None:
                for channel in (ZEPHYR if device == "zephyr" else E4)[kind]:
                    tracks.append(
                        {
                            "id": f"{device}.{kind}.{channel}",
                            "name": f"{device} · {channel}",
                            "rate": None,
                            "samples": 0,
                            "missing": 0,
                            "points": [],
                        }
                    )
                continue
            times = np.asarray(series["Time"])
            np.flatnonzero((times >= start) & (times <= end))
            dt = np.diff(times)
            step = float(np.median(dt[dt > 0])) if (dt > 0).any() else None
            from app.services.signal_preview import track

            for channel, values in series.items():
                if channel == "Time":
                    continue
                tracks.append(
                    track(
                        f"{device}.{kind}.{channel}",
                        f"{device} · {channel}",
                        times,
                        values,
                        start,
                        end,
                        None if step is None else 1 / step,
                    )
                )
    ecg, bvp = signals["zephyr"].get("ECG"), signals["e4"].get("BVP")
    common = None
    if ecg is not None and bvp is not None and len(ecg["Time"]) and len(bvp["Time"]):
        overlap = [
            float(max(start, ecg["Time"][0], bvp["Time"][0])),
            float(min(end, ecg["Time"][-1], bvp["Time"][-1])),
        ]
        if overlap[0] < overlap[1]:
            common = overlap
    return {
        "subject": data["subject"],
        "session": data["session"],
        "timezone": data.get("timezone"),
        "packaged": packaged,
        "range": [float(low), float(high)],
        "selection": [start, end],
        "common": common,
        "tracks": tracks,
        "cgm": [
            {**r, "glucose": r["glucose"] if np.isfinite(r["glucose"]) else None}
            for r in cgm
            if start <= r["Time"] <= end + options.cgm_tolerance
        ],
        "windows": [
            {
                k: w[k]
                for k in ("Index", "Timestamp", "glucose", "start", "end", "matched_ecg_samples")
            }
            for w in windows
        ],
    }


async def package(db, user, source, options):
    import anyio

    from app.services import project_assistant_tasks as tasks

    if options.task_id:
        await tasks.context(db, source.project_id, user, str(options.task_id))
    if source.file_format in {"pkl", "pickle"}:
        with tempfile.TemporaryDirectory(prefix="sf-physio-pack-") as temporary:
            metadata = await anyio.to_thread.run_sync(
                _package_worker, "signal_package", source, options, temporary
            )
            return await save_staged_package(db, user, source, options, Path(temporary), metadata)
    data = await anyio.to_thread.run_sync(load, source, options)
    bundle, records = await anyio.to_thread.run_sync(package_data, data, options)
    return await save_package(db, user, source, options, bundle, records)


def package_data(data, options):
    from app.services import bidmc, hapt

    if "windows" in data:
        raise invalid("此文件已经分段，请打开原会话调整选段。")
    if data.get("schema") == hapt.SCHEMA:
        return hapt.package_data(data, options)
    if data.get("schema") == bidmc.SCHEMA:
        return bidmc.package_data(data, options)
    windows = align(data, options)
    if not windows:
        raise invalid(
            "没有匹配到有效 CGM 窗口。请核对时区、共同时间段及 Summary"
            " 的 HR/HRConfidence/ECGNoise 数据。"
        )
    for window in windows:
        window.update(slice_signals(data["signals"], window["start"], window["end"]))
    bundle = {
        "schema": SCHEMA,
        "subject": data["subject"],
        "session": data["session"],
        "timezone": options.timezone,
        "time_unit": "UTC Unix seconds",
        "options": options.model_dump(mode="json"),
        "windows": windows,
    }
    bundle["channels"] = {
        f"{device}.{kind}": {
            "columns": [k for k in series if k != "Time"],
            "sample_interval_seconds": float(np.median(np.diff(series["Time"])))
            if len(series["Time"]) > 1
            else None,
        }
        for device, streams in data["signals"].items()
        for kind, series in streams.items()
        if series is not None
    }
    records = []
    for w in windows:
        record = {
            k: w[k]
            for k in ("Index", "Timestamp", "glucose", "start", "end", "matched_ecg_samples")
        }
        record["duration_seconds"] = w["end"] - w["start"]
        for device in ("zephyr", "e4"):
            for kind, series in w[device].items():
                record[f"{device}_{kind}_samples"] = (
                    len(series["Time"]) if series is not None else 0
                )
                record[f"{device}_{kind}_missing"] = series is None
        records.append(record)
    return bundle, records


def stage_package(bundle, records, directory):
    root = Path(directory)
    with (root / "windows.pkl").open("wb") as stream:
        pickle.dump(bundle, stream, protocol=5)
    pd.DataFrame(records).to_csv(root / "windows.csv", index=False)
    windows = bundle["windows"]
    return {
        "schema": bundle["schema"],
        "subject": str(bundle["subject"]),
        "session": str(bundle["session"]),
        "count": len(windows),
        "first_start": windows[0]["start"],
        "last_end": windows[-1]["end"],
    }


async def save_package(db, user, source, options, bundle, records):
    with tempfile.TemporaryDirectory(prefix="sf-physio-pack-") as temporary:
        metadata = stage_package(bundle, records, temporary)
        return await save_staged_package(db, user, source, options, Path(temporary), metadata)


async def save_staged_package(db, user, source, options, temporary, result):
    from app.preview.write_fence import acquire_write_fence
    from app.services import bidmc
    from app.services import project_assistant_tasks as tasks

    stamp = uuid.uuid4().hex[:10]
    if options.task_id:
        await acquire_write_fence(db, user.id)
    # Input metadata may come from a user-selected PKL: never use it as a path.
    subject = (
        "".join(c if c.isalnum() or c in "-_" else "_" for c in result["subject"])[:80] or "signal"
    )
    name = f"{subject}.{stamp}.windows.pkl"
    packed = await project_sources.store(
        db, source.project_id, user.id, str(temporary / "windows.pkl"), name, f"分段结果/{name}"
    )
    interval = options.window_seconds
    label = (
        f"{result['first_start']:g}–{result['last_end']:g}秒 "
        f"· 每窗{interval:g}秒 · 步长{(options.step_seconds or interval):g}秒"
        if result["schema"] == bidmc.SCHEMA
        else f"{result['session']} · {result['count']}个窗口"
    )
    packed.preview_json = json.dumps(
        {
            "raw_file": True,
            "signal_session": True,
            "source_dataset_id": str(source.id),
            "display_name": f"{label} · PKL",
            "source_path": f"分段结果/{name}",
        }
    )
    packed.upload_status = UploadStatus.READY
    table_name = f"{subject}.{stamp}.windows.csv"
    summary = await dataset_service.upload_dataset(
        db,
        source.project_id,
        user.id,
        str(temporary / "windows.csv"),
        table_name,
        source_path=f"窗口摘要/{table_name}",
        allow_duplicate=True,
    )
    metadata = json.loads(summary.preview_json or "{}")
    metadata["source_dataset_id"] = str(packed.id)
    metadata["display_name"] = f"{label} · 摘要"
    summary.preview_json = json.dumps(metadata)
    if options.task_id:
        live = SimpleNamespace(
            project=source.project_id, value={"task_context": {"id": str(options.task_id)}}
        )
        for identity in (source.id, packed.id, summary.id):
            await tasks.append(db, user, live, "dataset_ids", identity)
    await db.flush()
    return {"package_id": str(packed.id), "summary_id": str(summary.id), "windows": result["count"]}
