"""Generate native Burg candidates or choose a path through saved candidates.

Read the native object produced by this engine, not a simulated set of separately
resampled Burg calls. Every candidate retains its own frame count and bandwidths.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

PATH_FINDER = {
    "frequency_bandwidth_weight": 1,
    "frequency_change_weight": 1,
    "stress_weight": 1,
    "ceiling_change_weight": 1,
    "intensity_modulation_db": 5,
    "stress_window_seconds": 0.035,
    "coefficients_by_track": [3, 3, 3, 3],
    "power": 1.25,
}


def native_arrays(text):
    if 'Object class = "FormantPath"' not in text:
        raise ValueError("当前 Praat 的 FormantPath 格式与已接入的引擎不同。")
    parts = re.split(r"(?m)^formants \[\d+\]:\s*$", text)
    header = parts[0]

    def scalar(block, name):
        return float(re.search(r"(?m)^\s*" + name + r" = ([^\s]+)", block)[1])

    n = int(scalar(header, "nx"))
    count = int(re.search(r"formants: size = (\d+)", header)[1])
    ceilings = np.asarray([float(v) for v in re.findall(r"ceilings \[\d+\] = ([^\s]+)", text)])
    selected = np.asarray(
        [int(v) - 1 for v in re.findall(r"path \[\d+\] = (\d+)", text)], dtype=np.int32
    )
    if len(parts) != count + 1 or ceilings.shape != (count,) or selected.shape != (n,):
        raise ValueError("原生共振峰候选或路径不完整。")
    frequency = np.full((count, n, 5), np.nan)
    bandwidth = np.full_like(frequency, np.nan)
    counts = np.zeros((count, n), dtype=np.int32)
    intensities = np.zeros((count, n), dtype=np.float64)
    clock = {key: scalar(header, key) for key in ("xmin", "xmax", "dx", "x1")}
    for candidate, block in enumerate(parts[1:]):
        frames = re.split(r"(?m)^\s+frames \[\d+\]:\s*$", block)
        if (
            len(frames) != n + 1
            or scalar(frames[0], "nx") != n
            or any(scalar(frames[0], k) != v for k, v in clock.items())
        ):
            raise ValueError("候选没有使用同一组帧中心，未保存。")
        for frame, values in enumerate(frames[1:]):
            pairs = re.findall(r"frequency = ([^\s]+)\s+bandwidth = ([^\s]+)", values)
            size = int(scalar(values, "numberOfFormants"))
            if len(pairs) != size or size > 5:
                raise ValueError("候选中的频率与带宽不对应。")
            counts[candidate, frame] = size
            intensities[candidate, frame] = scalar(values, "intensity")
            for peak, (f, b) in enumerate(pairs):
                f, b = float(f), float(b)
                frequency[candidate, frame, peak] = f if np.isfinite(f) and f > 0 else np.nan
                bandwidth[candidate, frame, peak] = (
                    b if np.isfinite(f) and f > 0 and np.isfinite(b) and b > 0 else np.nan
                )
    if np.any(selected < 0) or np.any(selected >= count):
        raise ValueError("原生共振峰路径不在候选范围内。")
    return {
        "candidate_frequency": frequency,
        "candidate_bandwidth": bandwidth,
        "candidate_count": counts,
        "candidate_intensity": intensities,
        "ceilings_hz": ceilings,
        "selected": selected,
        "original_selected": selected.copy(),
        "frame_index": np.arange(1, n + 1, dtype=np.int32),
        "times": clock["x1"] + np.arange(n) * clock["dx"],
    }, clock


def _write_native(path, data, method):
    """Reconstruct Praat 6.1.38's text object using only the saved arrays."""
    frequencies, bandwidths = data["candidate_frequency"], data["candidate_bandwidth"]
    counts, intensities = data["candidate_count"], data["candidate_intensity"]
    ceilings, selected = data["ceilings_hz"], data["selected"]
    n = len(selected)
    clock = method["clock"]
    original = data["original_selected"]
    times = clock["x1"] + np.arange(n) * clock["dx"]
    if (
        not n
        or frequencies.shape != (len(ceilings), n, 5)
        or bandwidths.shape != frequencies.shape
        or counts.shape != frequencies.shape[:2]
        or intensities.shape != counts.shape
        or not np.issubdtype(counts.dtype, np.integer)
        or np.any((counts < 0) | (counts > 5))
        or not np.isfinite(intensities).all()
        or np.any(intensities < 0)
        or not np.isfinite(ceilings).all()
        or np.any(ceilings <= 0)
        or np.any(np.diff(ceilings) <= 0)
        or any(
            indices.shape != (n,)
            or not np.issubdtype(indices.dtype, np.integer)
            or np.any((indices < 0) | (indices >= len(ceilings)))
            for indices in (original, selected)
        )
        or not np.array_equal(data["frame_index"], np.arange(1, n + 1))
        or not np.array_equal(data["times"], times + method["first_pts"])
        or not np.array_equal(data["times_player"], times + method["player_origin"])
        or not np.isfinite(times).all()
        or clock["dx"] <= 0
        or not np.array_equal(
            data["original_frequency"], frequencies[original, np.arange(n)], equal_nan=True
        )
        or not np.array_equal(
            data["original_bandwidth"], bandwidths[original, np.arange(n)], equal_nan=True
        )
    ):
        raise ValueError("保存的共振峰候选、原始轨迹或帧时钟不完整，无法自动选路。")
    present = np.arange(5) < counts[..., None]
    for values in (frequencies, bandwidths):
        if (
            not np.isfinite(values[present]).all()
            or np.any(values[present] <= 0)
            or not np.isnan(values[~present]).all()
        ):
            raise ValueError("保存的候选包含无法恢复的频率或带宽，无法自动选路。")
    header = {**clock, "nx": n}
    clock_fields = ("xmin", "xmax", "nx", "dx", "x1")
    with path.open("w", encoding="ascii") as stream:

        def line(value):
            stream.write(value + "\n")

        line('File type = "ooTextFile"\nObject class = "FormantPath"\n')
        for name in clock_fields:
            line(f"{name} = {header[name]}")
        line(f"formants: size = {len(ceilings)}")
        for c in range(len(ceilings)):
            line(f"formants [{c + 1}]:")
            for name in clock_fields:
                line(f"    {name} = {header[name]}")
            line("    maxnFormants = 5\n    frames []:")
            for i, size in enumerate(counts[c]):
                line(
                    f"        frames [{i + 1}]:\n            intensity = {float(intensities[c, i])}"
                )
                line(f"            numberOfFormants = {size}\n            formant []:")
                for j in range(size):
                    line(
                        f"                formant [{j + 1}]:\n \
                   frequency = {float(frequencies[c, i, j])}"
                    )
                    line(f"                    bandwidth = {float(bandwidths[c, i, j])}")
        line("ceilings []:")
        for i, ceiling in enumerate(ceilings, 1):
            line(f"    ceilings [{i}] = {float(ceiling)}")
        line("path []:")
        for i, candidate in enumerate(selected, 1):
            line(f"    path [{i}] = {int(candidate) + 1}")


def automatic(request):
    import parselmouth as pm
    from parselmouth.praat import call

    method = request["method"]
    if (
        pm.PRAAT_VERSION != "6.1.38"
        or method["praat_version"] != pm.PRAAT_VERSION
        or method["max_formants"] != 5
    ):
        raise ValueError("保存的候选与当前 Praat 的自动选路格式不一致。")
    with np.load(request["input_npz"], allow_pickle=False) as bundle:
        data = {name: bundle[name] for name in bundle.files}
    if len(data["ceilings_hz"]) < 2:
        raise ValueError("自动选路需要至少两组共振峰候选。")
    source = Path(request["input_npz"]).with_suffix(".FormantPath")
    _write_native(source, data, method)
    obj = pm.read(str(source))
    p = PATH_FINDER
    call(
        obj,
        "Path finder",
        p["frequency_bandwidth_weight"],
        p["frequency_change_weight"],
        p["stress_weight"],
        p["ceiling_change_weight"],
        p["intensity_modulation_db"],
        p["stress_window_seconds"],
        " ".join(map(str, p["coefficients_by_track"])),
        p["power"],
    )
    native = Path(request["output_npz"]).with_suffix(".FormantPath")
    obj.save_as_text_file(str(native))
    result, clock = native_arrays(native.read_text(encoding="utf-8-sig"))
    unchanged = (
        "candidate_frequency",
        "candidate_bandwidth",
        "candidate_count",
        "candidate_intensity",
        "ceilings_hz",
        "frame_index",
    )
    if (
        clock != method["clock"]
        or any(not np.array_equal(result[key], data[key], equal_nan=True) for key in unchanged)
        or not np.array_equal(result["times"] + method["first_pts"], data["times"])
        or not np.array_equal(result["times"] + method["player_origin"], data["times_player"])
    ):
        raise ValueError("Praat 返回的候选或帧时钟与保存结果不一致，未保存自动选路。")
    np.savez_compressed(request["output_npz"], selected=result["selected"])
    meta = {
        "algorithm": "Path finder",
        "engine": "Parselmouth",
        "version": pm.__version__,
        "praat_version": pm.PRAAT_VERSION,
        "parameters": p,
    }
    Path(request["output_json"]).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def generate(request):
    import parselmouth as pm
    import soundfile as sf
    from parselmouth.praat import call

    path = Path(request["audio_path"])
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != request["audio_sha256"]:
            raise ValueError("这次测量的声音文件已变化，请重新打开原材料处理。")
    signal, rate = sf.read(path, dtype="float64")
    if signal.ndim != 1 or len(signal) != request["sample_count"] or rate != request["sample_rate"]:
        raise ValueError("测量音轨的采样率或长度与保存记录不一致。")
    p = request["parameters"]
    if p["middle_ceiling_hz"] * np.exp(p["ceiling_step"] * p["steps_each_side"]) > rate / 2:
        raise ValueError("最高候选上限超过此音轨的奈奎斯特频率，请降低中间上限或步数。")
    sound = pm.Sound(signal, sampling_frequency=rate)
    obj = call(
        sound,
        "To FormantPath (burg)",
        p["time_step_s"],
        5,
        p["middle_ceiling_hz"],
        p["window_s"],
        p["preemphasis_hz"],
        p["ceiling_step"],
        p["steps_each_side"],
    )
    native = Path(request["output_npz"]).with_suffix(".FormantPath")
    obj.save_as_text_file(str(native))
    data, clock = native_arrays(native.read_text(encoding="utf-8-sig"))
    times = data["times"].copy()
    data["times"] += request["first_pts"]
    data["times_player"] = times + request["player_origin"]
    selected = data["selected"]
    data["original_frequency"] = data["candidate_frequency"][selected, np.arange(len(selected))]
    data["original_bandwidth"] = data["candidate_bandwidth"][selected, np.arange(len(selected))]
    np.savez_compressed(request["output_npz"], **data)
    result = {
        "engine": "Parselmouth",
        "version": pm.__version__,
        "praat_version": pm.PRAAT_VERSION,
        "algorithm": "To FormantPath (burg)",
        "max_formants": 5,
        "parameters": p,
        "clock": clock,
        "sample_rate": rate,
        "sample_count": len(signal),
        "first_pts": request["first_pts"],
        "player_origin": request["player_origin"],
        "audio_sha256": request["audio_sha256"],
        "total_frames": len(selected),
        "ceilings_hz": data["ceilings_hz"].tolist(),
    }
    Path(request["output_json"]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    import sys

    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        if request.get("action") == "auto":
            automatic(request)
        else:
            generate(request)
    except Exception as error:
        Path(request["output_json"]).write_text(
            json.dumps({"error": str(error).splitlines()[0]}, ensure_ascii=False), encoding="utf-8"
        )
        raise
