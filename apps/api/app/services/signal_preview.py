"""Display-only peak sampling shared by the two local signal readers."""

import numpy as np


def track(identity, name, times, values, start, end, rate=None):
    times, values = np.asarray(times), np.asarray(values)
    positions = np.flatnonzero((times >= start) & (times <= end))
    indices = []
    for bucket in np.array_split(positions, min(700, len(positions))) if len(positions) else []:
        finite = bucket[np.isfinite(values[bucket])]
        picks = [bucket[0], bucket[-1]]
        if len(finite):
            picks += [finite[np.argmin(values[finite])], finite[np.argmax(values[finite])]]
        indices.extend(sorted(set(picks)))
    points, previous = [], None
    step = 1 / rate if rate else None
    for i in indices:
        if previous is not None and (
            (step and times[i] - times[previous] > (i - previous + 2) * step)
            or not np.isfinite(values[previous : i + 1]).all()
        ):
            points.append([float(times[i]), None])
        points.append([float(times[i]), float(values[i]) if np.isfinite(values[i]) else None])
        previous = i
    return {
        "id": identity,
        "name": name,
        "rate": rate,
        "samples": len(positions),
        "missing": int((~np.isfinite(values[positions])).sum()),
        "points": points,
    }
