"""Fixed plotting operation for the existing OS-controlled scientific worker."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import platform
import zipfile
from pathlib import Path

from app.ml.resources import MLResourceError

FORMATS = {"svg": "image/svg+xml", "png": "image/png", "pdf": "application/pdf"}
ARTIFACT_TYPES = {
    **FORMATS,
    "renderer": "text/x-python;charset=utf-8",
    "figure_state": "application/octet-stream",
}
MAX_BUNDLE = 64 * 1024 * 1024


class DataDigest(io.RawIOBase):
    """Hash the same Parquet serialization without retaining another data copy."""

    def __init__(self):
        super().__init__()
        self.sha = hashlib.sha256()
        self.size = 0

    def writable(self):
        return True

    def write(self, content):
        self.sha.update(content)
        self.size += len(content)
        return len(content)


def compute(owned, input_file, request, source_sha):
    from app.ml.bounded_writer import BoundedWriter
    from app.plotting.program import render
    from app.services.profiling_service import load_dataframe

    config = request["config"]
    if set(config) != {"code", "columns", "dpi"}:
        raise MLResourceError("PLOT_INPUT_INVALID", "绘图配置无法读取，请重新打开草稿。")
    frame = load_dataframe(
        str(input_file),
        request["file_format"],
        request.get("sheet_name"),
        request.get("csv_encoding"),
    )
    columns = config["columns"]
    if not columns or len(set(columns)) != len(columns) or not set(columns) <= set(frame.columns):
        raise MLResourceError("PLOT_COLUMNS_CHANGED", "字段已变化，请重新选择绘图数据。")
    selected = frame.select(columns)
    if selected.estimated_size() > 128 * 1024 * 1024:
        raise MLResourceError("PLOT_INPUT_LIMIT", "所选数据过大，请减少字段或拆分数据。")
    result = render(config["code"], selected.to_pandas(), source_sha, dpi=config["dpi"])
    files = result.pop("files")
    files["renderer"] = Path(__file__).with_name("program.py").read_bytes()
    digest = DataDigest()
    selected.write_parquet(digest)
    metadata = {
        extension: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        for extension, content in files.items()
    }
    with BoundedWriter(owned / "plot.zip", MAX_BUNDLE) as sink:
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for extension, content in files.items():
                bundle.writestr("figure." + extension, content)
    return {
        "version": 1,
        "status": "ok",
        "result": {
            **result,
            "status": "completed",
            "files": metadata,
            "selected_data_sha256": digest.sha.hexdigest(),
            "selected_data_bytes": digest.size,
            "python_version": platform.python_version(),
            "software_versions": {
                name: importlib.metadata.version(name)
                for name in (
                    "matplotlib",
                    "seaborn",
                    "numpy",
                    "pandas",
                    "polars",
                    "pyarrow",
                    "pillow",
                )
            },
        },
        "artifact_meta": {
            "bytes": (owned / "plot.zip").stat().st_size,
            "sha256": hashlib.sha256((owned / "plot.zip").read_bytes()).hexdigest(),
        },
    }


def verify_bundle(response, config, source_sha):
    """Check the worker's data-only channel before any artifact is published."""
    content = response.get("artifact", b"")
    result = response.get("result", {})
    meta = response.get("artifact_meta", {})
    if (
        not content
        or len(content) > MAX_BUNDLE
        or meta.get("bytes") != len(content)
        or meta.get("sha256") != hashlib.sha256(content).hexdigest()
        or result.get("status") != "completed"
        or result.get("code_sha256") != hashlib.sha256(config["code"].encode()).hexdigest()
        or result.get("source_sha256") != source_sha
        or set(result.get("files", {})) != set(ARTIFACT_TYPES)
    ):
        raise MLResourceError("PLOT_OUTPUT_INVALID", "图形产物不完整，请重新运行。")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            if set(bundle.namelist()) != {"figure." + value for value in ARTIFACT_TYPES} or len(
                bundle.infolist()
            ) != len(ARTIFACT_TYPES):
                raise ValueError()
            files = {}
            for extension in ARTIFACT_TYPES:
                entry = bundle.getinfo("figure." + extension)
                expected = result["files"][extension]
                if entry.file_size > 20000000 or entry.file_size != expected["bytes"]:
                    raise ValueError()
                data = bundle.read(entry)
                if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                    raise ValueError()
                files[extension] = data
            if (
                not files["png"].startswith(b"\x89PNG\r\n\x1a\n")
                or not files["pdf"].startswith(b"%PDF-")
                or b"<svg" not in files["svg"]
            ):
                raise ValueError()
            return files
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile):
        raise MLResourceError("PLOT_OUTPUT_INVALID", "图形文件校验失败，请重新运行。") from None


def style_compute(owned, input_file, request, source_sha):
    """Restyle a verified native figure state. No dataframe or analysis is loaded."""
    from app.ml.bounded_writer import BoundedWriter
    from app.plotting.program import configure_export, restyle

    config = request["config"]
    if set(config) != {"code", "columns", "dpi", "style", "source_sha256", "parent_metadata"}:
        raise MLResourceError("PLOT_INPUT_INVALID", "样式配置无法读取。")
    content = input_file.read_bytes()
    if hashlib.sha256(content).hexdigest() != source_sha:
        raise MLResourceError("PLOT_INPUT_INVALID", "图表渲染状态已变化。")
    configure_export(config["code"], config["source_sha256"])
    result = restyle(content, config["style"], config["dpi"])
    files = result.pop("files")
    files["renderer"] = Path(__file__).with_name("program.py").read_bytes()
    metadata = {
        extension: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
        for extension, value in files.items()
    }
    with BoundedWriter(owned / "plot.zip", MAX_BUNDLE) as sink:
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for extension, value in files.items():
                bundle.writestr("figure." + extension, value)
    return {
        "version": 1,
        "status": "ok",
        "result": {
            **config["parent_metadata"],
            **result,
            "status": "completed",
            "files": metadata,
            "source_sha256": config["source_sha256"],
            "code_sha256": hashlib.sha256(config["code"].encode()).hexdigest(),
            "style_only": True,
            "style": config["style"],
        },
        "artifact_meta": {
            "bytes": (owned / "plot.zip").stat().st_size,
            "sha256": hashlib.sha256((owned / "plot.zip").read_bytes()).hexdigest(),
        },
    }


def prepare_data(owned, input_file, request, source_sha):
    """Materialize only selected source columns for explicit local IDE editing."""
    from app.ml.bounded_writer import BoundedWriter
    from app.services.profiling_service import load_dataframe

    config = request["config"]
    if set(config) != {"columns"}:
        raise MLResourceError("PLOT_INPUT_INVALID", "请重新选择绘图数据。")
    frame = load_dataframe(
        str(input_file),
        request["file_format"],
        request.get("sheet_name"),
        request.get("csv_encoding"),
    )
    columns = config["columns"]
    if not columns or len(set(columns)) != len(columns) or not set(columns) <= set(frame.columns):
        raise MLResourceError("PLOT_COLUMNS_CHANGED", "字段已变化，请重新选择数据。")
    selected = frame.select(columns)
    if selected.estimated_size() > 128 * 1024 * 1024:
        raise MLResourceError("PLOT_INPUT_LIMIT", "所选数据过大，请减少字段。")
    path = owned / "data.parquet"
    with BoundedWriter(path, MAX_BUNDLE) as sink:
        selected.write_parquet(sink)
    return {
        "version": 1,
        "status": "ok",
        "result": {
            "status": "completed",
            "source_sha256": source_sha,
            "columns": columns,
            "input_rows": selected.height,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }


def verify_data(response, config, source_sha):
    content, value = response.get("artifact", b""), response.get("result", {})
    if (
        not 8 <= len(content) <= MAX_BUNDLE
        or content[:4] != b"PAR1"
        or content[-4:] != b"PAR1"
        or value.get("status") != "completed"
        or value.get("source_sha256") != source_sha
        or value.get("columns") != config["columns"]
        or value.get("bytes") != len(content)
        or value.get("sha256") != hashlib.sha256(content).hexdigest()
    ):
        raise MLResourceError("PLOT_DATA_INVALID", "本地数据副本无法确认，请重新打开代码文件。")
    return content, value
