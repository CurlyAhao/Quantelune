"""Fixed Praat audio preparation, shared by media runs and intensity tiers."""

import json
import os
import sys
from pathlib import Path

import numpy as np
import parselmouth
import soundfile as sf
from parselmouth.praat import call


def selected_sound(source, target, start, end, origin, rate, channels, ffmpeg):
    try:
        from .media_features import _extract_audio
    except ImportError:
        from media_features import _extract_audio
    try:
        sf.info(source)
        independent_audio = True
    except (RuntimeError, OSError):
        independent_audio = False
    if independent_audio:
        try:
            from .audio_pcm import canonical_audio, read_selection
        except ImportError:
            from audio_pcm import canonical_audio, read_selection
        meta = canonical_audio(source, ffmpeg, Path(os.environ["TEMP"]) / "audio-native")
        values, actual, first, last = read_selection(meta["path"], start, end, channels)
        if actual != rate or len(values) != last - first:
            raise ValueError("声音读取长度发生变化，请重新打开原材料。")
        sound = parselmouth.Sound(values.T, sampling_frequency=rate, start_time=first / rate)
        extraction = f"Native-sample selection [{first},{last}); {meta['clock']}"
    else:
        pts, count = _extract_audio(
            source, target, origin + start, origin + end, ffmpeg, rate, channels, codec="pcm_f32le"
        )
        values, actual = sf.read(target, dtype="float64", always_2d=True)
        if actual != rate or len(values) != count:
            raise ValueError("声音读取长度发生变化，请重新打开原材料。")
        sound = parselmouth.Sound(values.T, sampling_frequency=rate, start_time=pts - origin)
        extraction = "FFmpeg native-rate PCM selection; original container timestamps"
    if sound.n_samples < 2:
        raise ValueError("选段不足两个采样点，请扩大选段。")
    return sound, extraction


def filter_audio(source, target, start, end, origin, source_rate, rate, channels, ffmpeg, options):
    try:
        from .media_features import _sha256
    except ImportError:
        from media_features import _sha256
    kind = options["audio_filter"]
    low = 0.0 if kind == "lowpass" else float(options.get("filter_low_hz", 300))
    high = source_rate / 2 if kind == "highpass" else float(options.get("filter_high_hz", 3400))
    width = float(options.get("filter_transition_hz", 100))
    if not 0 <= low < high <= source_rate / 2 or not 0 < width < source_rate / 2:
        raise ValueError(
            f"滤波频率须在 0–{source_rate / 2:g} Hz 内，下限低于上限，过渡宽度大于 0 且小于该上限。"
        )
    sound, extraction = selected_sound(
        source, target, start, end, origin, source_rate, channels, ffmpeg
    )
    filtered = call(sound, "Filter (pass Hann band)", low, high, width)
    native_rms = float(np.sqrt(np.mean(filtered.values**2)))
    if rate != source_rate:
        filtered = call(filtered, "Resample", rate, 50)
    # The saved waveform is the input for every selected feature and the player.
    filtered.save(str(target), "WAV")
    saved, actual_rate = sf.read(target, dtype="float64", always_2d=True)
    sample_origin = filtered.x1 - 0.5 / rate
    method = {
        "kind": kind,
        "method": "Praat Filter (pass Hann band)",
        "low_hz": low,
        "high_hz": high,
        "transition_hz": width,
        "order": [
            "channel_mix",
            "select_native_samples",
            "Hann_filter",
            "resample",
            "save_PCM16",
            "selected_features",
        ],
        "extraction": extraction,
        "source_sample_rate": source_rate,
        "sample_rate": actual_rate,
        "channels": channels,
        "resample_precision": 50 if rate != source_rate else None,
        "original_start": sample_origin,
        "original_end": sample_origin + len(saved) / rate,
        "praat_domain_start": filtered.xmin,
        "praat_domain_end": filtered.xmax,
        "first_sample_center": filtered.x1,
        "clock_mapping": (
            "saved WAV frame zero = Praat first sample center minus half output sample period"
        ),
        "requested_start": start,
        "requested_end": end,
        "sample_count": len(saved),
        "native_filtered_float_rms": native_rms,
        "saved_audio_rms": float(np.sqrt(np.mean(saved**2))),
        "source_sha256": _sha256(source),
        "audio_sha256": _sha256(target),
        "parselmouth": parselmouth.__version__,
        "praat": parselmouth.PRAAT_VERSION,
    }
    return origin + sample_origin, len(saved), method


def intensity_regions(request):
    from media_features import _run, _sha256

    source, output = Path(request["source"]), Path(request["output"])
    if _sha256(source) != request["source_sha256"]:
        raise ValueError("所选声音已变化，请重新打开材料。")
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
    if stream is None:
        raise ValueError("材料没有可读取的声音。")
    rate = int(stream["sample_rate"])
    duration = float(stream.get("duration") or probe["format"]["duration"])
    if source.suffix.lower() == ".wav":
        info = sf.info(source)
        duration = info.frames / info.samplerate
    start = request.get("input_start")
    end = request.get("input_end")
    start = 0 if start is None else start
    end = duration if end is None else end
    if start < -1 / rate or end > duration + 1 / rate or end <= start:
        raise ValueError("分段范围超出当前音轨，请重新选择时间。")
    origin = request.get("input_container_origin") or float(
        stream.get("start_time") or probe["format"].get("start_time") or 0
    )
    sound, extraction = selected_sound(
        source,
        output / "measurement.wav",
        max(0, start),
        min(duration, end),
        origin,
        rate,
        1,
        request["ffmpeg"],
    )
    intensity = sound.to_intensity(
        minimum_pitch=request["minimum_pitch"],
        time_step=request["time_step"],
        subtract_mean=request["subtract_mean"],
    )
    grid = call(
        intensity,
        "To TextGrid (silences)",
        request["threshold_db"],
        request["minimum_silence"],
        request["minimum_sounding"],
        "静音",
        "有声",
    )
    intervals = [
        {
            "start": call(grid, "Get start time of interval", 1, i),
            "end": call(grid, "Get end time of interval", 1, i),
            "text": call(grid, "Get label of interval", 1, i),
        }
        for i in range(1, int(call(grid, "Get number of intervals", 1)) + 1)
    ]
    method = {
        "method": "Intensity: To TextGrid (silences)",
        "extraction": extraction,
        "parselmouth": parselmouth.__version__,
        "praat": parselmouth.PRAAT_VERSION,
        "input_start": sound.xmin,
        "input_end": sound.xmax,
        "input_sample_rate": rate,
        "input_sample_count": sound.n_samples,
        "source_sha256": request["source_sha256"],
        "parameters": {
            key: request[key]
            for key in (
                "minimum_pitch",
                "time_step",
                "subtract_mean",
                "threshold_db",
                "minimum_silence",
                "minimum_sounding",
            )
        },
    }
    np.savez_compressed(
        output / "intensity.npz", times_input=intensity.xs(), intensity_db=intensity.values[0]
    )
    result = {"intervals": intervals, "method": method}
    (output / "regions.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        intensity_regions(request)
    except Exception as error:
        message = str(error)
        friendly = (
            message
            if message and "\u4e00" <= message[0] <= "\u9fff"
            else "强度分段失败，请扩大选段或调整最低周期频率。"
        )
        Path(request["output"], "failure.json").write_text(
            json.dumps({"message": friendly, "detail": message}, ensure_ascii=False),
            encoding="utf-8",
        )
        raise
