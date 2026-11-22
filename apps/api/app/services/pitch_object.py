"""Conversion between saved pitch candidates and native Praat Pitch objects.

The saved candidate matrices are never reordered. Praat stores a selection by
putting it first in a frame, so native serialization uses a temporary ordering.
Clock and domain are explicit because a frame clock alone does not determine
the original sound domain.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import parselmouth


def pitch_from_arrays(data, *, xmin, xmax, dx, x1):
    """Build a native Pitch without reading audio or mutating saved arrays."""
    times = np.asarray(data["times"], dtype=float)
    frequencies = np.asarray(data["candidate_frequency"], dtype=float)
    strengths = np.asarray(data["candidate_strength"], dtype=float)
    counts = np.asarray(data["candidate_count"])
    selected = np.asarray(data["selected"])
    intensity = np.asarray(data["frame_intensity"], dtype=float)
    ceiling = float(data["ceiling"])
    n = len(times)
    if (
        times.ndim != 1
        or n == 0
        or frequencies.ndim != 2
        or frequencies.shape != strengths.shape
        or frequencies.shape[0] != n
        or any(a.shape != (n,) for a in (counts, selected, intensity))
        or not all(np.isfinite(v) for v in (xmin, xmax, dx, x1, ceiling))
        or xmin >= xmax
        or dx <= 0
        or ceiling <= 0
        or not np.isfinite(times).all()
        or not np.isfinite(intensity).all()
        or not np.allclose(times, x1 + np.arange(n) * dx, rtol=1e-10, atol=1e-10)
        or not np.issubdtype(counts.dtype, np.integer)
        or not np.issubdtype(selected.dtype, np.integer)
        or np.any(counts < 1)
        or np.any(counts > frequencies.shape[1])
        or np.any(selected < 0)
        or np.any(selected >= counts)
    ):
        raise ValueError("Invalid saved pitch candidates or clock")
    lines = [
        'File type = "ooTextFile"',
        'Object class = "Pitch 1"',
        "",
        repr(float(xmin)),
        repr(float(xmax)),
        str(n),
        repr(float(dx)),
        repr(float(x1)),
        repr(ceiling),
        str(frequencies.shape[1]),
    ]
    for i, count in enumerate(counts):
        order = [int(selected[i]), *(j for j in range(int(count)) if j != selected[i])]
        if (
            not np.isfinite(frequencies[i, order]).all()
            or not np.isfinite(strengths[i, order]).all()
        ):
            raise ValueError("Non-finite native pitch candidate")
        lines.extend((repr(float(intensity[i])), str(int(count))))
        for j in order:
            lines.extend((repr(float(frequencies[i, j])), repr(float(strengths[i, j]))))
    # Parselmouth's native reader accepts filenames rather than streams.
    with tempfile.TemporaryDirectory(prefix="statforge-pitch-") as directory:
        path = Path(directory) / "pitch.Pitch"
        path.write_text("\n".join(lines) + "\n", encoding="ascii")
        pitch = parselmouth.read(str(path))
    if not isinstance(pitch, parselmouth.Pitch):
        raise ValueError("Native reader did not return a Pitch")
    return pitch


def read_pitch(path):
    """Read any native Praat Pitch format into the media candidate array schema."""
    pitch = parselmouth.read(str(path))
    if not isinstance(pitch, parselmouth.Pitch):
        raise ValueError("File is not a Praat Pitch")
    n, width = pitch.n_frames, pitch.max_n_candidates
    frequency = np.full((n, width), np.nan, dtype=np.float64)
    strength = np.full_like(frequency, np.nan)
    counts = np.empty(n, dtype=np.int32)
    intensity = np.empty(n, dtype=np.float64)
    for i in range(n):
        frame = pitch.get_frame(i + 1)
        counts[i] = len(frame.candidates)
        intensity[i] = frame.intensity
        for j, candidate in enumerate(frame.candidates):
            frequency[i, j], strength[i, j] = candidate.frequency, candidate.strength
    selected = np.zeros(n, dtype=np.int32)
    valid = (frequency[:, 0] > 0) & (frequency[:, 0] <= pitch.ceiling)
    hz = np.where(valid, frequency[:, 0], np.nan)
    return {
        "times": pitch.xs(),
        "frame_index": np.arange(1, n + 1, dtype=np.int32),
        "frame_intensity": intensity,
        "candidate_count": counts,
        "candidate_frequency": frequency,
        "candidate_strength": strength,
        "selected": selected,
        "original_selected": selected.copy(),
        "hz": hz,
        "original_hz": hz.copy(),
        "valid": valid,
        "original_valid": valid.copy(),
        "ceiling": np.asarray(pitch.ceiling),
        "xmin": pitch.xmin,
        "xmax": pitch.xmax,
        "dx": pitch.dx,
        "x1": pitch.x1,
    }


def reselect_pitch(
    data,
    *,
    xmin,
    xmax,
    dx,
    x1,
    silence_threshold,
    voicing_threshold,
    octave_cost,
    octave_jump_cost,
    voiced_unvoiced_cost,
    ceiling,
):
    """Run Praat's path finder and return indices into the original matrices."""
    result, _ = _reselect(
        data,
        xmin=xmin,
        xmax=xmax,
        dx=dx,
        x1=x1,
        silence_threshold=silence_threshold,
        voicing_threshold=voicing_threshold,
        octave_cost=octave_cost,
        octave_jump_cost=octave_jump_cost,
        voiced_unvoiced_cost=voiced_unvoiced_cost,
        ceiling=ceiling,
    )
    return result


def _reselect(
    data,
    *,
    xmin,
    xmax,
    dx,
    x1,
    silence_threshold,
    voicing_threshold,
    octave_cost,
    octave_jump_cost,
    voiced_unvoiced_cost,
    ceiling,
):
    pitch = pitch_from_arrays(data, xmin=xmin, xmax=xmax, dx=dx, x1=x1)
    pitch.path_finder(
        silence_threshold=silence_threshold,
        voicing_threshold=voicing_threshold,
        octave_cost=octave_cost,
        octave_jump_cost=octave_jump_cost,
        voiced_unvoiced_cost=voiced_unvoiced_cost,
        ceiling=ceiling,
        pull_formants=False,
    )
    chosen = pitch.selected_array
    frequencies = np.asarray(data["candidate_frequency"])
    strengths = np.asarray(data["candidate_strength"])
    original = np.asarray(data["selected"])
    result = np.empty(len(original), dtype=np.int32)
    for i, count in enumerate(data["candidate_count"]):
        matches = np.flatnonzero(
            (frequencies[i, : int(count)] == chosen["frequency"][i])
            & (strengths[i, : int(count)] == chosen["strength"][i])
        )
        if not len(matches):
            raise ValueError("Praat selected a candidate absent from the saved frame")
        # Equal candidate pairs are indistinguishable to Praat; preserve the old index if possible.
        result[i] = original[i] if original[i] in matches else matches[0]
    return result, float(pitch.ceiling)


def write_pitch(path, data, *, xmin, xmax, dx, x1, format):
    """Write selected-first candidates to native long, short, or binary Pitch."""
    pitch = pitch_from_arrays(data, xmin=xmin, xmax=xmax, dx=dx, x1=x1)
    writers = {
        "long": pitch.save_as_text_file,
        "short": pitch.save_as_short_text_file,
        "binary": pitch.save_as_binary_file,
    }
    if format not in writers:
        raise ValueError("Pitch format must be long, short, or binary")
    writers[format](str(path))


def _main():
    """Fixed subprocess protocol; all paths are supplied by the calling service."""
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("read", "reselect", "write"))
    parser.add_argument("request")
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    engine = {
        "engine": "Parselmouth",
        "version": parselmouth.__version__,
        "praat_version": parselmouth.PRAAT_VERSION,
    }
    if args.action == "read":
        data = read_pitch(request["input_path"])
        clock = {key: data.pop(key) for key in ("xmin", "xmax", "dx", "x1")}
        np.savez(request["output_npz"], **data)
        metadata = {**clock, "ceiling": float(data["ceiling"]), **engine}
    else:
        with np.load(request["input_npz"], allow_pickle=False) as bundle:
            prefix = request.get("prefix", "")
            data = {
                key[len(prefix) :]: bundle[key] for key in bundle.files if key.startswith(prefix)
            }
        clock = {key: request["clock"][key] for key in ("xmin", "xmax", "dx", "x1")}
        if args.action == "reselect":
            selected, actual_ceiling = _reselect(data, **clock, **request["parameters"])
            np.save(request["output_npy"], selected, allow_pickle=False)
            metadata = {**clock, "ceiling": actual_ceiling, **engine}
        else:
            write_pitch(request["output_path"], data, **clock, format=request["format"])
            metadata = {**clock, "ceiling": float(data["ceiling"]), **engine}
    Path(request["output_json"]).write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    _main()
