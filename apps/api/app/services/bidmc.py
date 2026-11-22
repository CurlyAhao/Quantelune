"""BIDMC CSV reader: native rates, shared relative time, no CGM matching."""

import json
import math
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.models.dataset import UploadStatus
from app.services import project_sources
from app.services.signal_preview import track

SCHEMA = "statforge-bidmc-v1"
WAVES = {
    "RESP": "呼吸波形 · RESP",
    "PLETH": "脉搏波 · PLETH",
    "V": "心电 · V",
    "AVR": "心电 · AVR",
    "II": "心电 · II",
}
NUMERICS = {
    "HR": "心率 · HR (bpm)",
    "PULSE": "脉率 · PULSE (bpm)",
    "RESP": "呼吸率 · RESP (次/分)",
    "SpO2": "血氧 · SpO2 (%)",
}
ATTRIBUTION = {
    "dataset": "BIDMC PPG and Respiration Dataset v1.0.0",
    "doi": "10.13026/C2208R",
    "license": "Open Data Commons Attribution License v1.0",
    "authors": "Pimentel et al.",
    "url": "https://physionet.org/content/bidmc/1.0.0/",
}


def invalid(message):
    from app.services.physiocgm import invalid as error

    return error(message)


async def discover(db, project, owner, root, rows):
    for signal in sorted(root.glob("**/bidmc_*_Signals.csv")):
        if ".statforge" in signal.relative_to(root).parts:
            continue
        name = signal.name.removesuffix("_Signals.csv")
        files = {
            kind: signal.with_name(f"{name}_{kind}.{ext}")
            for kind, ext in [
                ("Signals", "csv"),
                ("Numerics", "csv"),
                ("Breaths", "csv"),
                ("Fix", "txt"),
            ]
        }
        if not all(path.is_file() for path in files.values()):
            continue
        identity = signal.relative_to(root).as_posix()
        if any(
            json.loads(row.preview_json or "{}").get("bidmc_session") == identity for row in rows
        ):
            continue
        descriptor = {
            "schema": SCHEMA,
            "subject": name,
            "session": name,
            "files": {kind: path.relative_to(root).as_posix() for kind, path in files.items()},
        }
        with tempfile.TemporaryDirectory(prefix="sf-bidmc-") as temporary:
            path = Path(temporary) / f"{name}.bidmc"
            path.write_text(json.dumps(descriptor), encoding="utf-8")
            row = await project_sources.store(
                db, project.id, owner, str(path), path.name, f"会话/{path.name}"
            )
            row.preview_json = json.dumps(
                {
                    "raw_file": True,
                    "signal_session": True,
                    "bidmc_session": identity,
                    "source_path": row.source_path,
                }
            )
            row.upload_status = UploadStatus.READY
            rows.append(row)


def read_csv(path):
    frame = pd.read_csv(path)
    frame.columns = frame.columns.str.strip()
    return frame


def load(root, descriptor):
    from app.services.physiocgm import source_path

    files = {k: source_path(root, v) for k, v in descriptor["files"].items()}
    fix = files["Fix"].read_text(encoding="utf-8-sig")
    rates = {}
    streams = {}
    for group, names in (("Signals", WAVES), ("Numerics", NUMERICS)):
        match = re.search(rf"{group} sampling frequency:\s*([\d.]+)\s*Hz", fix)
        if not match or float(match[1]) <= 0:
            raise invalid(f"Fix.txt 缺少有效 {group} 采样率。")
        rate = rates[group] = float(match[1])
        frame = read_csv(files[group])
        if not {"Time [s]", *names}.issubset(frame.columns) or frame.empty:
            raise invalid(f"{group}.csv 缺少时间或 BIDMC 通道列。")
        original_time = pd.to_numeric(frame["Time [s]"], errors="raise").to_numpy(dtype=float)
        if not np.isfinite(original_time).all() or np.any(np.diff(original_time) < 0):
            raise invalid(f"{group}.csv 时间有缺失或倒序，请核对原文件。")
        restored = original_time[0] + np.arange(len(frame), dtype=float) / rate
        deviation = float(np.max(np.abs(original_time - restored)))
        if deviation > 0.51 / rate:
            raise invalid(f"{group}.csv 时间偏差超过半个采样间隔，不能按均匀样本序号恢复。")
        streams[group] = {
            "Time": restored,
            "csv_time": original_time,
            "sample_index": np.arange(len(frame)),
            **{c: pd.to_numeric(frame[c], errors="coerce").to_numpy(dtype=float) for c in names},
        }
    breaths = read_csv(files["Breaths"])
    events = {}
    for annotator in (1, 2):
        column = f"breaths ann{annotator} [signal sample no]"
        if column not in breaths:
            raise invalid(f"Breaths.csv 缺少第 {annotator} 位标注者列。")
        values = pd.to_numeric(breaths[column], errors="raise").to_numpy(dtype=float)
        valid = np.isfinite(values)
        indices = values[valid]
        if (
            np.any(indices != np.floor(indices))
            or np.any(indices < 1)
            or np.any(indices > len(streams["Signals"]["Time"]))
        ):
            raise invalid("呼吸标注包含越界或非整数的样本号，请核对 Breaths.csv。")
        events[f"ann{annotator}"] = {
            "Time": streams["Signals"]["Time"][0] + (indices - 1) / rates["Signals"],
            "sample_number": indices.astype(np.int64),
            "annotation_row": np.flatnonzero(valid),
        }
    time = streams["Signals"]
    return {
        "schema": SCHEMA,
        "subject": descriptor["subject"],
        "session": descriptor["session"],
        "time_unit": "relative seconds",
        "streams": streams,
        "events": events,
        "rates": rates,
        "annotation_index_base": 1,
        "attribution": ATTRIBUTION,
        "time_precision": {
            "method": "first_csv_time + sample_index / sampling_frequency",
            "original_column": "csv_time",
            "duplicate_display_times": int(
                len(time["csv_time"]) - len(np.unique(time["csv_time"]))
            ),
            "maximum_deviation_seconds": float(np.max(np.abs(time["Time"] - time["csv_time"]))),
        },
    }


def selection(data, options):
    values = data["streams"]["Signals"]["Time"]
    start = max(float(values[0]), options.start if options.start is not None else float(values[0]))
    end = min(float(values[-1]), options.end if options.end is not None else float(values[-1]))
    if start >= end:
        raise invalid("选段没有有效时长，请在记录范围内选择开始和结束秒数。")
    return start, end


def windows(data, options):
    start, end = selection(data, options)
    step = options.step_seconds if options.step_seconds is not None else options.window_seconds
    count = math.ceil((end - start) / step)
    return [
        {
            "Index": str(i + 1),
            "start": start + i * step,
            "end": min(end, start + i * step + options.window_seconds),
            "end_inclusive": i == count - 1,
        }
        for i in range(count)
    ]


def cut(series, window):
    times = np.asarray(series["Time"])
    mask = (times >= window["start"]) & (
        (times <= window["end"]) if window["end_inclusive"] else (times < window["end"])
    )
    return {k: np.asarray(v)[mask] for k, v in series.items()}


def package_data(data, options):
    result, records = [], []
    for window in windows(data, options):
        streams = {k: cut(v, window) for k, v in data["streams"].items()}
        events = {k: cut(v, window) for k, v in data["events"].items()}
        result.append({**window, "streams": streams, "events": events})
        record = {**window, "duration_seconds": window["end"] - window["start"]}
        for group, columns in (("Signals", WAVES), ("Numerics", NUMERICS)):
            record[f"{group}_samples"] = len(streams[group]["Time"])
            for column in columns:
                values = streams[group][column]
                finite = values[np.isfinite(values)]
                record[f"{group}_{column}_missing"] = int(len(values) - len(finite))
                if group == "Numerics":
                    record[f"{group}_{column}_mean"] = (
                        float(np.mean(finite)) if len(finite) else None
                    )
        for who, series in events.items():
            record[f"breaths_{who}_count"] = len(series["Time"])
        records.append(record)
    bundle = {k: v for k, v in data.items() if k not in {"streams", "events"}}
    bundle.update(
        windows=result,
        options=options.model_dump(mode="json"),
        boundary_rule="[start,end), last window includes its end; step may create overlaps or gaps",
        summary_view="Counts and numeric means only; complete sequences are in windows",
    )
    return bundle, records


def preview(data, options):
    packaged = "windows" in data
    if packaged:
        index = options.window or 0
        if index >= len(data["windows"]):
            raise invalid("窗口不存在，请重新选择。")
        chosen = data["windows"][index]
        streams, events = chosen["streams"], chosen["events"]
        low, high = chosen["start"], chosen["end"]
        start = max(low, options.start if options.start is not None else low)
        end = min(high, options.end if options.end is not None else high)
        items = [
            {k: w[k] for k in ("Index", "start", "end", "end_inclusive")} for w in data["windows"]
        ]
    else:
        streams, events = data["streams"], data["events"]
        low, high = float(streams["Signals"]["Time"][0]), float(streams["Signals"]["Time"][-1])
        start, end = selection(data, options)
        items = windows(data, options)
    if start >= end:
        raise invalid("结束时间必须晚于开始时间。")
    tracks = []
    for group, columns in (("Signals", WAVES), ("Numerics", NUMERICS)):
        for column, label in columns.items():
            tracks.append(
                track(
                    f"{group}.{column}",
                    label,
                    streams[group]["Time"],
                    streams[group][column],
                    start,
                    end,
                    data["rates"][group],
                )
            )
    for who, series in events.items():
        selected = (series["Time"] >= start) & (series["Time"] <= end)
        tracks.append(
            {
                "id": who,
                "name": f"呼吸事件 · 标注者 {who[-1]}",
                "events": True,
                "rate": None,
                "samples": int(selected.sum()),
                "missing": 0,
                "points": [
                    [float(t), int(n)]
                    for t, n in zip(series["Time"][selected], series["sample_number"][selected])
                ],
            }
        )
    ranges = [(s["Time"][0], s["Time"][-1]) for s in streams.values() if len(s["Time"])]
    common = (
        [float(max(start, *(r[0] for r in ranges))), float(min(end, *(r[1] for r in ranges)))]
        if len(ranges) == 2
        else None
    )
    return {
        "kind": "bidmc",
        "subject": data["subject"],
        "session": "BIDMC",
        "packaged": packaged,
        "range": [low, high],
        "selection": [start, end],
        "common": common,
        "tracks": tracks,
        "cgm": [],
        "windows": items,
        "time_precision": data["time_precision"],
        "default_channels": [
            "Signals.RESP",
            "Signals.PLETH",
            "Signals.II",
            "Numerics.RESP",
            "Numerics.SpO2",
        ],
        "attribution": data["attribution"],
    }
