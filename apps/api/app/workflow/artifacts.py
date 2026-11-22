"""Real portable workflow outputs from aggregate results and explicit local rows."""

from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class RenderedArtifact:
    filename: str
    media_type: str
    content: bytes
    includes_raw_rows: bool = False

    def __post_init__(self) -> None:
        if not self.content or len(self.content) > MAX_OUTPUT_BYTES:
            raise ValueError("WORKFLOW_OUTPUT_LIMIT")


def json_bytes(value: Any) -> bytes:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("WORKFLOW_JSON_LIMIT")
    return data


def aggregate_result(result: dict) -> dict:
    """Only known aggregate contracts; no fitted values, row arrays or storage keys."""
    allowed = {
        "status",
        "method",
        "method_version",
        "sample",
        "estimates",
        "statistic",
        "p_value",
        "alternative",
        "confidence_level",
        "confidence_interval",
        "effect_size",
        "descriptive_statistics",
        "assumption_checks",
        "warnings",
        "limitations",
        "software_versions",
        "input_config",
        "task_type",
        "models",
        "split",
        "comparison",
        "leakage_checks",
        "n_observations",
        "evaluation",
        "interpretation",
        "source",
    }
    return {key: value for key, value in result.items() if key in allowed}


def scalar_rows(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows = []
    if isinstance(value, dict):
        for key, item in value.items():
            rows.extend(scalar_rows(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(scalar_rows(item, f"{prefix}[{index}]"))
    elif value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("WORKFLOW_NONFINITE_OUTPUT")
        rows.append((prefix, value))
    if len(rows) > 50000:
        raise ValueError("WORKFLOW_TABLE_LIMIT")
    return rows


def _csv(rows: list[list[Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    # Keep literal values in a spreadsheet instead of executing formula strings.
    writer.writerows(
        [
            "'" + item if isinstance(item, str) and item.startswith(("=", "+", "-", "@")) else item
            for item in row
        ]
        for row in rows
    )
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def render_table(value: dict | pl.DataFrame, data: dict) -> RenderedArtifact:
    limit = data.get("row_limit", 1000)
    raw = isinstance(value, pl.DataFrame) and data.get("include_raw", False)
    if isinstance(value, pl.DataFrame):
        if raw:
            rows = [value.columns, *[list(row) for row in value.head(limit).iter_rows()]]
        else:
            rows = [["column", "dtype", "n", "missing", "mean", "std", "min", "max"]]
            for column in value.columns:
                series = value[column]
                numeric = series.dtype.is_numeric()
                rows.append(
                    [
                        column,
                        str(series.dtype),
                        len(series),
                        series.null_count(),
                        series.mean() if numeric else None,
                        series.std() if numeric else None,
                        series.min() if numeric else None,
                        series.max() if numeric else None,
                    ]
                )
    else:
        rows = [
            ["field", "value"],
            *[list(row) for row in scalar_rows(aggregate_result(value))[:limit]],
        ]
    return RenderedArtifact("table.csv", "text/csv; charset=utf-8", _csv(rows), raw)


def _model(result: dict) -> dict:
    models = result.get("models", [])
    completed = [model for model in models if model.get("status") == "completed"]
    if len(completed) != 1:
        raise ValueError("WORKFLOW_CHART_NEEDS_SINGLE_MODEL")
    return completed[0]


def chart_series(result: dict, kind: str) -> tuple[str, Sequence[tuple[str | float, float]], bool]:
    models = result.get("models")
    if kind == "auto":
        kind = "coefficient" if result.get("interpretation") else "bar"
    if kind == "coefficient":
        interpretation = result.get("interpretation") or (
            _model(result).get("interpretation") if models else None
        )
        features = (interpretation or {}).get("features", {})
        if not features:
            raise ValueError("WORKFLOW_COEFFICIENT_UNAVAILABLE")
        return kind, [(key, float(value)) for key, value in features.items()], False
    if kind in {"roc", "pr", "calibration", "residuals"}:
        diagnostics_key = (
            "validation_prediction_diagnostics"
            if result.get("evaluation", {}).get("partition") == "validation"
            else "prediction_diagnostics"
        )
        diagnostics = _model(result).get(diagnostics_key) or {}
        if kind == "residuals":
            histogram = diagnostics.get("regression", {}).get("histogram", {})
            edges, counts = histogram.get("edges", []), histogram.get("counts", [])
            if len(edges) != len(counts) + 1 or not counts:
                raise ValueError("WORKFLOW_RESIDUALS_UNAVAILABLE")
            return kind, [(float(edges[i]), float(n)) for i, n in enumerate(counts)], False
        curve = diagnostics.get(kind, {})
        if curve.get("status") != "available":
            raise ValueError("WORKFLOW_CURVE_UNAVAILABLE")
        if kind == "calibration":
            points = [
                (float(b["mean_predicted_probability"]), float(b["positive_fraction"]))
                for b in curve.get("bins", [])
                if b.get("n", 0) > 0
            ]
        else:
            xkey, ykey = ("fpr", "tpr") if kind == "roc" else ("recall", "precision")
            xs, ys = curve.get(xkey, []), curve.get(ykey, [])
            if len(xs) != len(ys):
                raise ValueError("WORKFLOW_CURVE_INVALID")
            points = [(float(x), float(y)) for x, y in zip(xs, ys)]
        if not points:
            raise ValueError("WORKFLOW_CURVE_UNAVAILABLE")
        return kind, points, True
    if kind != "bar":
        raise ValueError("WORKFLOW_CHART_UNSUPPORTED")
    if result.get("evaluation"):
        metrics = result["evaluation"].get("metrics", {})
    elif models:
        metrics = _model(result).get("test_metrics", {})
    else:
        metrics = dict(
            scalar_rows(
                {
                    key: result.get(key)
                    for key in ("statistic", "estimates", "descriptive_statistics", "effect_size")
                }
            )
        )
    points = [
        (key, float(value))
        for key, value in metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not points:
        raise ValueError("WORKFLOW_CHART_NO_NUMERIC_AGGREGATES")
    return kind, points[:64], False


def render_chart(result: dict, data: dict, provenance: dict) -> RenderedArtifact:
    kind, points, curve = chart_series(result, data.get("chart_type", "auto"))
    if any(not math.isfinite(y) or (curve and not math.isfinite(float(x))) for x, y in points):
        raise ValueError("WORKFLOW_NONFINITE_OUTPUT")
    width, height, left, bottom = 960, 600, 120, 490
    low, high = min(0.0, min(y for _, y in points)), max(0.0, max(y for _, y in points))
    span = high - low or 1.0

    def ycoord(y: float) -> float:
        return bottom - (y - low) / span * 370

    content = [
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0
{width} {height}" role="img" aria-label="{kind}">''',
        '<rect width="960" height="600" fill="white"/>',
        f"<metadata>{html.escape(json_bytes(provenance).decode())}</metadata>",
        f"""<text x="120" y="55" font-family="sans-serif"
font-size="26">{html.escape(kind.title())}</text>""",
        '<g font-family="sans-serif" font-size="15" fill="#192d26">',
    ]
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        val = low + span * fraction
        y = ycoord(val)
        content.extend(
            [
                f'<line x1="120" x2="920" y1="{y:.3f}" y2="{y:.3f}" stroke="#e2e8e4"/>',
                f'<text x="105" y="{y + 5:.3f}" text-anchor="end">{val:.4g}</text>',
            ]
        )
    if curve:
        xmin, xmax = min(float(x) for x, _ in points), max(float(x) for x, _ in points)
        coords = " ".join(
            f"{left + (float(x) - xmin) / (xmax - xmin or 1) * 800:.3f},{ycoord(y):.3f}"
            for x, y in points
        )
        content.append(
            f'<polyline points="{coords}" fill="none" stroke="#21604c" stroke-width="3"/>'
        )
        content.append(
            f"""<text x="120" y="535">{xmin:.4g}</text>
<text x="920" y="535" text-anchor="end">{xmax:.4g}</text>"""
        )
    else:
        step = 800 / len(points)
        zero = ycoord(0)
        for index, (label, value) in enumerate(points):
            x, y = left + index * step + step * 0.15, ycoord(value)
            content.extend(
                [
                    f'''<rect x="{x:.3f}" y="{min(y, zero):.3f}" width="{step * 0.7:.3f}"
height="{max(abs(zero - y), 0.3):.3f}" fill="#21604c"/>''',
                    f'''<text x="{x:.3f}" y="535" transform="rotate(-25 {x:.3f}
535)">{html.escape(str(label)[:28])}</text>''',
                ]
            )
    content.append("</g></svg>")
    return RenderedArtifact("chart.svg", "image/svg+xml", "".join(content).encode("utf-8"))


def render_report(result: dict, data: dict, provenance: dict) -> RenderedArtifact:
    title = data.get("title") or "Workflow research result"
    rows = scalar_rows(aggregate_result(result))
    body = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in rows
    )
    document = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title>
<style>body{{font:16px/1.6 system-ui;
max-width:1000px;
margin:48px auto;
padding:0 24px;
color:#192d26}}
table{{border-collapse:collapse;
width:100%}}
th,td{{padding:10px;
border-bottom:1px solid #dbe3de;
text-align:left;
overflow-wrap:anywhere}}
th{{font-weight:500}}
pre{{white-space:pre-wrap}}
</style>
<h1>{html.escape(title)}</h1>
<p>Saved deterministic result. Aggregate output; no source rows are included.</p>
<table>{body}</table>
<h2>Provenance</h2>
<pre>{html.escape(json_bytes(provenance).decode())}</pre>
</html>"""
    return RenderedArtifact("report.html", "text/html; charset=utf-8", document.encode("utf-8"))


def render_notebook(result: dict, data: dict, provenance: dict) -> RenderedArtifact:
    source = result.get("source", {})
    config = result.get("input_config")
    if not config or not source.get("dataset_sha256"):
        raise ValueError("WORKFLOW_NOTEBOOK_REPLAY_SOURCE_MISSING")
    config_literal = repr(json.dumps(config, ensure_ascii=False, allow_nan=False))
    node_type = source.get("node_type")
    code = [
        "from pathlib import Path\nimport hashlib, json\n",
        "from app.services.profiling_service import load_dataframe\n",
        'DATASET_PATH = Path("input.' + source.get("file_format", "csv") + '")\n',
        f"EXPECTED_SHA = {source['dataset_sha256']!r}\n",
        "actual_sha = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()\n",
        'assert actual_sha == EXPECTED_SHA, "Input SHA changed"\n',
        f"config = json.loads({config_literal})\n",
    ]
    if source.get("ml_run_id"):
        code.extend(
            [
                "from app.ml.worker_control import run_ml_worker\n",
                "output = await run_ml_worker(str(DATASET_PATH.resolve()), "
                "DATASET_PATH.suffix[1:], config, EXPECTED_SHA)\n",
                'result = output["result"]\nresult\n',
            ]
        )
    else:
        params = result.get("source", {}).get("node_parameters")
        if not params or not node_type:
            raise ValueError("WORKFLOW_NOTEBOOK_REPLAY_CONFIG_MISSING")
        code.extend(
            [
                "from app.workflow.executor import _run_stat_node\n",
                f"parameters = json.loads({json.dumps(params, ensure_ascii=False)!r})\n",
                "frame = load_dataframe(str(DATASET_PATH), DATASET_PATH.suffix[1:])\n",
                f"result = _run_stat_node({node_type!r}, frame, parameters)\nresult\n",
            ]
        )
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "statforge": provenance,
        },
        "cells": [
            {
                "id": "instructions",
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    f"# {data.get('title') or 'Workflow replay'}\n",
                    "Supply the exact derived input file shown in provenance and the recorded "
                    "StatForge/scientific versions. This notebook was exported without "
                    "execution or embedded data. Inspect code before running locally.\n",
                ],
            },
            {
                "id": "replay",
                "cell_type": "code",
                "metadata": {},
                "source": code,
                "outputs": [],
                "execution_count": None,
            },
        ],
    }
    return RenderedArtifact("replay.ipynb", "application/x-ipynb+json", json_bytes(notebook))


def render_export(files: list[RenderedArtifact], data: dict, provenance: dict) -> RenderedArtifact:
    if not files:
        raise ValueError("WORKFLOW_EXPORT_NO_ARTIFACTS")
    fmt = data.get("format", "zip")
    raw = any(file.includes_raw_rows for file in files)
    if fmt == "json":
        return RenderedArtifact(
            "workflow.json",
            "application/json",
            json_bytes(
                {
                    "provenance": provenance,
                    "includes_raw_rows": raw,
                    "files": [
                        {
                            "filename": file.filename,
                            "media_type": file.media_type,
                            "content_utf8": file.content.decode("utf-8"),
                        }
                        for file in files
                        if file.media_type != "application/zip"
                    ],
                }
            ),
            raw,
        )
    if fmt != "zip":
        media = {
            "html": "text/html",
            "csv": "text/csv",
            "svg": "image/svg+xml",
            "ipynb": "application/x-ipynb+json",
            "json": "application/json",
        }[fmt]
        matches = [file for file in files if file.media_type.startswith(media)]
        if len(matches) != 1:
            raise ValueError("WORKFLOW_EXPORT_FORMAT_MISMATCH")
        return matches[0]
    total = sum(len(file.content) for file in files)
    if total > MAX_OUTPUT_BYTES - MAX_JSON_BYTES:
        raise ValueError("WORKFLOW_OUTPUT_LIMIT")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
        names = set()
        for index, file in enumerate(files, 1):
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", file.filename)
            name = f"{index:02d}-{safe}"
            if name in names:
                raise ValueError("WORKFLOW_EXPORT_DUPLICATE")
            names.add(name)
            bundle.writestr(name, file.content)
        bundle.writestr(
            "manifest.json",
            json_bytes(
                {
                    **provenance,
                    "includes_raw_rows": raw,
                    "files": [
                        {
                            "filename": file.filename,
                            "media_type": file.media_type,
                            "bytes": len(file.content),
                        }
                        for file in files
                    ],
                }
            ),
        )
    return RenderedArtifact("workflow.zip", "application/zip", stream.getvalue(), raw)
