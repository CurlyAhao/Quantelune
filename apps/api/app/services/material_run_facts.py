"""Read bounded material facts from a verified saved media run."""

import csv
import json
import math
import uuid

from app.services import local_workspaces


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def read_run_file(output, name):
    path = local_workspaces.checked_path(output / name)
    if path.stat().st_size > 20_000_000:
        raise ValueError("invalid run artifact")
    return path


def measurement_range(entry, params):
    segment = entry.get("segment") or {}
    source = entry.get("audio_format") or entry.get("format") or {}
    duration = number(source.get("duration"))
    start = number(segment.get("player_start"))
    end = number(segment.get("audio_player_end", segment.get("player_end")))
    rates = [
        number((entry.get("audio_stream") or {}).get("sample_rate")),
        number((params.get("audio") or {}).get("sample_rate")),
    ]
    tolerance = max((1 / rate for rate in rates if rate and rate > 0), default=None)
    coverage = "unknown"
    if None not in (duration, start, end, tolerance):
        coverage = (
            "full" if abs(start) <= tolerance and abs(end - duration) <= tolerance else "selection"
        )
    return {
        "source_duration_seconds": duration,
        "measurement_start_seconds": start,
        "measurement_end_seconds": end,
        "coverage": coverage,
        "coverage_tolerance_seconds": tolerance,
    }


def material_facts(project, run, dataset_id, sha256):
    root = local_workspaces.project_root(project)
    if root is None or run.get("status") != "completed":
        return None
    try:
        identity = uuid.UUID(str(run["id"]))
        output = local_workspaces.checked_path(
            root / ".statforge" / "storage" / "media-runs" / identity.hex
        )
        index = json.loads(read_run_file(output, "index.json").read_text(encoding="utf-8-sig"))
        params = json.loads(
            read_run_file(output, "parameters.json").read_text(encoding="utf-8-sig")
        )
        inputs = run.get("parameters", {}).get("input_files", [])
        for entry in index.get("entries", []):
            sample = entry.get("sample") or {}
            sample_id = str(sample.get("sample_id"))
            matching = [
                kind
                for kind, info in (sample.get("files") or {}).items()
                if isinstance(info, dict)
                and str(info.get("id")) == dataset_id
                and info.get("sha256") == sha256
            ]
            if not matching or not any(
                isinstance(i, list)
                and len(i) == 3
                and str(i[0]) == sample_id
                and i[1] in matching
                and i[2] == sha256
                for i in inputs
            ):
                continue
            with read_run_file(output, "summary.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                row = next(
                    (r for r in csv.DictReader(stream) if r.get("sample_id") == sample_id), None
                )
            if row is None:
                continue
            steps = params.get("executed_steps")
            if not isinstance(steps, list):
                fields = {
                    "rms": "mean_frame_rms",
                    "pitch": "pitch_mean_hz",
                    "intensity": "intensity_energy_mean_db",
                    "formants": "f1_mean_hz",
                    "speech_rate": "speech_rate_per_second",
                }
                steps = [
                    step for step, field in fields.items() if number(row.get(field)) is not None
                ]
            speech = entry.get("speech_rate") or {}
            return {
                **measurement_range(entry, params),
                "executed_steps": steps,
                "speech_count": number(row.get("speech_count")),
                "speech_count_unit": speech.get("unit") or row.get("speech_count_unit"),
                "speech_count_source": speech.get("count_source"),
                "speech_rate_per_second": number(row.get("speech_rate_per_second")),
                "mean_frame_rms": number(row.get("mean_frame_rms")),
                "pitch_mean_hz": number(row.get("pitch_mean_hz")),
                "intensity_energy_mean_db": number(row.get("intensity_energy_mean_db")),
            }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


def material_brief(facts):
    duration, start, end = (
        facts.get(k)
        for k in ("source_duration_seconds", "measurement_start_seconds", "measurement_end_seconds")
    )
    lines = []
    if facts.get("coverage") == "full" and duration is not None:
        lines.append(f"**整段录音** · {duration:.3f} 秒")
    elif start is not None and end is not None:
        lines.append(f"**测量选段** · {start:.3f}–{end:.3f} 秒")
    for key, label, unit in (
        ("pitch_mean_hz", "平均基频", "Hz"),
        ("intensity_energy_mean_db", "Praat 平均强度", "dB"),
        ("mean_frame_rms", "平均帧 RMS", ""),
    ):
        if facts.get(key) is not None:
            lines.append(f"- {label}　{facts[key]:.5g} {unit}".rstrip())
    if facts.get("speech_rate_per_second") is not None:
        unit = {"english_words": "英文词", "chinese_characters": "汉字"}.get(
            facts.get("speech_count_unit"), "计数单位"
        )
        count = facts.get("speech_count")
        suffix = f"（{count:g} {unit}）" if count is not None else ""
        lines.append(f"- 语速　{facts['speech_rate_per_second']:.4g} {unit}/秒{suffix}")
    if facts.get("executed_steps"):
        lines.append("\n本次未计算停顿。")
    return "\n".join(lines)
