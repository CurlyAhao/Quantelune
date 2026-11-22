"""Fixed Praat pitch runner shared by the API and the local media worker."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("praat_scripts") / "filtered_pitch.praat"
FLAGS = ["--no-pref-files", "--no-plugins", "--utf8"]


def config_path():
    from app.config import settings
    from app.services.multimodal_cases import invalid

    if not settings.desktop_mode or not settings.app_data_path:
        raise invalid("请在桌面版中配置本机 Praat 程序。", 403)
    return Path(settings.app_data_path) / "tools" / "praat.json"


def configured_path():
    path = config_path()
    return json.loads(path.read_text(encoding="utf-8")).get("path") if path.is_file() else None


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pitch_parameters(options):
    filtered = options.get("pitch_method", "raw_autocorrelation").startswith("filtered_")
    defaults = {
        "silence_threshold": 0.09 if filtered else 0.03,
        "voicing_threshold": 0.50 if filtered else 0.45,
        "octave_cost": 0.055 if filtered else 0.01,
        "octave_jump_cost": 0.35,
        "voiced_unvoiced_cost": 0.14,
        "max_number_of_candidates": 15,
        "very_accurate": False,
    }
    result = {
        key: options.get("pitch_" + key) if options.get("pitch_" + key) is not None else value
        for key, value in defaults.items()
    }
    result.update(
        time_step=options.get("praat_time_step", 0.01),
        pitch_floor=options.get("pitch_floor_hz", 75),
        pitch_ceiling=options.get("pitch_ceiling_hz", 600),
    )
    if filtered:
        result["attenuation_at_top"] = options.get("pitch_attenuation_at_top", 0.03)
    return result


def praat_identity(path):
    executable = Path(path).resolve() if path else None
    if not executable or not executable.is_file():
        raise ValueError("滤波基频需要本机 Praat 7，请在基频方法旁选择 Praat 程序。")
    try:
        result = subprocess.run(
            [str(executable), *FLAGS, "--version"],
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValueError("无法读取 Praat 版本，请重新选择可运行的 Praat 7 程序。") from exc
    match = re.search(r"Praat (\d+)\.(\d+)\.(\d+)", result.stdout)
    if not match or int(match[1]) < 7:
        raise ValueError("所选程序不支持本次滤波基频，请选择 Praat 7 程序。")
    return {
        "engine": "Praat",
        "version": ".".join(match.groups()),
        "path": str(executable),
        "sha256": sha256(executable),
        "script_sha256": sha256(SCRIPT),
    }


def runtime_identity(config, method, executable=None):
    if method.startswith("filtered_"):
        return praat_identity(executable)
    # Resolve the actual media environment, not the API interpreter's installed packages.
    code = (
        "import json,parselmouth,hashlib;from pathlib import Path;"
        "p=Path(parselmouth.__file__);"
        "print(json.dumps(dict(engine='Parselmouth',version=parselmouth.__version__,"
        "praat_version=parselmouth.PRAAT_VERSION,sha256=hashlib.sha256(p.read_bytes()).hexdigest())))"
    )
    try:
        result = subprocess.run(
            [config["python"], "-B", "-c", code],
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            env={
                **os.environ,
                "TEMP": config["temp"],
                "TMP": config["temp"],
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("无法读取原始基频组件，请检查当前桌面版的本地声音环境。") from exc


def filtered_pitch(audio, options):
    expected = options.get("pitch_runtime")
    if not expected or expected.get("engine") != "Praat":
        raise ValueError("尚未配置滤波基频，请在基频方法旁选择 Praat 7 程序。")
    actual = praat_identity(expected.get("path"))
    if actual != expected:
        raise ValueError("Praat 程序或测量脚本在启动后发生变化，请重新运行以保存新版本的结果。")
    p = pitch_parameters(options)
    pitch_file = Path(audio).with_suffix(".Pitch")
    method = options.get("pitch_method", "filtered_autocorrelation")
    args = [
        str(Path(audio).resolve()),
        str(pitch_file.resolve()),
        "yes" if method == "filtered_cross_correlation" else "no",
        p["time_step"],
        p["pitch_floor"],
        p["pitch_ceiling"],
        p["max_number_of_candidates"],
        "yes" if p["very_accurate"] else "no",
        p["attenuation_at_top"],
        p["silence_threshold"],
        p["voicing_threshold"],
        p["octave_cost"],
        p["octave_jump_cost"],
        p["voiced_unvoiced_cost"],
    ]
    try:
        subprocess.run(
            [actual["path"], "--run", *FLAGS, str(SCRIPT), *map(str, args)],
            check=True,
            capture_output=True,
            encoding="utf-8",
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except subprocess.CalledProcessError as exc:
        Path(audio).with_suffix(".pitch.log").write_text(exc.stderr or "", encoding="utf-8")
        # The worker log retains Praat's actual error; the run UI receives a short next action.
        raise RuntimeError("Praat 滤波基频未完成，请检查所选片段长度与基频范围后重试。") from exc
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("Praat 程序无法运行，请在基频方法旁重新选择程序。") from exc
    import parselmouth

    pitch = parselmouth.read(str(pitch_file))
    if not isinstance(pitch, parselmouth.Pitch) or not pitch.n_frames:
        raise ValueError("Praat 未生成基频帧，请扩大选段或检查基频下限。")
    return pitch


def pitch_arrays(pitch):
    """Preserve every native candidate in its original order, including unvoiced 0."""
    import numpy as np

    frames = [pitch.get_frame(i) for i in range(1, pitch.n_frames + 1)]
    width = pitch.max_n_candidates
    frequencies = np.full((len(frames), width), np.nan, dtype=np.float64)
    strengths = np.full_like(frequencies, np.nan)
    counts = np.empty(len(frames), dtype=np.int32)
    intensity = np.empty(len(frames), dtype=np.float64)
    for i, frame in enumerate(frames):
        candidates = frame.candidates
        counts[i] = len(candidates)
        intensity[i] = frame.intensity
        for j, candidate in enumerate(candidates):
            frequencies[i, j] = candidate.frequency
            strengths[i, j] = candidate.strength
    selected = np.zeros(len(frames), dtype=np.int32)
    values = frequencies[:, 0]
    valid = np.isfinite(values) & (values > 0) & (values <= pitch.ceiling)
    hz = np.where(valid, values, np.nan)
    return {
        "frame_index": np.arange(1, len(frames) + 1, dtype=np.int32),
        "frame_intensity": intensity,
        "candidate_count": counts,
        "candidate_frequency": frequencies,
        "candidate_strength": strengths,
        "original_selected": selected.copy(),
        "selected": selected,
        "original_hz": hz.copy(),
        "original_valid": valid.copy(),
        "hz": hz,
        "valid": valid,
        "ceiling": np.asarray(pitch.ceiling),
    }
