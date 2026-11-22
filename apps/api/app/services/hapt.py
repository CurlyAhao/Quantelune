"""HAPT raw six-axis sessions; original label integers are retained."""

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

SCHEMA = "statforge-hapt-v1"
CHANNELS = ("acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z")


def invalid(message):
    from app.services.physiocgm import invalid as error

    return error(message)


async def discover(db, project, owner, root, rows):
    for acc in sorted(root.glob("**/acc_exp*_user*.txt")):
        match = re.fullmatch(r"acc_exp(\d+)_user(\d+)\.txt", acc.name)
        if not match or ".statforge" in acc.relative_to(root).parts:
            continue
        exp, user = match.groups()
        gyro = acc.with_name(f"gyro_exp{exp}_user{user}.txt")
        labels = acc.with_name("labels.txt")
        names = acc.parent.parent / "activity_labels.txt"
        if not all(p.is_file() for p in (gyro, labels, names)):
            continue
        identity = acc.relative_to(root).as_posix()
        if any(
            json.loads(row.preview_json or "{}").get("hapt_session") == identity for row in rows
        ):
            continue
        descriptor = {
            "schema": SCHEMA,
            "subject": f"user{user}",
            "session": f"exp{exp}",
            "experiment": int(exp),
            "user": int(user),
            "files": {
                k: p.relative_to(root).as_posix()
                for k, p in (
                    ("acc", acc),
                    ("gyro", gyro),
                    ("labels", labels),
                    ("activity_labels", names),
                )
            },
        }
        with tempfile.TemporaryDirectory(prefix="sf-hapt-") as temporary:
            path = Path(temporary) / f"exp{exp}_user{user}.hapt"
            path.write_text(json.dumps(descriptor), encoding="utf-8")
            row = await project_sources.store(
                db, project.id, owner, str(path), path.name, f"会话/{path.name}"
            )
            row.preview_json = json.dumps(
                {
                    "raw_file": True,
                    "signal_session": True,
                    "hapt_session": identity,
                    "source_path": row.source_path,
                }
            )
            row.upload_status = UploadStatus.READY
            rows.append(row)


def load(root, descriptor, options):
    from app.services.physiocgm import source_path

    files = {k: source_path(root, v) for k, v in descriptor["files"].items()}
    acc = np.loadtxt(files["acc"], ndmin=2)
    gyro = np.loadtxt(files["gyro"], ndmin=2)
    if (
        acc.shape != gyro.shape
        or acc.shape[1] != 3
        or len(acc) < 2
        or not (np.isfinite(acc).all() and np.isfinite(gyro).all())
    ):
        raise invalid("HAPT 加速度与角速度必须是同长度、三列的有效数值。")
    raw = np.loadtxt(files["labels"], dtype=np.int64, ndmin=2)
    names = {}
    for line in files["activity_labels"].read_text(encoding="utf-8-sig").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            names[int(parts[0])] = parts[1]
    events = []
    for row_number, row in enumerate(raw, 1):
        if len(row) != 5:
            raise invalid("HAPT 活动标注需要五列。")
        exp, user, activity, start, end = map(int, row)
        if exp != descriptor["experiment"] or user != descriptor["user"]:
            continue
        lo = start - options.hapt_index_base
        hi = end - options.hapt_index_base + int(options.hapt_end_inclusive)
        if lo < 0 or hi > len(acc) or lo >= hi:
            raise invalid("活动索引超出信号范围，请检查索引解释。")
        events.append(
            {
                "activity_id": activity,
                "name": names.get(activity, str(activity)),
                "original_start": start,
                "original_end": end,
                "start_index": lo,
                "end_index_exclusive": hi,
                "source_row": row_number,
                "source_file": descriptor["files"]["labels"],
            }
        )
    return {
        "schema": SCHEMA,
        "subject": descriptor["subject"],
        "session": descriptor["session"],
        "sample_id": f"{descriptor['session']}_{descriptor['subject']}",
        "values": np.column_stack((acc, gyro)),
        "events": events,
        "rate": 50,
        "files": descriptor["files"],
        "index_interpretation": {
            "base": options.hapt_index_base,
            "end_inclusive": options.hapt_end_inclusive,
            "evidence": "假设，未获作者说明证实；README v2.1 未明确索引起点和终点是否包含。",
        },
        "units": {**{c: "g" for c in CHANNELS[:3]}, **{c: "rad/s" for c in CHANNELS[3:]}},
    }


def selection(data, options):
    n = len(data["values"])
    start = max(0, math.ceil((options.start or 0) * 50 - 1e-9))
    end = min(
        n, math.floor((options.end if options.end is not None else (n - 1) / 50) * 50 + 1e-9) + 1
    )
    if end - start < 2:
        raise invalid("选段没有足够样本，请扩大选段。")
    return start, end


def windows(data, options):
    first, stop = selection(data, options)
    length = round(options.hapt_window_seconds * 50)
    step = round(options.hapt_step_seconds * 50)
    if (
        length < 1
        or step < 1
        or abs(length / 50 - options.hapt_window_seconds) > 1e-8
        or abs(step / 50 - options.hapt_step_seconds) > 1e-8
    ):
        raise invalid("窗口长度和步长须为 0.02 秒的整数倍。")
    result = []
    for index, lo in enumerate(range(first, stop, step)):
        hi = min(lo + length, stop)
        overlapping = [
            {
                **e,
                "overlap_start_index": max(lo, e["start_index"]),
                "overlap_end_index_exclusive": min(hi, e["end_index_exclusive"]),
            }
            for e in data["events"]
            if e["start_index"] < hi and e["end_index_exclusive"] > lo
        ]
        covered = np.zeros(hi - lo, dtype=bool)
        for e in overlapping:
            covered[e["overlap_start_index"] - lo : e["overlap_end_index_exclusive"] - lo] = True
        result.append(
            {
                "Index": str(index + 1),
                "start": lo / 50,
                "end": (hi - 1) / 50,
                "end_inclusive": True,
                "start_index": lo,
                "end_index_exclusive": hi,
                "valid_length": hi - lo,
                "events": overlapping,
                "unlabeled_samples": int((~covered).sum()),
            }
        )
        if hi == stop:
            break
    return result


def package_data(data, options):
    result, records = [], []
    for w in windows(data, options):
        values = data["values"][w["start_index"] : w["end_index_exclusive"]].copy()
        result.append(
            {
                **w,
                "values": values,
                "time": np.arange(w["start_index"], w["end_index_exclusive"]) / 50,
                "sample_index": np.arange(w["start_index"], w["end_index_exclusive"]),
                "original_row_1based": np.arange(w["start_index"], w["end_index_exclusive"]) + 1,
            }
        )
        record = {
            k: w[k]
            for k in (
                "Index",
                "start",
                "end",
                "start_index",
                "end_index_exclusive",
                "valid_length",
                "unlabeled_samples",
            )
        }
        record.update(
            sample_id=data["sample_id"], activities=json.dumps(w["events"], ensure_ascii=False)
        )
        for i, channel in enumerate(CHANNELS):
            x = values[:, i]
            record.update(
                {
                    f"{channel}_mean": float(np.mean(x)),
                    f"{channel}_std": float(np.std(x)),
                    f"{channel}_rms": float(np.sqrt(np.mean(x * x))),
                }
            )
        records.append(record)
    bundle = {k: v for k, v in data.items() if k != "values"}
    bundle.update(
        windows=result,
        options=options.model_dump(mode="json"),
        tools={"numpy": np.__version__, "pandas": pd.__version__},
        boundary_rule=(
            "sample indices [start,end); final short window retained with"
            "out padding; all overlapping activities retained"
        ),
        processing=(
            "50 Hz raw values, no filter or resampling; summaries are not the original 561 features"
        ),
    )
    return bundle, records


def preview(data, options):
    packaged = "windows" in data
    if packaged:
        index = options.window or 0
        if index >= len(data["windows"]):
            raise invalid("窗口不存在，请重新选择。")
        chosen = data["windows"][index]
        values, offset = chosen["values"], chosen["start_index"]
        low, high = chosen["start"], chosen["end"]
        start, end = (
            max(low, options.start if options.start is not None else low),
            min(high, options.end if options.end is not None else high),
        )
        items = [
            {k: w[k] for k in ("Index", "start", "end", "end_inclusive")} for w in data["windows"]
        ]
    else:
        values, offset = data["values"], 0
        low, high = 0.0, (len(values) - 1) / 50
        first, stop = selection(data, options)
        start, end = first / 50, (stop - 1) / 50
        items = windows(data, options)
    if start >= end:
        raise invalid("结束时间必须晚于开始时间。")
    times = (np.arange(len(values)) + offset) / 50
    tracks = [
        track(c, f"{c} · {data['units'][c]}", times, values[:, i], start, end, 50)
        for i, c in enumerate(CHANNELS)
    ]
    events = [
        e
        for e in data["events"]
        if e["start_index"] / 50 <= end and (e["end_index_exclusive"] - 1) / 50 >= start
    ]
    return {
        "kind": "hapt",
        "subject": data["subject"],
        "session": data["session"],
        "packaged": packaged,
        "range": [low, high],
        "selection": [start, end],
        "common": [start, end],
        "tracks": tracks,
        "cgm": [],
        "windows": items,
        "activities": events,
        "index_interpretation": data["index_interpretation"],
        "default_channels": list(CHANNELS),
    }
