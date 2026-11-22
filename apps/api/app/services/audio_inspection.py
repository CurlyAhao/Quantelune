"""Read saved acoustic frames and a bounded spectrogram of the same playback WAV."""

from __future__ import annotations

import asyncio
import math
import uuid
import wave
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.ml.bounded_writer import filesystem_path
from app.models.dataset import Dataset, UploadStatus
from app.services import dataset_service, storage_service
from app.services.multimodal_cases import get_run, invalid, read_json, read_metadata

TRACKS = [
    ("rms", "audio", "amplitude"),
    ("pitch", "pitch", "Hz"),
    ("intensity", "intensity", "dB"),
    *[(f"{kind}{n}", "formant", "Hz") for kind in ("f", "b") for n in range(1, 5)],
]


async def _saved_sample(db, user, dataset, sample_id):
    meta = read_metadata(dataset).get("media_result") or {}
    if not meta.get("collection_id") or not meta.get("run_id"):
        raise invalid("这份数据没有对应的声音处理结果。")
    source = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(meta["collection_id"]), user.id
    )
    run = await get_run(db, user, source, uuid.UUID(meta["run_id"]))
    if run["status"] != "completed":
        raise invalid("这次处理尚未完成，请打开已完成的结果。")
    index = await dataset_service.get_dataset_with_access(db, uuid.UUID(run["index_id"]), user.id)
    entries = read_json(storage_service.get_local_object_path(index.object_key)).get("entries", [])
    matches = [
        entry for entry in entries if (entry.get("sample") or {}).get("sample_id") == sample_id
    ]
    if len(matches) != 1 or not matches[0].get("array_prefix"):
        raise invalid("这次结果没有对应样本，请重新选择当前样本。")
    return source, run, matches[0]


def _measurements(path, prefix, point, start, end):
    tracks = {}
    with np.load(path, allow_pickle=False) as bundle:
        for name, clock, unit in TRACKS:
            key = (
                f"{prefix}_rms"
                if name == "rms"
                else f"{prefix}_{name}_{'db' if name == 'intensity' else 'hz'}"
            )
            time_key = f"{prefix}_{clock}_times_player"
            if key not in bundle or time_key not in bundle:
                continue
            values, times = bundle[key], bundle[time_key]
            if name == "rms" and values.ndim == 2:
                values = values.mean(axis=0)  # Same channel mean as the saved RMS curve.
            if values.ndim != 1 or times.ndim != 1 or len(values) != len(times) or not len(times):
                continue
            finite_time = np.isfinite(times)
            gaps = np.diff(times[finite_time])
            positive = gaps[gaps > 0]
            step = float(np.median(positive)) if positive.size else None
            valid = finite_time & np.isfinite(values)
            valid_key = f"{prefix}_{name}_valid"
            if valid_key in bundle and bundle[valid_key].shape == valid.shape:
                valid &= bundle[valid_key].astype(bool)
            if name == "pitch" or name.startswith(("f", "b")):
                valid &= values > 0
            track = {"unit": unit, "step_seconds": step}
            if point is not None:
                # Choose the nearest FRAME, not the nearest valid value. Never bridge a missing
                # frame.
                positions = np.flatnonzero(finite_time)
                near = (
                    int(positions[np.abs(times[positions] - point).argmin()])
                    if positions.size
                    else None
                )
                distance = abs(float(times[near]) - point) if near is not None else math.inf
                allowed = step / 2 + 1e-8 if step is not None else 1e-8
                track["point"] = {
                    "frame_time": float(times[near])
                    if near is not None and distance <= allowed
                    else None,
                    "value": float(values[near])
                    if near is not None and distance <= allowed and valid[near]
                    else None,
                }
            if start is not None and end is not None:
                inside = finite_time & (times >= start) & (times < end)
                selected = values[inside & valid].astype(np.float64)
                average = None
                if selected.size:
                    if name == "intensity":
                        peak = float(selected.max())
                        average = peak + 10 * math.log10(
                            float(np.mean(10 ** ((selected - peak) / 10)))
                        )
                    else:
                        average = float(selected.mean())
                track["selection"] = {
                    "valid_count": int(selected.size),
                    "total_count": int(inside.sum()),
                    "mean": average,
                    "min": float(selected.min()) if selected.size else None,
                    "max": float(selected.max()) if selected.size else None,
                }
            tracks[name] = track
    return {"point_time": point, "start": start, "end": end, "tracks": tracks}


async def measurements(db, user, dataset, sample_id, point=None, start=None, end=None):
    _, run, entry = await _saved_sample(db, user, dataset, sample_id)
    package = await dataset_service.get_dataset_with_access(
        db, uuid.UUID(run["package_id"]), user.id
    )
    path = storage_service.get_local_object_path(package.object_key)
    return await asyncio.to_thread(_measurements, path, entry["array_prefix"], point, start, end)


def _spectrogram(path, origin, start, end):
    # Existing extracted playback WAV is signed 16-bit PCM; original features are not recomputed.
    with wave.open(str(filesystem_path(Path(path))), "rb") as audio:
        sr, channels, count = audio.getframerate(), audio.getnchannels(), audio.getnframes()
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise invalid("这份派生音轨不是 PCM 16位 WAV，请从材料组重新提取音轨。")
        total_end = origin + count / sr
        left, right = (
            max(origin, start if start is not None else origin),
            min(total_end, end if end is not None else total_end),
        )
        if right <= left:
            raise invalid("显示范围超出音轨，请恢复全段视图。")
        win = max(8, round(sr * 0.025))
        hop = max(1, round(sr * 0.010))
        if count < win:
            raise invalid("声音短于25毫秒，无法显示此窗长的声谱图。")
        final_frame = (count - win) // hop
        first = min(final_frame, max(0, math.floor(((left - origin) * sr - win / 2) / hop)))
        last = max(first, min(final_frame, math.ceil(((right - origin) * sr - win / 2) / hop)))
        positions = np.unique(np.linspace(first, last, min(700, last - first + 1), dtype=int)) * hop
        taper = np.hanning(win)
        frequencies = np.fft.rfftfreq(win, 1 / sr)
        eligible = np.flatnonzero(frequencies <= min(8000, sr / 2))
        bins = eligible[
            np.unique(np.linspace(0, len(eligible) - 1, min(192, len(eligible)), dtype=int))
        ]
        powers = []
        for position in positions:
            audio.setpos(int(position))
            signal = (
                np.frombuffer(audio.readframes(win), dtype="<i2").reshape(-1, channels).mean(axis=1)
                / 32768
            )
            spectrum = np.fft.rfft(signal * taper)
            power = np.abs(spectrum / taper.sum()) ** 2
            powers.append(10 * np.log10(np.maximum(power[bins], 1e-16)))
    matrix = np.asarray(powers)
    peak = float(matrix.max())
    return {
        "times": (origin + (positions + win / 2) / sr).tolist(),
        "frequencies": frequencies[bins].tolist(),
        "db": np.round(matrix, 2).tolist(),
        "color_min_db": max(-160, peak - 70),
        "color_max_db": peak,
        "start": left,
        "end": right,
        "origin": origin,
        "sample_rate": sr,
        "channels": channels,
        "window_ms": 1000 * win / sr,
        "hop_ms": 1000 * hop / sr,
        "method": "Hann · 归一化PCM功率 dB",
        "display_stride": int(max(1, round((positions[1] - positions[0]) / hop)))
        if len(positions) > 1
        else 1,
    }


async def spectrogram(db, user, dataset, sample_id, start=None, end=None):
    source, run, entry = await _saved_sample(db, user, dataset, sample_id)
    filename = Path(entry.get("audio_file") or "").name
    ids = [uuid.UUID(value) for value in run["result_ids"]]
    items = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == source.project_id,
                Dataset.id.in_(ids),
                Dataset.upload_status != UploadStatus.DELETED,
                Dataset.original_filename == filename,
            )
        )
    ).all()
    items = [
        item for item in items if read_metadata(item).get("media_result", {}).get("kind") == "media"
    ]
    if len(items) != 1:
        raise invalid("找不到这次结果的派生音轨，请在材料组打开原文件并重新提取音轨。")
    segment = entry.get("segment") or {}
    offset = (entry.get("audio_alignment") or {}).get("effective_offset_seconds") or 0
    if (
        segment.get("first_audio_pts_seconds") is not None
        and entry.get("audio_origin_seconds") is not None
    ):
        origin = segment["first_audio_pts_seconds"] - entry["audio_origin_seconds"] - offset
    else:
        origin = segment.get("audio_player_start", segment.get("player_start", 0)) - offset
    path = storage_service.get_local_object_path(items[0].object_key)
    return await asyncio.to_thread(_spectrogram, path, origin, start, end)
