"""Praat voice quality; fixed algorithms, original samples and saved clocks."""

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import parselmouth
import soundfile as sf
from media_features import _extract_audio, _run, _sha256
from parselmouth.praat import call


def finite(value):
    return float(value) if math.isfinite(value) else None


def calculate(request):
    source, output = Path(request["source"]), Path(request["output"])
    if _sha256(source) != request["source_sha256"]:
        raise ValueError("原声音文件已变化，请刷新后重新选择。")
    probe = json.loads(
        _run(
            [
                request["ffprobe"],
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(source),
            ]
        )
    )
    stream = next((x for x in probe["streams"] if x.get("codec_type") == "audio"), None)
    if not stream:
        raise ValueError("材料中没有可读取的音轨，请选择对应录音。")
    rate = int(stream["sample_rate"])
    origin = request.get("audio_origin")
    if origin is None:
        frames = json.loads(
            _run(
                [
                    request["ffprobe"],
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-read_intervals",
                    "%+#1",
                    "-show_frames",
                    "-show_entries",
                    "frame=best_effort_timestamp_time,pts_time",
                    "-of",
                    "json",
                    str(source),
                ]
            )
        )["frames"]
        first = frames[0] if frames else {}
        origin = float(
            first.get(
                "best_effort_timestamp_time", first.get("pts_time", stream.get("start_time", 0))
            )
        )
    duration = float(stream.get("duration") or probe["format"]["duration"])
    left, right = request["original_start"], request["original_end"]
    if left < -1 / rate or right > duration + 1 / rate or right <= left:
        raise ValueError("选段超出原声音范围，请重新选段。")
    left, right = max(0, left), min(duration, right)
    target = output / "measurement.wav"
    # Native WAV uses exactly the same rectangular extraction as Praat's reference.
    # Compressed/container audio reuses the existing PTS-aware FFmpeg reader.
    if "wav" in probe.get("format", {}).get("format_name", "").split(","):
        original = parselmouth.Sound(str(source))
        if original.n_channels > 1:
            original = call(original, "Convert to mono")
        sound = call(original, "Extract part", left, right, "Rectangular", 1, True)
        extraction = "Praat rectangular extraction; original sample times"
        channel_method = "mono" if int(stream.get("channels", 1)) == 1 else "Praat channel mean"
    else:
        pts, count = _extract_audio(
            source,
            target,
            origin + left,
            origin + right,
            request["ffmpeg"],
            rate,
            1,
            codec="pcm_f32le",
        )
        signal, actual_rate = sf.read(target, dtype="float64")
        if actual_rate != rate or len(signal) != count:
            raise ValueError("音轨读取的采样率或长度不一致，请重新打开原录音。")
        sound = parselmouth.Sound(signal, sampling_frequency=rate, start_time=pts - origin)
        extraction = "FFmpeg original-rate rectangular extraction; container PTS"
        channel_method = "mono" if int(stream.get("channels", 1)) == 1 else "FFmpeg mono mix"
    if sound.n_samples < 2:
        raise ValueError("选段不足两个采样点，请扩大时间范围。")
    sf.write(target, sound.values[0], rate, subtype="DOUBLE")
    start, end = sound.xmin, sound.xmax
    p = request
    pitch = sound.to_pitch_cc(
        time_step=p["time_step"],
        pitch_floor=p["pitch_floor"],
        max_number_of_candidates=15,
        very_accurate=False,
        silence_threshold=0.03,
        voicing_threshold=0.45,
        octave_cost=0.01,
        octave_jump_cost=0.35,
        voiced_unvoiced_cost=0.14,
        pitch_ceiling=p["pitch_ceiling"],
    )
    pulses = call([sound, pitch], "To PointProcess (cc)")
    count = int(call(pulses, "Get number of points"))
    times = np.array([call(pulses, "Get time from index", i) for i in range(1, count + 1)])
    period_args = (start, end, p["period_floor"], p["period_ceiling"], p["maximum_period_factor"])
    summary = {
        "source_file": request["source_name"],
        "source_sha256": request["source_sha256"],
        "original_start_seconds": start,
        "original_end_seconds": end,
        "duration_seconds": end - start,
        "sample_rate_hz": rate,
        "pulse_count": count,
        "valid_period_count": int(call(pulses, "Get number of periods", *period_args)),
        "pitch_total_frames": pitch.n_frames,
        "pitch_voiced_frames": int((pitch.selected_array["frequency"] > 0).sum()),
    }
    for name, suffix in (
        ("local", "local_ratio"),
        ("local, absolute", "local_absolute_seconds"),
        ("rap", "rap_ratio"),
        ("ppq5", "ppq5_ratio"),
        ("ddp", "ddp_ratio"),
    ):
        summary["jitter_" + suffix] = finite(call(pulses, f"Get jitter ({name})", *period_args))
    for name, suffix in (
        ("local", "local_ratio"),
        ("local_dB", "local_db"),
        ("apq3", "apq3_ratio"),
        ("apq5", "apq5_ratio"),
        ("apq11", "apq11_ratio"),
        ("dda", "dda_ratio"),
    ):
        summary["shimmer_" + suffix] = finite(
            call(
                [sound, pulses],
                f"Get shimmer ({name})",
                *period_args,
                p["maximum_amplitude_factor"],
            )
        )
    hnr = sound.to_harmonicity_cc(
        time_step=p["time_step"],
        minimum_pitch=p["pitch_floor"],
        silence_threshold=0.1,
        periods_per_window=1,
    )
    hnr_times, hnr_raw = np.asarray(hnr.xs()), np.asarray(hnr.values[0])
    hnr_valid = np.isfinite(hnr_raw) & (hnr_raw != -200)
    hnr_values = np.where(hnr_valid, hnr_raw, np.nan)
    summary.update(
        mean_hnr_db=finite(call(hnr, "Get mean", start, end)),
        hnr_valid_frames=int(hnr_valid.sum()),
        hnr_total_frames=len(hnr_times),
    )
    cepstrum = call(sound, "To PowerCepstrogram", p["pitch_floor"], 0.002, 5000, 50)
    summary["cpps_db"] = finite(
        call(
            cepstrum,
            "Get CPPS",
            True,
            0.01,
            0.001,
            p["pitch_floor"],
            p["pitch_ceiling"],
            0.05,
            "Parabolic",
            0.001,
            0.05,
            "Straight",
            "Robust",
        )
    )
    np.savez_compressed(
        output / "voice-quality.npz",
        pulse_times_original=times,
        pulse_times_display=times - p["offset"],
        hnr_times_original=hnr_times,
        hnr_times_display=hnr_times - p["offset"],
        hnr_db=hnr_values,
        hnr_raw_db=hnr_raw,
        hnr_valid=hnr_valid,
        pitch_times_original=np.asarray(pitch.xs()),
        pitch_hz=pitch.selected_array["frequency"],
        pitch_voiced=pitch.selected_array["frequency"] > 0,
    )
    with (output / "pulses.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["pulse_index", "original_seconds", "display_seconds"])
        writer.writerows((i + 1, t, t - p["offset"]) for i, t in enumerate(times))
    with (output / "hnr.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["original_seconds", "display_seconds", "hnr_db", "valid", "praat_raw_db"])
        writer.writerows(
            (t, t - p["offset"], finite(value), bool(valid), raw)
            for t, value, valid, raw in zip(hnr_times, hnr_values, hnr_valid, hnr_raw)
        )
    with (output / "summary.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)
    method = {
        "extraction": extraction,
        "original_start": start,
        "original_end": end,
        "requested_original_start": p["original_start"],
        "requested_original_end": p["original_end"],
        "sample_first_time": sound.x1,
        "sample_rate_hz": rate,
        "sample_count": sound.n_samples,
        "input_channels": int(stream.get("channels", 1)),
        "channel_method": channel_method,
        "audio_origin": origin,
        "display_offset": p["offset"],
        "window": "Rectangular",
        "resampled": False,
        "pitch": {
            "method": "raw cross-correlation",
            "time_step": p["time_step"],
            "floor_hz": p["pitch_floor"],
            "ceiling_hz": p["pitch_ceiling"],
            "max_candidates": 15,
            "very_accurate": False,
            "silence_threshold": 0.03,
            "voicing_threshold": 0.45,
            "octave_cost": 0.01,
            "octave_jump_cost": 0.35,
            "voiced_unvoiced_cost": 0.14,
        },
        "pulses": "Sound & Pitch: To PointProcess (cc)",
        "period": {
            "floor_seconds": p["period_floor"],
            "ceiling_seconds": p["period_ceiling"],
            "maximum_period_factor": p["maximum_period_factor"],
            "maximum_amplitude_factor": p["maximum_amplitude_factor"],
        },
        "hnr": {
            "method": "Harmonicity cross-correlation",
            "time_step": p["time_step"],
            "minimum_pitch": p["pitch_floor"],
            "silence_threshold": 0.1,
            "periods_per_window": 1,
            "mean": "Praat Get mean; finite frames excluding -200 sentinel",
        },
        "cpps": {
            "cepstrogram_floor_hz": p["pitch_floor"],
            "time_step": 0.002,
            "maximum_frequency_hz": 5000,
            "preemphasis_hz": 50,
            "subtract_trend": True,
            "time_average_seconds": 0.01,
            "quefrency_average_seconds": 0.001,
            "peak_floor_hz": p["pitch_floor"],
            "peak_ceiling_hz": p["pitch_ceiling"],
            "tolerance": 0.05,
            "interpolation": "Parabolic",
            "fit_range_seconds": [0.001, 0.05],
            "trend": "Straight",
            "fit": "Robust",
        },
        "source_sha256": request["source_sha256"],
        "measurement_wav_sha256": _sha256(target),
        "parselmouth": parselmouth.__version__,
        "praat": parselmouth.PRAAT_VERSION,
    }
    (output / "method.json").write_text(
        json.dumps(method, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        calculate(request)
    except Exception as error:
        message = str(error)
        friendly = (
            message
            if message and "\u4e00" <= message[0] <= "\u9fff"
            else "声音质量计算失败，请扩大选段或调整基频范围后重试。"
        )
        Path(request["output"], "failure.json").write_text(
            json.dumps({"message": friendly, "detail": message}, ensure_ascii=False),
            encoding="utf-8",
        )
        raise
