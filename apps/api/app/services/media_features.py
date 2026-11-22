"""Local, deterministic feature extraction for the RAVDESS and COCO examples.

No model or data downloads, database access, or writes outside the output directory.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

try:
    from .praat_pitch import filtered_pitch, pitch_arrays, pitch_parameters, sha256
except ImportError:  # The existing media worker also loads this module as a script.
    from praat_pitch import filtered_pitch, pitch_arrays, pitch_parameters, sha256


def _json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _run(args):
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=0x08000000 if os.name == "nt" else 0,
        ).stdout
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{args[0]} failed: {exc.stderr[-3000:]}") from exc


def _source(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or out-of-root input file: {relative}")
    return path


def _model_path(options, key, env):
    value = options.get(key) or os.environ.get(env)
    if not value or not Path(value).exists():
        raise FileNotFoundError(
            f"Provide existing local {key} in options or {env}; automatic download is disabled"
        )
    return Path(value).resolve()


def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _visual_model(options):
    import torch
    from torchvision.models import ResNet18_Weights, resnet18

    torch.set_num_threads(4)
    weights = _model_path(options, "resnet_weights", "STATFORGE_RESNET18_WEIGHTS")
    if not weights.is_file():
        raise ValueError("resnet_weights must be a file")
    visual = resnet18(weights=None)
    visual.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    visual.fc = torch.nn.Identity()
    visual.eval()
    return visual, ResNet18_Weights.IMAGENET1K_V1.transforms(), weights


def _language_model(options):
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(4)
    checkpoint = _model_path(options, "minilm_path", "STATFORGE_MINILM_PATH")
    if not checkpoint.is_dir():
        raise ValueError("minilm_path must be a directory")
    tokenizer = AutoTokenizer.from_pretrained(str(checkpoint), local_files_only=True)
    language = AutoModel.from_pretrained(str(checkpoint), local_files_only=True).cpu().eval()
    if language.config.hidden_size != 384:
        raise ValueError("MiniLM checkpoint must have 384 hidden dimensions")
    return tokenizer, language, checkpoint


def _models(options):
    visual, transform, weights = _visual_model(options)
    tokenizer, language, checkpoint = _language_model(options)
    return visual, transform, tokenizer, language, weights, checkpoint


def _image_vectors(images, model, transform):
    import torch
    from PIL import Image

    with torch.inference_mode():
        return (
            model(torch.stack([transform(Image.fromarray(image)) for image in images]))
            .cpu()
            .numpy()
        )


def _text_vectors(texts, tokenizer, model):
    import torch

    encoded = tokenizer(texts, padding=True, truncation=True, max_length=256, return_tensors="pt")
    with torch.inference_mode():
        hidden = model(**encoded).last_hidden_state
        mask = encoded["attention_mask"].unsqueeze(-1)
        pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
    return pooled.cpu().numpy(), encoded["attention_mask"].sum(dim=1).cpu().numpy()


def _tfidf(texts):
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer()
    vectors = vectorizer.fit_transform(texts)
    return vectors.toarray().astype(np.float32), vectorizer.get_feature_names_out().tolist()


def _setup(root, samples, output, options):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not root.is_dir() or not samples:
        raise ValueError("A valid root and at least one sample are required")
    if output == root or root.is_relative_to(output):
        raise ValueError("Output must not be the input root or its ancestor")
    output.mkdir(parents=True, exist_ok=True)
    return root, output, _models(options)


def _parameters(output, weights, checkpoint, extra, options):
    packages = [
        "numpy",
        "librosa",
        "soundfile",
        "opencv-python-headless",
        "opencv-python",
        "torch",
        "torchvision",
        "transformers",
        "scikit-learn",
        "Pillow",
    ]
    versions = {}
    for name in packages:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    versions["ffmpeg"] = _run([str(options.get("ffmpeg_path", "ffmpeg")), "-version"]).splitlines()[
        0
    ]
    versions["ffprobe"] = _run(
        [str(options.get("ffprobe_path", "ffprobe")), "-version"]
    ).splitlines()[0]
    params = {
        "versions": versions,
        "resnet": {
            "architecture": "resnet18",
            "declared_weights": "IMAGENET1K_V1",
            "official_sha256_prefix_match": _sha256(weights).startswith("f37072fd"),
            "path": str(weights),
            "sha256": _sha256(weights),
            ("preprocessing"): (
                "torchvision IMAGENET1K_V1 transform"
                "s: resize 256, center crop 224, nor"
                "malize ImageNet"
            ),
        },
        "minilm": {
            "declared_checkpoint": "sentence-transformers/all-MiniLM-L6-v2",
            "declared_revision": options.get("minilm_revision"),
            ("identity_note"): (
                "local file hashes recorded; checkpo"
                "int identity is supplied by caller "
                "and not independently authenticated"
            ),
            "local_path": str(checkpoint),
            "file_sha256": {
                str(p.relative_to(checkpoint)): _sha256(p)
                for p in sorted(checkpoint.rglob("*"))
                if p.is_file()
            },
            "max_length": 256,
            "pooling": "attention-mask mean, L2 normalize",
            "device": "cpu",
        },
        **extra,
    }
    path = output / "parameters.json"
    _json(path, params)
    return path


def _save(output, rows, arrays, index, params):
    summary = output / "summary.csv"
    with summary.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    npz = output / "arrays.npz"
    np.savez_compressed(npz, **arrays)
    index_path = output / "index.json"
    _json(index_path, index)
    return {
        "summary_csv": str(summary),
        "arrays_npz": str(npz),
        "index_json": str(index_path),
        "parameters_json": str(params),
        "media_files": index.get("media_files", []),
    }


def _progress(callback, current, total, label):
    if callback:
        callback(current, total, label)


def _geometry(width, height):
    # torchvision Resize(256) uses integer truncation for the long side.
    if width <= height:
        resized_width, resized_height = 256, int(256 * height / width)
    else:
        resized_width, resized_height = int(256 * width / height), 256
    left = int(round((resized_width - 224) / 2.0))
    top = int(round((resized_height - 224) / 2.0))
    return {
        "original_size_wh": [width, height],
        "resized_size_wh": [resized_width, resized_height],
        "scale_xy": [resized_width / width, resized_height / height],
        "crop_xywh_resized": [left, top, 224, 224],
        "crop_xywh_original": [
            left * width / resized_width,
            top * height / resized_height,
            224 * width / resized_width,
            224 * height / resized_height,
        ],
    }


def _extract_audio(
    source,
    target,
    first,
    last,
    ffmpeg,
    sample_rate=16000,
    channels=1,
    codec="pcm_s16le",
    native_clock=False,
):
    if native_clock:
        import soundfile as sf

        try:
            from .audio_pcm import canonical_audio, read_selection
        except ImportError:
            from audio_pcm import canonical_audio, read_selection
        meta = canonical_audio(source, ffmpeg, Path(os.environ["TEMP"]) / "audio-native")
        values, rate, left, _ = read_selection(meta["path"], first, last, channels=None)
        with tempfile.TemporaryDirectory(
            dir=Path(target).parent, prefix="native-selection-"
        ) as directory:
            native = Path(directory) / "selected.wav"
            sf.write(native, values, rate, subtype="FLOAT")
            _run(
                [
                    str(ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(native),
                    "-ac",
                    str(channels),
                    "-ar",
                    str(sample_rate),
                    "-c:a",
                    codec,
                    str(target),
                ]
            )
        return left / rate, sf.info(target).frames
    # ashowinfo is before asetpts so it reports the actual trimmed, resampled
    # first buffer on the original container timebase, not the WAV's zero clock.
    filters = f"aresample\
={sample_rate},atrim=start={first:.9f}:end={last:.9f},ashowinfo,asetpts=PTS-STARTPTS"
    args = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        "-y",
        "-copyts",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-af",
        filters,
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        codec,
        str(target),
    ]
    try:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable not found: {ffmpeg}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Audio extraction failed: {exc.stderr[-3000:]}") from exc
    records = re.findall(
        r"\bn:(\d+)\s+pts:(-?\d+)\s+pts_time:([-+0-9.eE]+).*?\bnb_samples:(\d+)", result.stderr
    )
    if not records:
        raise ValueError("ffmpeg did not report the first audio PTS via ashowinfo")
    first_pts = float(records[0][2])
    samples_before = 0
    for _, _, pts_time, count in records:
        # ashowinfo prints a rounded decimal; reject gaps or non-monotonic PTS.
        if abs(float(pts_time) - (first_pts + samples_before / sample_rate)) > 1 / sample_rate:
            raise ValueError(
                "Audio PTS is discontinuous; one linear sample-to-PTS mapping is not valid"
            )
        samples_before += int(count)
    return first_pts, samples_before


def _praat_features(
    signal, sample_rate, first_pts, audio_origin, steps, options, key, arrays, row, audio_file
):
    """Measure the source-rate mono segment with the selected real pitch engine."""
    try:
        import parselmouth
    except ImportError as exc:
        raise RuntimeError("未找到 Praat 声音处理组件，请使用包含该组件的桌面版。") from exc

    sound = parselmouth.Sound(signal, sampling_frequency=sample_rate)
    time_step = float(options.get("praat_time_step", 0.01))
    pitch_floor = float(options.get("pitch_floor_hz", 75))
    pitch_ceiling = float(options.get("pitch_ceiling_hz", 600))
    formant_ceiling = float(options.get("formant_ceiling_hz", 5500))
    formant_window = float(options.get("formant_window_s", 0.025))
    preemphasis = float(options.get("formant_preemphasis_hz", 50))
    if not 0 < pitch_floor < pitch_ceiling or not 0 < time_step <= 1:
        raise ValueError("请检查基频范围和时间步长。")

    def save_times(label, obj):
        relative = np.asarray(obj.xs(), dtype=np.float64)
        arrays[f"{key}_{label}_times"] = first_pts + relative
        arrays[f"{key}_{label}_times_player"] = first_pts - audio_origin + relative
        return relative

    if "pitch" in steps:
        method = options.get("pitch_method", "raw_autocorrelation")
        if method.startswith("filtered_"):
            pitch = filtered_pitch(audio_file, options)
        else:
            expected = options.get("pitch_runtime")
            if expected and (
                expected.get("praat_version") != parselmouth.PRAAT_VERSION
                or expected.get("version") != parselmouth.__version__
                or expected.get("sha256") != sha256(parselmouth.__file__)
            ):
                raise ValueError("原始基频组件在启动后发生变化，请重新运行以保存新版本的结果。")
            measure = sound.to_pitch_cc if method == "raw_cross_correlation" else sound.to_pitch_ac
            pitch = measure(**pitch_parameters(options))
        save_times("pitch", pitch)
        data = pitch_arrays(pitch)
        for label, array in data.items():
            arrays[f"{key}_pitch_{label}"] = array
        values, valid = data["hz"], data["valid"]
        voiced = values[valid]
        row["pitch_mean_hz"] = float(voiced.mean()) if voiced.size else None
        row["pitch_cv"] = float(voiced.std(ddof=1) / voiced.mean()) if voiced.size >= 2 else None
        row["pitch_range_hz"] = float(voiced.max() - voiced.min()) if voiced.size else None
        row["pitch_valid_frames"] = int(voiced.size)
        row["pitch_total_frames"] = int(values.size)

    if "intensity" in steps:
        intensity = sound.to_intensity(
            minimum_pitch=float(options.get("intensity_minimum_pitch_hz", 75)),
            time_step=float(options.get("intensity_time_step", 0.01)),
            subtract_mean=bool(options.get("intensity_subtract_mean", True)),
        )
        save_times("intensity", intensity)
        values = np.asarray(intensity.values[0], dtype=np.float64)
        valid = np.isfinite(values)
        arrays[f"{key}_intensity_db"] = np.where(valid, values, np.nan)
        arrays[f"{key}_intensity_valid"] = valid
        average = intensity.get_average(
            averaging_method=parselmouth.Intensity.AveragingMethod.ENERGY
        )
        row["intensity_energy_mean_db"] = float(average) if np.isfinite(average) else None

    if "formants" in steps:
        if not 0 < formant_ceiling <= sample_rate / 2:
            raise ValueError(
                "原始音频采样率不足以测量所选的共振峰上限，请降低上限或选择采样率更高的音频。"
            )
        formants = sound.to_formant_burg(
            time_step=time_step,
            max_number_of_formants=5,
            maximum_formant=formant_ceiling,
            window_length=formant_window,
            pre_emphasis_from=preemphasis,
        )
        times = save_times("formant", formants)
        row["formant_total_frames"] = len(times)
        for number in range(1, 5):
            values = np.asarray(
                [formants.get_value_at_time(number, float(t)) for t in times], dtype=np.float64
            )
            valid = np.isfinite(values) & (values > 0)
            arrays[f"{key}_f{number}_hz"] = np.where(valid, values, np.nan)
            arrays[f"{key}_f{number}_valid"] = valid
            row[f"f{number}_mean_hz"] = float(values[valid].mean()) if valid.any() else None
            bandwidth = np.asarray(
                [formants.get_bandwidth_at_time(number, float(t)) for t in times], dtype=np.float64
            )
            bandwidth_valid = valid & np.isfinite(bandwidth) & (bandwidth > 0)
            arrays[f"{key}_b{number}_hz"] = np.where(bandwidth_valid, bandwidth, np.nan)
            arrays[f"{key}_b{number}_valid"] = bandwidth_valid
            row[f"b{number}_mean_hz"] = (
                float(bandwidth[bandwidth_valid].mean()) if bandwidth_valid.any() else None
            )
            row[f"b{number}_valid_frames"] = int(bandwidth_valid.sum())

    return {"parselmouth": parselmouth.__version__, "praat": parselmouth.PRAAT_VERSION}


def _read_image(path):
    import cv2

    encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR) if encoded.size else None


def _extract_frames(source, output, sample_index, first, last, requested, ffmpeg, ffprobe):
    import cv2

    frames = json.loads(
        _run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(source),
            ]
        )
    )["frames"]
    timestamps = [
        float(f["best_effort_timestamp_time"]) for f in frames if "best_effort_timestamp_time" in f
    ]
    if len(timestamps) != len(frames) or not timestamps:
        raise ValueError(f"Video frames lack timestamps: {source}")
    eligible = [j for j, pts in enumerate(timestamps) if first <= pts < last]
    if not eligible:
        raise ValueError(f"No video frames in selected segment: {source}")
    indices = np.asarray(
        [
            eligible[n]
            for n in np.unique(
                np.linspace(0, len(eligible) - 1, min(requested, len(eligible)), dtype=int)
            )
        ]
    )
    images, paths, geometry = [], [], []
    for j, n in enumerate(indices):
        path = output / f"sample_{sample_index:04d}_frame_{j:02d}.png"
        _run(
            [
                str(ffmpeg),
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                f"select=eq(n\\,{int(n)})",
                "-fps_mode",
                "vfr",
                "-frames:v",
                "1",
                str(path),
            ]
        )
        image = _read_image(path)
        if image is None:
            raise ValueError(f"第 {j + 1} 张抽帧图片无法读取，请重新取帧。")
        images.append(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        geometry.append(_geometry(image.shape[1], image.shape[0]))
        paths.append(str(path))
    gray = [cv2.cvtColor(im, cv2.COLOR_RGB2GRAY) for im in images]
    brightness = np.array([np.mean(g) for g in gray], dtype=np.float32)
    movement = np.array(
        [np.nan] + [np.mean(cv2.absdiff(a, b)) for a, b in zip(gray, gray[1:])], dtype=np.float32
    )
    return (
        indices,
        np.array([timestamps[n] for n in indices]),
        images,
        paths,
        geometry,
        brightness,
        movement,
    )


def process_ravdess(
    root: Path, samples: list[dict], output: Path, options: dict, progress_callback=None
) -> dict:
    import librosa
    import soundfile as sf

    root, output, models = _setup(root, samples, output, options)
    visual, transform, tokenizer, language, weights, checkpoint = models
    ffmpeg, ffprobe = options.get("ffmpeg_path", "ffmpeg"), options.get("ffprobe_path", "ffprobe")
    texts = [str(s["transcript"]) for s in samples]
    text_vectors, token_lengths = _text_vectors(texts, tokenizer, language)
    tfidf, vocab = _tfidf(texts)
    arrays = {"minilm": text_vectors, "token_lengths": token_lengths, "tfidf": tfidf}
    rows, entries, media = [], [], []
    frames_requested = int(options.get("frames_per_video", 5))
    if frames_requested < 1 or frames_requested > 30:
        raise ValueError("frames_per_video must be between 1 and 30")
    start_relative = float(options.get("start") or 0.0)
    end_relative = float(options["end"]) if options.get("end") is not None else None
    if (
        not np.isfinite(start_relative)
        or start_relative < 0
        or (
            end_relative is not None
            and (not np.isfinite(end_relative) or end_relative <= start_relative)
        )
    ):
        raise ValueError("start/end must be finite player-relative seconds with 0 <= start < end")
    offsets_file = root / "av_pair_verification.json"
    offsets = (
        json.loads(offsets_file.read_text(encoding="utf-8")) if offsets_file.is_file() else None
    )
    offset_pairs = {pair["sample_id"]: pair for pair in offsets.get("pairs", [])} if offsets else {}
    for i, sample in enumerate(samples):
        _progress(progress_callback, i, len(samples), str(sample["sample_id"]))
        source = _source(root, sample["video_av"])
        probe = json.loads(
            _run(
                [
                    str(ffprobe),
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
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        container = probe.get("format", {})
        if "start_time" not in container or "duration" not in container:
            raise ValueError(f"Container start_time and duration are required: {source}")
        origin, duration = float(container["start_time"]), float(container["duration"])
        end = min(end_relative if end_relative is not None else duration, duration)
        if start_relative >= end:
            raise ValueError(f"Selected segment is outside media duration: {source}")
        first, last = origin + start_relative, origin + end
        audio_path = output / f"sample_{i:04d}_audio.wav"
        audio_first_pts, filtered_samples = _extract_audio(source, audio_path, first, last, ffmpeg)
        signal, sr = sf.read(audio_path, dtype="float32")
        if sr != 16000 or len(signal) < 400:
            raise ValueError(f"Audio selection is shorter than 25 ms or invalid: {source}")
        if filtered_samples != len(signal):
            raise ValueError(f"Audio output sample count disagrees with ashowinfo: {source}")
        mfcc = librosa.feature.mfcc(
            y=signal,
            sr=sr,
            n_mfcc=20,
            n_fft=400,
            hop_length=160,
            win_length=400,
            window="hann",
            center=False,
            n_mels=128,
            htk=False,
            norm="ortho",
            dct_type=2,
        )
        rms = librosa.feature.rms(y=signal, frame_length=400, hop_length=160, center=False)[0]
        times = audio_first_pts + (np.arange(mfcc.shape[1]) * 160 + 200) / sr
        indices, frame_pts, images, paths, geometry, brightness, movement = _extract_frames(
            source, output, i, first, last, frames_requested, ffmpeg, ffprobe
        )
        key = f"sample_{i:04d}"
        arrays.update(
            {
                f"{key}_mfcc": mfcc,
                f"{key}_rms": rms,
                f"{key}_audio_times": times,
                f"{key}_audio_times_player": times - origin,
                f"{key}_frame_pts": frame_pts,
                f"{key}_frame_indices": indices,
                f"{key}_brightness": brightness,
                f"{key}_movement": movement,
                f"{key}_resnet": _image_vectors(images, visual, transform),
            }
        )
        covered_samples = (mfcc.shape[1] - 1) * 160 + 400
        entries.append(
            {
                "sample": sample,
                "array_prefix": key,
                "text_row": i,
                "audio_file": str(audio_path),
                "frame_files": paths,
                "frame_geometry": geometry,
                "video_stream": video,
                "audio_stream": audio,
                "format": container,
                "segment": {
                    "player_start": start_relative,
                    "player_end": end,
                    "container_start_pts_seconds": first,
                    "container_end_pts_seconds": last,
                    "boundary": "[start,end)",
                    "first_audio_pts_seconds": audio_first_pts,
                    "valid_audio_samples": len(signal),
                    "analysis_covered_samples": covered_samples,
                    "trailing_samples_excluded_from_full_windows": len(signal) - covered_samples,
                },
                "independent_wav_offset_estimate": offset_pairs.get(str(sample["sample_id"])),
                "offset_source": str(offsets_file) if offsets else None,
                "offset_method": offsets.get("method") if offsets else None,
                ("offset_note"): (
                    "estimated relative to decoded video"
                    " audio; not an author-provided prec"
                    "ise alignment"
                ),
            }
        )
        media.extend([str(audio_path), *paths])
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "actor_id": sample.get("actor_id", ""),
                "emotion": sample.get("emotion", ""),
                "start": start_relative,
                "end": end,
                "duration_seconds": len(signal) / sr,
                "mean_frame_rms": float(rms.mean()),
                "brightness_mean": float(brightness.mean()),
                "frame_count": len(indices),
            }
        )
    params = _parameters(
        output,
        weights,
        checkpoint,
        {
            "audio": {
                "source": "MP4 embedded audio",
                "sample_rate": 16000,
                "channels": 1,
                "window_samples": 400,
                "hop_samples": 160,
                "n_fft": 400,
                "window": "hann",
                "center": False,
                "mfcc": 20,
                "n_mels": 128,
                "rms": "per-frame amplitude RMS; summary is mean frame RMS",
                ("time"): (
                    "ashowinfo first trimmed/resampled P"
                    "TS plus sample offsets; no separate"
                    " stream start offset"
                ),
            },
            "frames": {
                ("selection"): (
                    "evenly spaced eligible decoded fram"
                    "e indices, actual container PTS in "
                    "[start,end)"
                ),
                "requested": frames_requested,
                ("motion"): (
                    "mean absolute grayscale difference "
                    "from preceding sampled frame; first"
                    " missing (NaN)"
                ),
            },
            "tfidf_vocabulary": vocab,
            ("limitations"): (
                "Two fixed statements and two actors"
                " do not validate emotion generaliza"
                "tion; independent WAV offset is an "
                "estimate."
            ),
        },
        options,
    )
    _progress(progress_callback, len(samples), len(samples), "complete")
    return _save(
        output, rows, arrays, {"kind": "ravdess", "entries": entries, "media_files": media}, params
    )


def process_media(
    root: Path, samples: list[dict], output: Path, options: dict, progress_callback=None
) -> dict:
    """Extract only requested features from root-relative generic media inputs."""
    root, output = Path(root).resolve(), Path(output).resolve()
    if not root.is_dir() or not samples:
        raise ValueError("A valid root and at least one sample are required")
    if output == root or root.is_relative_to(output):
        raise ValueError("Output must not be the input root or its ancestor")
    allowed = {
        "extract_audio",
        "mfcc",
        "rms",
        "frames",
        "resnet",
        "tfidf",
        "minilm",
        "pitch",
        "intensity",
        "formants",
        "speech_rate",
    }
    requested = options.get("steps", [])
    if not isinstance(requested, list) or any(
        not isinstance(x, str) or x not in allowed for x in requested
    ):
        raise ValueError("处理步骤包含未支持的项目。")
    if len(set(requested)) != len(requested):
        raise ValueError("steps must not contain duplicates")
    steps = set(requested)
    dependencies = {}
    if (
        steps & {"mfcc", "rms", "pitch", "intensity", "formants", "speech_rate"}
        and "extract_audio" not in steps
    ):
        steps.add("extract_audio")
        dependencies["extract_audio"] = "required by audio features"
    if "resnet" in steps and "frames" not in steps:
        steps.add("frames")
        dependencies["frames"] = "required by resnet"
    source_kind = options.get("audio_source", "video")
    if source_kind not in ("video", "independent"):
        raise ValueError("audio_source must be video or independent")

    def integer(name, default, low, high):
        value = options.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} must be an integer between {low} and {high}")
        return value

    def milliseconds(name, default):
        value = options.get(name, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not np.isfinite(value)
            or not 0 < value <= 10000
        ):
            raise ValueError(
                f"{name} must be a finite number greater than 0 and at most 10000 milliseconds"
            )
        return float(value)

    sr = integer("sample_rate", 16000, 8000, 192000)
    channels = integer("channels", 1, 1, 2)
    window_ms = milliseconds("window_ms", 25.0)
    hop_ms = milliseconds("hop_ms", 10.0)
    n_mfcc = integer("n_mfcc", 20, 1, 128)
    frame_count = integer("frames_per_video", 5, 1, 30)
    win, hop = max(1, round(sr * window_ms / 1000)), max(1, round(sr * hop_ms / 1000))
    try:
        start = float(options.get("start", 0) or 0)
        end_option = options.get("end")
        end_option = float(end_option) if end_option is not None else None
    except (ValueError, TypeError) as exc:
        raise ValueError("start/end must be finite player-relative seconds") from exc
    if (
        not np.isfinite(start)
        or start < 0
        or (end_option is not None and (not np.isfinite(end_option) or end_option <= start))
    ):
        raise ValueError("start/end must be finite player-relative seconds with 0 <= start < end")
    ids = [s.get("sample_id") for s in samples]
    if any(not isinstance(x, str) or not x.strip() for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Each sample requires a unique non-empty sample_id")
    for sample in samples:
        for field in ("video_av", "audio", "text"):
            value = sample.get(field)
            if value is not None and (
                not isinstance(value, str)
                or not value
                or Path(value).is_absolute()
                or Path(value).drive
            ):
                raise ValueError(f"{field} must be a root-relative file path")
        if (
            not isinstance(sample.get("title"), str)
            or not sample["title"].strip()
            or not (sample.get("video_av") or sample.get("audio"))
        ):
            raise ValueError("每个样本需要名称和视频或音频。")
        if not sample.get("video_av") and steps & {"frames", "resnet"}:
            raise ValueError(f"{sample['title']} 是音频文件，不能提取视频帧。")
    if "speech_rate" in steps and options.get("speech_count") is not None and len(samples) != 1:
        raise ValueError("手动输入的词数仅适用于一个样本。")
    output.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = options.get("ffmpeg_path", "ffmpeg"), options.get("ffprobe_path", "ffprobe")
    arrays, rows, entries, media = {}, [], [], []
    params = {
        "requested_steps": requested,
        "executed_steps": [
            x
            for x in (
                "extract_audio",
                "mfcc",
                "rms",
                "pitch",
                "intensity",
                "formants",
                "speech_rate",
                "frames",
                "resnet",
                "tfidf",
                "minilm",
            )
            if x in steps
        ],
        "dependencies": dependencies,
        "audio": {
            "source": "independent" if not any(s.get("video_av") for s in samples) else source_kind,
            "sample_rate": sr,
            "channels": channels,
            "window_ms": window_ms,
            "hop_ms": hop_ms,
            "window_samples": win,
            "hop_samples": hop,
            "effective_window_ms": 1000 * win / sr,
            "effective_hop_ms": 1000 * hop / sr,
            "n_mfcc": n_mfcc,
            ("array_dimensions"): (
                "mfcc [channel, coefficient, window]; rms [channel, window]; audio_times [window]"
            ),
            ("independent_alignment"): (
                "Independent WAV uses audio_seconds "
                "= video_seconds + the user-specifie"
                "d offset; absent offset preserves l"
                "egacy equal times. Association and "
                "content review do not establish pre"
                "cise synchronization."
            ),
        },
        "frames": {
            "requested": frame_count,
            "selection": "evenly spaced eligible decoded frame indices; PTS in [start,end)",
        },
        "versions": {},
    }
    params["audio"]["rms_method"] = {
        ("mean_frame_rms"): (
            "arithmetic mean over channels and f"
            "ull unpadded rectangular frames; tr"
            "ailing incomplete frame excluded"
        ),
        ("whole_signal_rms"): (
            "square root of mean of squared extr"
            "acted floating-point PCM samples ac"
            "ross all samples and channels, incl"
            "uding trailing samples"
        ),
        ("boundary"): (
            "extracted audio selection [start,en"
            "d); full frames start at sample 0 a"
            "nd advance by hop_samples"
        ),
        ("waveform"): (
            "per bucket signed maximum absolute "
            "value of arithmetic channel mean; t"
            "imestamp is that sample on the prim"
            "ary player clock"
        ),
    }
    for package in (
        "numpy",
        "librosa",
        "soundfile",
        "praat-parselmouth",
        "opencv-python",
        "opencv-python-headless",
        "torch",
        "torchvision",
        "transformers",
        "scikit-learn",
        "Pillow",
    ):
        try:
            params["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    params["versions"]["ffprobe"] = _run([str(ffprobe), "-version"]).splitlines()[0]
    if steps & {"extract_audio", "frames"}:
        params["versions"]["ffmpeg"] = _run([str(ffmpeg), "-version"]).splitlines()[0]
    texts = []
    if steps & {"tfidf", "minilm"}:
        for sample in samples:
            value = sample.get("transcript")
            if value is None and sample.get("text"):
                value = _source(root, sample["text"]).read_text(encoding="utf-8")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Text step requires transcript or UTF-8 text file for {sample['sample_id']}"
                )
            texts.append(value)
    if "tfidf" in steps:
        _progress(progress_callback, 0, len(samples), "TF-IDF · 文本编码")
        arrays["tfidf"], params["tfidf_vocabulary"] = _tfidf(texts)
    if "minilm" in steps:
        _progress(progress_callback, 0, len(samples), "MiniLM · 文本编码")
        tokenizer, language, checkpoint = _language_model(options)
        arrays["minilm"], arrays["token_lengths"] = _text_vectors(texts, tokenizer, language)
        params["minilm"] = {
            "local_path": str(checkpoint),
            "declared_revision": options.get("minilm_revision"),
            "file_sha256": {
                str(p.relative_to(checkpoint)): _sha256(p)
                for p in sorted(checkpoint.rglob("*"))
                if p.is_file()
            },
            "pooling": "attention-mask mean, L2 normalize",
            "max_length": 256,
            "device": "cpu",
        }
    if "resnet" in steps:
        _progress(progress_callback, 0, len(samples), "加载 ResNet18")
        visual, transform, weights = _visual_model(options)
        digest = _sha256(weights)
        params["resnet"] = {
            "architecture": "resnet18",
            "path": str(weights),
            "sha256": digest,
            "official_sha256_prefix_match": digest.startswith("f37072fd"),
            "preprocessing": "torchvision IMAGENET1K_V1 transforms",
        }
    if "extract_audio" in steps:
        import soundfile as sf
    if steps & {"mfcc", "rms"}:
        import librosa
    if steps & {"pitch", "intensity", "formants"}:
        params["praat"] = {
            "pitch_method": options.get("pitch_method", "raw_autocorrelation"),
            "pitch_engine": options.get("pitch_runtime"),
            "pitch_parameters": pitch_parameters(options),
            "pitch_floor_hz": options.get("pitch_floor_hz", 75),
            "pitch_ceiling_hz": options.get("pitch_ceiling_hz", 600),
            "time_step_seconds": options.get("praat_time_step", 0.01),
            ("pitch_summary"): (
                "positive finite selected frequencie"
                "s at or below Pitch ceiling; mean, "
                "sample-standard-deviation/mean (CV)"
                ", max-minus-min, valid and total fr"
                "ame counts"
            ),
            "intensity_method": "Praat intensity, energy average",
            "intensity_subtract_mean": options.get("intensity_subtract_mean", True),
            "intensity_minimum_pitch_hz": options.get("intensity_minimum_pitch_hz", 75),
            "intensity_time_step_seconds": options.get("intensity_time_step", 0.01),
            "formant_method": "Burg, 5 maximum formants",
            "formant_ceiling_hz": options.get("formant_ceiling_hz", 5500),
            "formant_window_seconds": options.get("formant_window_s", 0.025),
            "formant_preemphasis_hz": options.get("formant_preemphasis_hz", 50),
            ("formant_summary"): (
                "mean of available positive values p"
                "er formant across all frames, no vo"
                "iced-frame filter"
            ),
            ("bandwidth_method"): (
                "B1-B4 bandwidths from the same Burg"
                " object and frame times as F1-F4; p"
                "ositive finite values with valid co"
                "rresponding center frequency"
            ),
            "input": "original source sample rate, mono, floating-point PCM",
        }

    def probe(path, independent_audio=False):
        result = json.loads(
            _run(
                [
                    str(ffprobe),
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        fmt = result.get("format", {})
        stream = (
            next((x for x in result.get("streams", []) if x.get("codec_type") == "audio"), None)
            if independent_audio
            else None
        )
        if independent_audio and stream:
            try:
                from .audio_pcm import canonical_audio
            except ImportError:
                from audio_pcm import canonical_audio
            decoded = canonical_audio(path, ffmpeg, Path(os.environ["TEMP"]) / "audio-native")
            return result, fmt, 0.0, decoded["duration"], decoded["clock"]

        def finite(value):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return parsed if np.isfinite(parsed) else None

        origin = finite(fmt.get("start_time"))
        origin_source = "format.start_time"
        if origin is None and independent_audio and stream:
            origin = finite(stream.get("start_time"))
            origin_source = "audio_stream.start_time"
            if origin is None:
                frame_data = json.loads(
                    _run(
                        [
                            str(ffprobe),
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
                            str(path),
                        ]
                    )
                )
                frames = frame_data.get("frames", [])
                if frames:
                    origin = finite(frames[0].get("best_effort_timestamp_time"))
                    if origin is None:
                        origin = finite(frames[0].get("pts_time"))
                origin_source = "first_audio_frame_timestamp"
            if (
                origin is None
                and "wav" in str(fmt.get("format_name", "")).split(",")
                and str(stream.get("codec_name", "")).startswith("pcm_")
            ):
                origin, origin_source = 0.0, "pcm_wav_local_zero"
        duration = finite(fmt.get("duration"))
        if Path(path).suffix.lower() == ".wav":
            import soundfile

            header = soundfile.info(path)
            duration = header.frames / header.samplerate
        if duration is None and stream:
            duration = finite(stream.get("duration"))
        if origin is None or duration is None or duration <= 0:
            raise ValueError(f"Missing or invalid media start time or duration: {path}")
        return result, fmt, origin, duration, origin_source

    for i, sample in enumerate(samples):
        _progress(progress_callback, i, len(samples), str(sample["title"]))
        has_video = bool(sample.get("video_av"))
        source = _source(root, sample["video_av"] if has_video else sample["audio"])
        info, fmt, origin, duration, _ = probe(source, independent_audio=not has_video)
        video = next((x for x in info.get("streams", []) if x.get("codec_type") == "video"), None)
        if has_video and video is None:
            raise ValueError(f"Video stream is required: {source}")
        end = min(end_option if end_option is not None else duration, duration)
        if start >= end:
            raise ValueError(f"Selected segment is outside media duration: {source}")
        first, last = origin + start, origin + end
        key = f"sample_{i:04d}"
        segment = {
            "player_start": start,
            "player_end": end,
            "container_start_pts_seconds": first,
            "container_end_pts_seconds": last,
            "boundary": "[start,end)",
        }
        entry = {
            "sample": sample,
            "array_prefix": key,
            "segment": segment,
            "audio_file": None,
            "frame_files": [],
            "video_stream": video,
            "format": fmt,
        }
        row = {
            "sample_id": sample["sample_id"],
            "title": sample["title"],
            "start": start,
            "end": end,
        }
        if texts:
            entry["text_row"] = i
        if "extract_audio" in steps:
            _progress(progress_callback, i, len(samples), f"{sample['title']} · FFmpeg 音轨")
            audio_source = (
                source
                if source_kind == "video" or not has_video
                else _source(root, sample.get("audio") or "")
            )
            if source_kind == "video" and has_video:
                audio_info, audio_fmt, audio_origin, audio_duration, audio_origin_source = (
                    info,
                    fmt,
                    origin,
                    duration,
                    "format.start_time",
                )
            else:
                audio_info, audio_fmt, audio_origin, audio_duration, audio_origin_source = probe(
                    audio_source, independent_audio=True
                )
            independent_audio = not (source_kind == "video" and has_video)
            audio_stream = next(
                (x for x in audio_info.get("streams", []) if x.get("codec_type") == "audio"), None
            )
            if audio_stream is None:
                raise ValueError(f"Audio stream is required: {audio_source}")
            alignment = sample.get("audio_alignment") or {}
            configured_offset = (
                alignment.get("offset_seconds")
                if has_video and source_kind == "independent"
                else None
            )
            offset = float(configured_offset) if configured_offset is not None else 0.0
            if not np.isfinite(offset):
                raise ValueError("独立音频时间偏移无效。")
            audio_start = start + offset
            audio_end = end + offset if configured_offset is not None else min(end, audio_duration)
            if configured_offset is not None and (
                audio_start < 0 or audio_end > audio_duration + 1e-6
            ):
                raise ValueError("视频选段映射到独立音频后超出范围，请调整选段或偏移。")
            audio_end = min(audio_end, audio_duration)
            if audio_start >= audio_end:
                raise ValueError(f"Selected segment is outside audio duration: {audio_source}")
            audio_path = output / f"{key}_audio.wav"
            preprocessing = None
            if options.get("audio_filter", "none") != "none":
                try:
                    from .audio_preprocessing import filter_audio
                except ImportError:
                    from audio_preprocessing import filter_audio
                _progress(progress_callback, i, len(samples), f"{sample['title']} · Praat 滤波")
                audio_pts, filtered, preprocessing = filter_audio(
                    audio_source,
                    audio_path,
                    audio_start,
                    audio_end,
                    audio_origin,
                    int(audio_stream["sample_rate"]),
                    sr,
                    channels,
                    ffmpeg,
                    options,
                )
                entry["audio_preprocessing"] = preprocessing
            else:
                audio_pts, filtered = _extract_audio(
                    audio_source,
                    audio_path,
                    audio_origin + audio_start,
                    audio_origin + audio_end,
                    ffmpeg,
                    sr,
                    channels,
                    native_clock=independent_audio,
                )
            signal, actual_sr = sf.read(audio_path, dtype="float32", always_2d=True)
            if (
                actual_sr != sr
                or signal.shape[1] != channels
                or filtered != len(signal)
                or not len(signal)
            ):
                raise ValueError(
                    f"Extracted audio has invalid format or sample count: {audio_source}"
                )
            entry.update(
                {
                    "audio_file": str(audio_path),
                    "audio_stream": audio_stream,
                    "audio_format": audio_fmt,
                    "audio_source_file": str(audio_source),
                    "audio_origin_seconds": audio_origin,
                    "audio_origin_source": audio_origin_source,
                }
            )
            entry["audio_selection"] = (
                "video" if has_video and source_kind == "video" else "independent"
            )
            segment.update(
                {
                    "first_audio_pts_seconds": audio_pts,
                    "audio_player_start": audio_start,
                    "audio_player_end": audio_end,
                    "valid_audio_samples": len(signal),
                }
            )
            entry["audio_alignment"] = {
                "offset_seconds": configured_offset,
                "effective_offset_seconds": offset,
                "convention": "audio_seconds = video_seconds + offset_seconds",
                "content_reviewed": bool(alignment.get("content_reviewed", False)),
                "offset_status": "user_specified"
                if configured_offset is not None
                else "legacy_equal_times_unverified",
            }
            media.append(str(audio_path))
            row["duration_seconds"] = len(signal) / sr
            # Preserve a bounded, signed waveform without publishing the full PCM array.
            mono = signal.astype(np.float64).mean(axis=1)
            bounds = np.linspace(0, len(mono), min(len(mono), 1200) + 1, dtype=int)
            peak_indices = np.asarray(
                [
                    left + int(np.argmax(np.abs(mono[left:right])))
                    for left, right in zip(bounds[:-1], bounds[1:])
                ],
                dtype=int,
            )
            arrays[f"{key}_waveform"] = mono[peak_indices]
            arrays[f"{key}_waveform_times_player"] = (
                audio_pts - audio_origin - offset + peak_indices / sr
            )
            if steps & {"mfcc", "rms"}:
                _progress(
                    progress_callback, i, len(samples), f"{sample['title']} · librosa 声音特征"
                )
                if len(signal) < win:
                    raise ValueError(f"Audio selection is shorter than window_ms: {audio_source}")
                if "mfcc" in steps:
                    arrays[f"{key}_mfcc"] = np.stack(
                        [
                            librosa.feature.mfcc(
                                y=signal[:, c],
                                sr=sr,
                                n_mfcc=n_mfcc,
                                n_fft=win,
                                hop_length=hop,
                                win_length=win,
                                window="hann",
                                center=False,
                                n_mels=128,
                                htk=False,
                                norm="ortho",
                                dct_type=2,
                            )
                            for c in range(channels)
                        ]
                    )
                if "rms" in steps:
                    rms = np.stack(
                        [
                            librosa.feature.rms(
                                y=signal[:, c], frame_length=win, hop_length=hop, center=False
                            )[0]
                            for c in range(channels)
                        ]
                    )
                    arrays[f"{key}_rms"] = rms
                    row["mean_frame_rms"] = float(rms.mean())
                    row["whole_signal_rms"] = float(
                        np.sqrt(np.mean(np.square(signal.astype(np.float64))))
                    )
                count = 1 + (len(signal) - win) // hop
                times = audio_pts + (np.arange(count) * hop + win / 2) / sr
                arrays[f"{key}_audio_times"] = times
                arrays[f"{key}_audio_times_player"] = times - audio_origin - offset
                covered = (count - 1) * hop + win
                segment.update(
                    {
                        "analysis_covered_samples": covered,
                        "trailing_samples_excluded_from_full_windows": len(signal) - covered,
                    }
                )
            if steps & {"pitch", "intensity", "formants"}:
                _progress(progress_callback, i, len(samples), f"{sample['title']} · Praat 声音特征")
                source_rate = int(audio_stream.get("sample_rate") or 0)
                if source_rate <= 0:
                    raise ValueError(f"无法读取音频采样率：{audio_source.name}")
                praat_path = output / f"{key}_praat_audio.wav"
                if preprocessing:
                    praat_signal = signal.astype(np.float64).mean(axis=1)
                    praat_pts, praat_count, praat_rate = audio_pts, len(signal), sr
                    sf.write(praat_path, praat_signal, praat_rate, subtype="FLOAT")
                    params["praat"]["input"] = (
                        "saved filtered audio; arithmetic ch"
                        "annel mean; same playback and featu"
                        "re input"
                    )
                else:
                    praat_pts, praat_count = _extract_audio(
                        audio_source,
                        praat_path,
                        audio_origin + audio_start,
                        audio_origin + audio_end,
                        ffmpeg,
                        source_rate,
                        1,
                        codec="pcm_f32le",
                        native_clock=independent_audio,
                    )
                    praat_signal, praat_rate = sf.read(praat_path, dtype="float64")
                if (
                    (not preprocessing and praat_rate != source_rate)
                    or len(praat_signal) != praat_count
                    or not praat_count
                ):
                    raise ValueError(f"无法读取完整声音片段：{audio_source.name}")
                params["praat"]["versions"] = _praat_features(
                    praat_signal,
                    praat_rate,
                    praat_pts,
                    audio_origin + offset,
                    steps,
                    options,
                    key,
                    arrays,
                    row,
                    praat_path,
                )
                entry["praat_audio_file"] = str(praat_path)
                entry["praat_audio_sample_rate"] = praat_rate
                entry["praat_audio_sha256"] = _sha256(praat_path)
                entry["praat_audio_source"] = str(audio_path if preprocessing else audio_source)
                entry["praat_audio_source_sha256"] = _sha256(
                    audio_path if preprocessing else audio_source
                )
                entry["praat_audio_first_pts"] = praat_pts
                entry["praat_audio_sample_count"] = praat_count
                if "pitch" in steps and options.get(
                    "pitch_method", "raw_autocorrelation"
                ).startswith("filtered_"):
                    entry["pitch_frames_file"] = str(praat_path.with_suffix(".Pitch"))
                media.append(str(praat_path))
            if "speech_rate" in steps:
                # Container/player times can round to milliseconds; rate uses actual decoded
                # samples.
                whole_audio = (
                    abs(audio_start) <= 0.0005 and abs(audio_end - audio_duration) <= 0.0005
                )
                count = options.get("speech_count")
                unit = options.get("speech_count_unit", "english_words")
                if unit not in {"english_words", "chinese_characters"}:
                    raise ValueError("请选择英文词数或中文字数。")
                count_source = "manual"
                if count is None:
                    text = sample.get("transcript")
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("请关联对应文本，或输入所选片段的词数。")
                    text_range = sample.get("text_range")
                    if text_range:
                        source_key = "video_av" if has_video and source_kind == "video" else "audio"
                        selected_source = (
                            sample.get("video_id")
                            if source_key == "video_av"
                            else sample.get("audio_id")
                        )
                        selected_sha = sample.get("files", {}).get(source_key, {}).get("sha256")
                        if (
                            text_range.get("source_id") != selected_source
                            or text_range.get("end") is None
                            or text_range.get("source_sha256") != selected_sha
                            or abs(float(text_range["start"]) - audio_start) > 0.01
                            or abs(float(text_range["end"]) - audio_end) > 0.01
                        ):
                            raise ValueError(
                                "转录文字的来源或时间范围与当前音频不一致，请选择对应转录或填写词数。"
                            )
                    elif not whole_audio:
                        raise ValueError(
                            "语速：关联台词没有时间标记，请填写这段的词数或字数，或关联同一选段的带时间转录。"
                        )
                    count = (
                        len(re.findall(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*", text))
                        if unit == "english_words"
                        else len(
                            re.findall(
                                r"[\u3400-\u9fff\U00020000-\U0002fa1f\U00030000-\U000323af]", text
                            )
                        )
                    )
                    if count == 0:
                        raise ValueError("文本中没有所选计数类型，请检查文本和计数方式。")
                    count_source = (text_range or {}).get("method") or "associated_text"
                duration_basis = options.get("speech_duration_basis", "audio_selection")
                if duration_basis == "video_duration":
                    if not has_video or start > 0 or end < duration - 1e-6:
                        raise ValueError("使用视频时长计算语速时，请选择完整视频。")
                    if (
                        audio_end < end + offset - 1e-6
                        or audio_pts > audio_origin + audio_start + 0.01
                        or audio_pts + len(signal) / sr < audio_origin + audio_end - 0.01
                    ):
                        raise ValueError("声音未覆盖完整视频范围，无法使用完整视频时长计算语速。")
                    rate_duration = duration
                elif duration_basis == "audio_selection":
                    rate_duration = len(signal) / sr
                else:
                    raise ValueError("请选择语速计算时长。")
                row["speech_count"] = int(count)
                row["speech_count_unit"] = unit
                row["speech_rate_per_second"] = float(count / rate_duration)
                entry["speech_rate"] = {
                    "count": int(count),
                    "count_source": count_source,
                    "unit": unit,
                    "text_scope": "full_media" if whole_audio else "selected_range",
                    ("count_method"): (
                        "English alphabetic words including internal apostrophes/hyphens"
                    )
                    if unit == "english_words"
                    else "CJK unified ideograph characters",
                    "duration_seconds": rate_duration,
                    "duration_source": duration_basis,
                }
        if "frames" in steps:
            _progress(progress_callback, i, len(samples), f"{sample['title']} · 均匀取帧")
            indices, frame_pts, images, paths, geometry, brightness, movement = _extract_frames(
                source, output, i, first, last, frame_count, ffmpeg, ffprobe
            )
            arrays.update(
                {
                    f"{key}_frame_pts": frame_pts,
                    f"{key}_frame_indices": indices,
                    f"{key}_brightness": brightness,
                    f"{key}_movement": movement,
                }
            )
            if "resnet" in steps:
                arrays[f"{key}_resnet"] = _image_vectors(images, visual, transform)
            entry.update({"frame_files": paths, "frame_geometry": geometry})
            media.extend(paths)
            row.update({"brightness_mean": float(brightness.mean()), "frame_count": len(indices)})
        entries.append(entry)
        rows.append(row)
    param_path = output / "parameters.json"
    _json(param_path, params)
    _progress(progress_callback, len(samples), len(samples), "complete")
    return _save(
        output,
        rows,
        arrays,
        {"kind": "media", "entries": entries, "media_files": media},
        param_path,
    )


def _annotation(root, options, option, prefix):
    if options.get(option):
        return _source(root, options[option])
    candidates = sorted((root / "annotations").glob(f"{prefix}*.json"))
    if len(candidates) != 1:
        raise ValueError(f"Specify {option}: found {len(candidates)} {prefix} annotation files")
    return candidates[0]


def process_coco(
    root: Path, samples: list[dict], output: Path, options: dict, progress_callback=None
) -> dict:
    import cv2

    root, output, models = _setup(root, samples, output, options)
    visual, transform, tokenizer, language, weights, checkpoint = models
    captions_path = _annotation(root, options, "captions_json", "captions_")
    instances_path = _annotation(root, options, "instances_json", "instances_")
    captions_data = json.loads(captions_path.read_text(encoding="utf-8"))
    instances_data = json.loads(instances_path.read_text(encoding="utf-8"))
    captions_by_image, objects_by_image = {}, {}
    for annotation in captions_data["annotations"]:
        captions_by_image.setdefault(int(annotation["image_id"]), []).append(annotation)
    for annotation in instances_data["annotations"]:
        objects_by_image.setdefault(int(annotation["image_id"]), []).append(annotation)
    images_by_id = {int(item["id"]): item for item in instances_data["images"]}
    categories = {int(item["id"]): item for item in instances_data["categories"]}
    ids = [int(s.get("image_id", s.get("id"))) for s in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate COCO image_id")
    all_captions = [c for image_id in ids for c in captions_by_image.get(image_id, [])]
    if not all_captions:
        raise ValueError("No captions found for selected images")
    text_vectors, token_lengths = _text_vectors(
        [c["caption"] for c in all_captions], tokenizer, language
    )
    tfidf, vocabulary = _tfidf([c["caption"] for c in all_captions])
    arrays = {
        "caption_minilm": text_vectors,
        "caption_token_lengths": token_lengths,
        "caption_tfidf": tfidf,
        "caption_tfidf_cosine": tfidf @ tfidf.T,
    }
    entries, rows, image_vectors = [], [], []
    caption_row = 0
    for i, (sample, image_id) in enumerate(zip(samples, ids)):
        _progress(progress_callback, i, len(samples), str(image_id))
        if image_id not in images_by_id:
            raise ValueError(f"Image {image_id} absent from instances annotations")
        image_record = images_by_id[image_id]
        source = _source(root, str(Path("images") / image_record["file_name"]))
        bgr = _read_image(source)
        if bgr is None:
            raise ValueError(f"Cannot decode image: {source}")
        height, width = bgr.shape[:2]
        if (width, height) != (int(image_record["width"]), int(image_record["height"])):
            raise ValueError(f"Image dimensions disagree with annotation: {source}")
        image_vectors.append(
            _image_vectors([cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)], visual, transform)[0]
        )
        objects = []
        for obj in objects_by_image.get(image_id, []):
            x, y, w, h = map(float, obj["bbox"])
            # Bounding rectangle measurements; retain original bbox and segmentation separately.
            x0, y0 = max(0, int(np.floor(x))), max(0, int(np.floor(y)))
            x1, y1 = min(width, int(np.ceil(x + w))), min(height, int(np.ceil(y + h)))
            region = bgr[y0:y1, x0:x1]
            measurement = {
                "bbox_area_pixels": w * h,
                "clipped_pixel_bounds_xyxy": [x0, y0, x1, y1],
                "mean_rgb": cv2.mean(region)[:3][::-1] if region.size else None,
                "mean_gray": float(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).mean())
                if region.size
                else None,
            }
            objects.append(
                {
                    "annotation": obj,
                    "category": categories.get(int(obj["category_id"])),
                    "measurement": measurement,
                }
            )
        captions = captions_by_image.get(image_id, [])
        entries.append(
            {
                "image": image_record,
                "image_path": str(source),
                "input_sample": sample,
                "image_vector_row": i,
                "image_geometry": _geometry(width, height),
                "captions": [
                    {"annotation": c, "vector_row": caption_row + j} for j, c in enumerate(captions)
                ],
                "objects": objects,
                "segmentation_source": str(instances_path),
                "caption_source": str(captions_path),
            }
        )
        caption_row += len(captions)
        rows.append(
            {
                "image_id": image_id,
                "file_name": image_record["file_name"],
                "width": width,
                "height": height,
                "caption_count": len(captions),
                "object_count": len(objects),
                "brightness_mean": float(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).mean()),
            }
        )
    from sklearn.cluster import KMeans

    vectors = np.asarray(image_vectors)
    arrays["image_resnet"] = vectors
    # Fewer clusters when duplicate feature vectors or selected sample count is small.
    cluster_count = min(4, len(np.unique(vectors, axis=0)))
    clusters = (
        KMeans(n_clusters=cluster_count, random_state=0, n_init=10).fit_predict(vectors)
        if cluster_count > 1
        else np.zeros(len(vectors), dtype=int)
    )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms != 0)
    similarities = normalized @ normalized.T
    arrays["image_cosine"] = similarities
    for i, row in enumerate(rows):
        row["image_cluster"] = int(clusters[i])
        entries[i]["image_cluster"] = int(clusters[i])
    neighbors = [
        {
            "image_id": image_id,
            "neighbors": [
                {"image_id": ids[j], "cosine": float(similarities[i, j])}
                for j in sorted(
                    (j for j in range(len(ids)) if j != i),
                    key=lambda j: (-float(similarities[i, j]), ids[j]),
                )
            ],
        }
        for i, image_id in enumerate(ids)
    ]
    retrieval_path = output / "image_neighbors.json"
    _json(
        retrieval_path,
        {
            "space": "ResNet18 image vectors",
            "ranking": "cosine descending, image_id tie-break, self excluded",
            "entries": neighbors,
        },
    )
    params = _parameters(
        output,
        weights,
        checkpoint,
        {
            "tfidf_vocabulary": vocabulary,
            ("retrieval"): (
                "caption_tfidf_cosine is caption-to-"
                "caption similarity, rows follow ind"
                "ex caption vector_row; no image/tex"
                "t cross-space similarity"
            ),
            ("object_measurement"): (
                "rectangle clipped to image using fl"
                "oor(x,y), ceil(x+w,y+h); color is R"
                "GB, grayscale is OpenCV conversion;"
                " original segmentation and bbox ret"
                "ained"
            ),
            "annotation_files": {"captions": str(captions_path), "instances": str(instances_path)},
            "image_clustering": {
                "method": "KMeans",
                "clusters": cluster_count,
                "random_state": 0,
                "n_init": 10,
                "input": "unscaled ResNet18 vectors",
            },
        },
        options,
    )
    _progress(progress_callback, len(samples), len(samples), "complete")
    result = _save(
        output,
        rows,
        arrays,
        {
            "kind": "coco",
            "entries": entries,
            "categories": list(categories.values()),
            "image_neighbors_json": str(retrieval_path),
            "media_files": [],
        },
        params,
    )
    result["image_neighbors_json"] = str(retrieval_path)
    return result
