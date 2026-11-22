"""Native Pitch file operations using the existing local media Python."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np

from app.services.multimodal_cases import invalid, runtime_config


def clock_for(data, entry, parameters):
    if entry.get("pitch_clock"):
        return {key: float(entry["pitch_clock"][key]) for key in ("xmin", "xmax", "dx", "x1")}
    # Older results already store the actual extraction timestamp and sample count.
    # Recover that domain from saved metadata, never by re-reading the recording.
    first = entry.get("praat_audio_first_pts")
    count, rate = entry.get("praat_audio_sample_count"), entry.get("praat_audio_sample_rate")
    step = parameters.get("praat_time_step")
    if first is None or not count or not rate or not step:
        raise invalid("这份旧结果缺少原始基频时钟，不能导出或重选；原曲线仍可查看。")
    return {
        "xmin": float(first),
        "xmax": float(first) + count / rate,
        "dx": float(step),
        "x1": float(data["times"][0]),
    }


def native(action, request, directory):
    config = runtime_config()
    directory = Path(directory)
    request_path, metadata_path = directory / "pitch-request.json", directory / "pitch-native.json"
    request_path.write_text(
        json.dumps({**request, "output_json": str(metadata_path)}, ensure_ascii=False),
        encoding="utf-8",
    )
    command = [
        config["python"],
        "-B",
        str(Path(__file__).with_name("pitch_object.py")),
        action,
        str(request_path),
    ]
    result = subprocess.run(
        command,
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
    if result.returncode:
        (directory / "pitch-error.log").write_text(result.stderr, encoding="utf-8")
        raise invalid(
            {
                "read": "无法读取这个 Pitch 文件，请确认它是 Praat 的 Pitch 对象。",
                "write": "无法导出基频，请重新打开当前保存版本后重试。",
                "reselect": "基频路径重选失败，请检查阈值、代价和上限。",
            }[action]
        )
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def check_package(path, expected):
    with Path(path).open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise invalid("这份结果文件已变化，请重新打开保存的版本。")


def reselect(path, prefix, clock, parameters, directory):
    selected_path = Path(directory) / "pitch-selected.npy"
    metadata = native(
        "reselect",
        {
            "input_npz": str(path),
            "prefix": prefix + "_pitch_",
            "clock": clock,
            "parameters": parameters,
            "output_npy": str(selected_path),
        },
        directory,
    )
    return np.load(selected_path, allow_pickle=False), metadata


def export_file(path, expected, prefix, clock, format, directory):
    check_package(path, expected)
    destination = Path(directory) / "selected.Pitch"
    native(
        "write",
        {
            "input_npz": str(path),
            "prefix": prefix + "_pitch_",
            "clock": clock,
            "format": format,
            "output_path": str(destination),
        },
        directory,
    )
    return destination
