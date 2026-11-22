"""Fixed Praat spectral slice worker, using the existing media environment."""

import csv
import json
import os
import sys
from pathlib import Path

import numpy as np
import parselmouth
import soundfile as sf
from audio_pcm import canonical_audio, read_selection
from media_features import _extract_audio, _run, _sha256


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
    independent = not any(x.get("codec_type") == "video" for x in probe.get("streams", []))
    origin = 0.0 if independent else request.get("audio_origin")
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
    meta = (
        canonical_audio(source, request["ffmpeg"], Path(os.environ["TEMP"]) / "audio-native")
        if independent
        else None
    )
    duration = (
        meta["duration"] if meta else float(stream.get("duration") or probe["format"]["duration"])
    )
    left, right = max(0, request["original_start"]), min(duration, request["original_end"])
    if right <= left:
        raise ValueError("选段超出原声音范围，请重新选段。")
    target = output / "measurement.wav"
    mapping = {}
    if meta:
        values, actual_rate, first_index, last_index = read_selection(meta["path"], left, right, 1)
        signal = values[:, 0]
        count, pts = len(signal), first_index / actual_rate
        sf.write(target, signal, actual_rate, subtype="FLOAT")
        mapping = {
            "source_start_index": first_index,
            "source_end_index": last_index,
            "decoding_clock": meta["clock"],
            "saved_file_start": 0,
            "container_first_pts": None,
            "channel_method": "mono" if meta["channels"] == 1 else "arithmetic channel mean",
            "clock_mapping": "saved WAV frame k = (source_start_index + k) / sample_rate_hz",
        }
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
    if actual_rate != rate or len(signal) != count or count < 2:
        raise ValueError("选段不足两个实际采样点，请扩大时间范围。")
    sound = parselmouth.Sound(signal, sampling_frequency=rate, start_time=pts - origin)
    if request["mode"] == "spectrogram":
        return spectrogram(
            sound, request, output, pts, origin, count, rate, stream, target, mapping
        )
    windowed = parselmouth.praat.call(
        sound, "Extract part", sound.xmin, sound.xmax, "Hanning", 1, True
    )
    spectrum = windowed.to_spectrum(fast=True)
    frequencies = np.asarray(spectrum.xs())
    real, imaginary = spectrum.values
    power = real * real + imaginary * imaginary
    amplitude = np.sqrt(power)
    fft_size = 2 * (len(frequencies) - 1)
    sf.write(output / "windowed.wav", windowed.values[0], rate, subtype="DOUBLE")
    np.savez_compressed(
        output / "spectrum.npz",
        frequency_hz=frequencies,
        real=real,
        imaginary=imaginary,
        amplitude=amplitude,
        complex_magnitude_squared=power,
    )
    with (output / "spectrum.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "frequency_hz",
                "real_pcm_s",
                "imaginary_pcm_s",
                "amplitude_pcm_s",
                "complex_magnitude_squared_pcm2_s2",
            ]
        )
        writer.writerows(zip(frequencies, real, imaginary, amplitude, power))
    method = {
        "window": "Hann",
        "window_implementation": "Praat Extract part: Hanning, relative width 1",
        "mode": request["mode"],
        "requested_window_ms": request["window_ms"],
        "requested_original_start": request["original_start"],
        "requested_original_end": request["original_end"],
        "original_start": pts - origin,
        "original_end": pts - origin + count / rate,
        "container_first_pts": pts,
        "audio_origin": origin,
        "display_offset": request["offset"],
        "sample_count": count,
        "sample_rate_hz": rate,
        "input_channels": int(stream.get("channels", 1)),
        "channel_method": "mono" if int(stream.get("channels", 1)) == 1 else "FFmpeg mono mix",
        "actual_window_ms": count / rate * 1000,
        "fft_size": fft_size,
        "bin_spacing_hz": rate / fft_size,
        "fast": True,
        "normalization": "X = FFT(windowed PCM) / sample_rate_hz",
        "amplitude_unit": "PCM·s",
        "power_unit": "PCM²·s²",
        "power_definition": "real² + imaginary²",
        "source_sha256": request["source_sha256"],
        "measurement_wav_sha256": _sha256(target),
        "parselmouth": parselmouth.__version__,
        "praat": parselmouth.PRAAT_VERSION,
        **mapping,
    }
    (output / "method.json").write_text(
        json.dumps(method, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return method


def spectrogram(sound, request, output, pts, origin, count, rate, stream, target, mapping):
    result = sound.to_spectrogram(
        window_length=request["window_ms"] / 1000,
        maximum_frequency=min(request["max_frequency_hz"], rate / 2),
        time_step=request["time_step_ms"] / 1000,
        frequency_step=request["frequency_step_hz"],
        window_shape=parselmouth.SpectralAnalysisWindowShape.GAUSSIAN,
    )
    grid = {
        key: getattr(result, key)
        for key in ("xmin", "xmax", "nx", "dx", "x1", "ymin", "ymax", "ny", "dy", "y1")
    }
    np.savez_compressed(
        output / "spectrum.npz",
        time_seconds=result.xs(),
        frequency_hz=result.ys(),
        density_pcm2_per_hz=result.values,
    )
    method = {
        "mode": "spectrogram",
        "window": "Gaussian",
        "window_ms": request["window_ms"],
        "requested_time_step_ms": request["time_step_ms"],
        "requested_frequency_step_hz": request["frequency_step_hz"],
        "requested_max_frequency_hz": request["max_frequency_hz"],
        "grid": grid,
        "original_start": pts - origin,
        "original_end": pts - origin + count / rate,
        "container_first_pts": pts,
        "audio_origin": origin,
        "display_offset": request["offset"],
        "sample_count": count,
        "sample_rate_hz": rate,
        "input_channels": int(stream.get("channels", 1)),
        "density_unit": "PCM²/Hz",
        "interpolation": "bilinear on full analysis grid",
        "source_sha256": request["source_sha256"],
        "measurement_wav_sha256": _sha256(target),
        "parselmouth": parselmouth.__version__,
        "praat": parselmouth.PRAAT_VERSION,
        **mapping,
    }
    (output / "method.json").write_text(
        json.dumps(method, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return method


def query(request):
    with np.load(request["arrays"], allow_pickle=False) as data:
        frequencies = data["frequency_hz"]
        if "density_pcm2_per_hz" in data:
            t, f = request.get("time"), request.get("frequency")
            times, values = data["time_seconds"], data["density_pcm2_per_hz"]
            if (
                t is None
                or f is None
                or not times[0] <= t <= times[-1]
                or not frequencies[0] <= f <= frequencies[-1]
            ):
                raise ValueError("点读位置超出完整分析网格，请在声谱图的有效时间与频率内选择。")
            row = np.array([np.interp(t, times, values[i]) for i in range(len(frequencies))])
            result = {
                "time": t,
                "frequency": f,
                "density": float(np.interp(f, frequencies, row)),
                "unit": "PCM²/Hz",
                "interpolation": "bilinear on full analysis grid",
            }
        else:
            low, high = request.get("lower_hz"), request.get("upper_hz")
            if low is None or high is None or not 0 <= low < high <= frequencies[-1]:
                raise ValueError("频带应在0至奈奎斯特频率之间，且终点大于起点。")
            spectrum = parselmouth.Spectrum(
                data["real"] + 1j * data["imaginary"], float(frequencies[-1])
            )
            result = {
                "lower_hz": low,
                "upper_hz": high,
                "energy": spectrum.get_band_energy(low, high),
                "mean_density": spectrum.get_band_density(low, high),
                "energy_unit": "PCM²·s",
                "density_unit": "PCM²·s/Hz",
                "method": "Praat Spectrum Get band energy / Get band density",
                "window": "Hann",
                "parselmouth": parselmouth.__version__,
                "praat": parselmouth.PRAAT_VERSION,
            }
    (Path(request["output"]) / "query.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        query(request) if request.get("operation") == "query" else calculate(request)
    except Exception as error:
        message = str(error)
        friendly = (
            message
            if message and "\u4e00" <= message[0] <= "\u9fff"
            else "频谱计算失败，请检查声音文件和本地 Praat 组件。"
        )
        Path(request["output"], "failure.json").write_text(
            json.dumps({"message": friendly, "detail": message}, ensure_ascii=False),
            encoding="utf-8",
        )
        raise
