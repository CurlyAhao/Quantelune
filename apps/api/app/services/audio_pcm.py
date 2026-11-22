"""Bounded native-sample access to independent audio files."""

import hashlib
import json
import math
import os
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_audio(source, ffmpeg, cache_root):
    """Return seekable PCM on the decoded audio clock, without changing the source."""
    source = Path(source)
    try:
        info = sf.info(source)
    except (RuntimeError, OSError):
        info = None
    if info is not None and info.format in {"WAV", "WAVEX", "RF64"}:
        path, clock = source, "native_pcm"
    else:
        cache = Path(cache_root)
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / (_hash(source) + "-ffmpeg-gapless-f32.wav")
        clock = "ffmpeg_gapless_decoded_samples"
        if not path.is_file():
            temporary = cache / (uuid.uuid4().hex + ".wav")
            try:
                subprocess.run(
                    [
                        str(ffmpeg),
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-map",
                        "0:a:0",
                        "-c:a",
                        "pcm_f32le",
                        str(temporary),
                    ],
                    check=True,
                    capture_output=True,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                sf.info(temporary)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    info = sf.info(path)
    return {
        "path": str(path),
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "frames": info.frames,
        "duration": info.frames / info.samplerate,
        "clock": clock,
        "source_start_index": 0,
    }


def _bounds(start, end, rate, frames):
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("无效的声音选段。")
    first = min(frames, max(0, math.ceil(start * rate - 1e-7)))
    last = min(frames, max(0, math.ceil(end * rate - 1e-7)))
    if last <= first:
        raise ValueError("选段没有声音采样点。")
    return first, last


def read_selection(path, start, end, channels=1):
    with sf.SoundFile(path) as file:
        rate = file.samplerate
        first, last = _bounds(start, end, rate, len(file))
        file.seek(first)
        values = file.read(last - first, dtype="float64", always_2d=True)
    if len(values) != last - first:
        raise ValueError("声音读取长度发生变化，请重新打开原材料。")
    if channels == 1 and values.shape[1] != 1:
        values = values.mean(axis=1, keepdims=True)
    elif channels == 2 and values.shape[1] == 1:
        values = np.repeat(values, 2, axis=1)
    elif channels is not None and values.shape[1] != channels:
        raise ValueError("无法转换所选声道数。")
    return values, rate, first, last


def preview(request):
    source = Path(request["source"])
    if request.get("source_sha256") and _hash(source) != request["source_sha256"]:
        raise ValueError("原声音文件已变化，请刷新后重新选择。")
    meta = canonical_audio(source, request["ffmpeg"], request["cache_root"])
    start = 0 if request.get("start") is None else float(request["start"])
    end = meta["duration"] if request.get("end") is None else float(request["end"])
    first, last = _bounds(start, end, meta["sample_rate"], meta["frames"])
    boundaries = np.linspace(first, last, min(900, last - first) + 1, dtype=np.int64)
    points, intervals, total = [], [], 0
    with sf.SoundFile(meta["path"]) as file:
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            # Contiguous bounded samples from each bucket, not a full-bucket peak.
            count = min(1024, int(right - left))
            file.seek(int(left))
            values = file.read(count, dtype="float64", always_2d=True)
            if len(values) != count:
                raise ValueError("声音读取长度发生变化，请重新打开原材料。")
            mono = values.mean(axis=1)
            index = int(np.argmax(np.abs(mono)))
            points.append(
                {"time": (int(left) + index) / file.samplerate, "value": float(mono[index])}
            )
            intervals.append([int(left), int(left) + count])
            total += count
    result = {
        **meta,
        "start": first / meta["sample_rate"],
        "end": last / meta["sample_rate"],
        "points": points,
        "read_frames": total,
        "read_intervals": intervals,
        "display_sampled": total < last - first,
    }
    output = Path(request["output"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "preview.json").write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if request.get("operation") != "preview":
        raise ValueError("Unsupported operation")
    preview(request)
