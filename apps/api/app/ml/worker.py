"""Private registered ML/frame operations; no provider imports or user code."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import IO, Any, cast


def _limits(memory_bytes: int, job_name: str | None = None) -> None:
    from app.ml.resources import WorkerLimits

    WorkerLimits(memory_bytes=memory_bytes)
    if os.name == "nt":
        from ctypes import wintypes

        from app.ml.resources import MLResourceError
        from app.ml.worker_control import _ExtendedLimits

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        opener = kernel.OpenJobObjectW
        opener.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        opener.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        query = kernel.QueryInformationJobObject
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query.restype = wintypes.BOOL
        limits = _ExtendedLimits()
        read = wintypes.DWORD()
        handle = opener(0x0004, False, job_name) if job_name else None
        if not handle:
            raise MLResourceError(
                "ML_RESOURCE_UNAVAILABLE", "ML worker cannot query its owned Job."
            )
        try:
            if not query(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), ctypes.byref(read)
            ):
                raise MLResourceError(
                    "ML_RESOURCE_UNAVAILABLE", "ML worker cannot verify its Job limits."
                )
            member = wintypes.BOOL()
            check = kernel.IsProcessInJob
            check.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
            check.restype = wintypes.BOOL
            current = kernel.GetCurrentProcess
            current.argtypes = []
            current.restype = wintypes.HANDLE
            if not check(current(), handle, ctypes.byref(member)) or not member.value:
                raise MLResourceError(
                    "ML_RESOURCE_UNAVAILABLE", "ML worker is not assigned to its owned Job."
                )
        finally:
            close(handle)
        flags = 0x100 | 0x200 | 0x2000 | 0x8 | 0x10
        if (
            limits.BasicLimitInformation.LimitFlags & flags != flags
            or not 0 < limits.ProcessMemoryLimit <= memory_bytes
            or not 0 < limits.JobMemoryLimit <= memory_bytes
            or limits.BasicLimitInformation.ActiveProcessLimit > 2
        ):
            raise MLResourceError(
                "ML_RESOURCE_UNAVAILABLE", "ML worker Job limits do not match the fixed policy."
            )
    elif sys.platform == "linux":
        import resource
        import signal

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
        libc = ctypes.CDLL(None, use_errno=True)
        # Registered worker dies if the API parent disappears.
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() == 1:
            raise RuntimeError("Cannot bind ML worker to its parent.")
    else:
        raise RuntimeError("No ML resource enforcement for this platform.")


def _config(data: dict[str, Any]):
    from app.ml.config import MLConfig
    from app.ml.types import MLModelId, MLTaskType, SplitStrategy

    return MLConfig(
        task_type=MLTaskType(data["task_type"]),
        target=data["target"],
        features=tuple(data.get("features", [])),
        categorical_features=tuple(data.get("categorical_features", [])),
        group_variable=data.get("group_variable"),
        time_variable=data.get("time_variable"),
        split_strategy=SplitStrategy(data.get("split_strategy", "stratified_kfold")),
        test_size=float(data.get("test_size", 0.2)),
        val_size=float(data.get("val_size", 0.15)),
        n_splits=int(data.get("n_splits", 5)),
        random_seed=int(data.get("random_seed", 42)),
        model_ids=tuple(MLModelId(value) for value in data.get("model_ids", [])),
        standardize=bool(data.get("standardize", True)),
        impute_numeric=data.get("impute_numeric", "median"),
        impute_categorical=data.get("impute_categorical", "most_frequent"),
        handle_outliers=bool(data.get("handle_outliers", False)),
        feature_selection_k=data.get("feature_selection_k"),
        model_params=data.get("model_params", {}),
    )


def _snapshot(owned: Path, request: dict[str, Any]) -> Path:
    """Read only the internally selected input under enforced process limits."""
    from app.ml.resources import MLResourceError

    fmt = request["file_format"]
    source_limit = None
    if request["operation"] == "egress_sample":
        from app.research.egress_reader import MAX_SOURCE_BYTES

        source_limit = MAX_SOURCE_BYTES
        if fmt not in {"csv", "parquet"} or request.get("artifact_target") is not None:
            raise MLResourceError("EGRESS_CONFIGURATION", "Invalid registered projection request.")
    if fmt not in (
        {"figure_state"} if request["operation"] == "plot_style" else {"csv", "xlsx", "parquet"}
    ):
        raise MLResourceError("ML_FORMAT_INVALID", "Unknown ML file format.")
    source = Path(request["source_path"])
    if not source.is_file() or (source_limit is not None and source.stat().st_size > source_limit):
        raise MLResourceError(
            "ML_FILE_LIMIT", "Input is unavailable or exceeds its fixed byte limit."
        )
    snapshot = owned / ("input." + fmt)
    size = 0
    from app.research.output_handles import opened

    with source.open("rb") as incoming, opened(snapshot, write=True, create=True) as outgoing:
        while chunk := incoming.read(1024 * 1024):
            size += len(chunk)
            if source_limit is not None and size > source_limit:
                raise MLResourceError("ML_FILE_LIMIT", "Input exceeds its fixed byte limit.")
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    return snapshot


def _execute(owned: Path, request: dict[str, Any]) -> dict[str, Any]:
    request["_phase"] = "snapshot_input"
    input_file = _snapshot(owned, request)
    if request["operation"] == "egress_sample":
        from app.research.egress_reader import project

        request["_phase"] = "registered_projection"
        return project(
            input_file,
            request["file_format"],
            request["config"],
            request["dataset_sha256"],
            request.get("csv_encoding"),
        )
    request["_phase"] = "scientific_imports"
    # Scientific imports occur only after OS limits have been verified.
    import joblib

    from app.ml.bounded_writer import MAX_ARTIFACT_BYTES, BoundedWriter
    from app.ml.executor import run_ml_experiment
    from app.ml.preflight import (
        preflight_config_columns,
        preflight_data,
        training_subset_stats,
    )
    from app.ml.resources import MLResourceError, validate_frame_resources
    from app.services.profiling_service import load_dataframe

    fmt = request["file_format"]
    if fmt not in (
        {"figure_state"} if request["operation"] == "plot_style" else {"csv", "xlsx", "parquet"}
    ):
        raise MLResourceError("ML_FORMAT_INVALID", "Unknown ML file format.")
    digest = hashlib.sha256()
    with input_file.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    sha = digest.hexdigest()
    if sha != request["dataset_sha256"]:
        raise MLResourceError(
            "ML_INPUT_CHANGED", "ML input no longer matches the saved dataset SHA."
        )
    if request["operation"] in {"research_quality", "research_statistics", "manual_statistics"}:
        from app.research.domain_compute import compute

        request["_phase"] = "registered_domain_compute"
        return {
            "version": 1,
            "status": "ok",
            "result": compute(
                input_file,
                fmt,
                sha,
                request["operation"],
                request["config"],
                request.get("sheet_name"),
                request.get("csv_encoding"),
            ),
        }
    if request["operation"] == "research_workflow":
        from app.research.workflow_compute import compute as compute_workflow

        request["_phase"] = "registered_workflow_compute"
        return {"version": 1, "status": "ok", "result": compute_workflow(owned, request)}
    if request["operation"] == "plot_data":
        from app.plotting.worker import prepare_data

        request["_phase"] = "registered_plot_data"
        return prepare_data(owned, input_file, request, sha)
    if request["operation"] == "plot_style":
        from app.plotting.worker import style_compute

        request["_phase"] = "registered_plot_style"
        return style_compute(owned, input_file, request, sha)
    if request["operation"] == "plot_render":
        from app.plotting.worker import compute as compute_plot

        request["_phase"] = "registered_plot_render"
        return compute_plot(owned, input_file, request, sha)
    config = _config(request["config"])
    config = replace(config, features=config.merged_features())
    request["_phase"] = "parse_input"
    df = load_dataframe(
        str(input_file), fmt, request.get("sheet_name"), request.get("csv_encoding")
    )
    validate_frame_resources(df, config)
    if request["operation"] == "frame":
        # Compressed input parsing remains under the same OS memory/deadline
        # limits. Only bounded, uncompressed, data-only Arrow reaches the API.
        if df.estimated_size() > MAX_ARTIFACT_BYTES:
            raise MLResourceError("ML_INPUT_LIMIT", "Workflow frame exceeds 128 MiB.")
        frame_file = owned / "frame.arrow"
        with BoundedWriter(frame_file, MAX_ARTIFACT_BYTES) as sink:
            df.write_ipc(cast(IO[bytes], sink), compression="uncompressed")
        frame_sha = hashlib.sha256(frame_file.read_bytes()).hexdigest()
        return {
            "version": 1,
            "status": "ok",
            "frame_meta": {
                "bytes": frame_file.stat().st_size,
                "sha256": frame_sha,
                "rows": df.height,
                "columns": df.columns,
            },
        }
    preflight_config_columns(config, set(df.columns))
    stats = training_subset_stats(df, config, dataset_sha256=sha)
    if request["operation"] == "stats":
        return {"version": 1, "status": "ok", "stats": asdict(stats)}
    stats = preflight_data(config, df, stats)
    if request["operation"] == "preflight":
        return {"version": 1, "status": "ok", "stats": asdict(stats)}
    if request["operation"] == "partition":
        from app.ml.allocation import allocate_evaluation
        from app.ml.executor import _prepare_xy, _software_versions
        from app.ml.research_protocol import build_evaluation_protocol

        X, y, _, kept = _prepare_xy(df, config)
        allocation = allocate_evaluation(df, config, X, y, kept)
        protocol = build_evaluation_protocol(
            config,
            dataset_sha256=sha,
            row_index=kept,
            train_idx=allocation.train,
            validation_idx=allocation.validation,
            test_idx=allocation.test,
            cv_splits=allocation.cv_folds,
            software_versions=_software_versions(),
        )
        return {
            "version": 1,
            "status": "ok",
            "partition": {"rows": allocation.original_rows(kept), "protocol": protocol},
        }
    if request["operation"] != "execute":
        raise MLResourceError("ML_OPERATION_INVALID", "Unknown registered ML operation.")
    request["_phase"] = "fit_evaluate"
    result, fitted = run_ml_experiment(df, config, dataset_sha256=sha)
    if result.get("status") == "completed":
        completed = {
            m["model_id"] for m in result.get("models", []) if m.get("status") == "completed"
        }
        if not fitted or set(fitted) != completed:
            raise MLResourceError(
                "ML_ARTIFACT_INVALID", "ML fitted models do not match completed results."
            )
        # A local API request stages to its newly generated MLRun object key.
        # It is inaccessible via download until the DB publication commits.
        target = Path(request["artifact_target"]) if request.get("artifact_target") else None
        artifact = Path(str(target) + ".pending") if target else owned / "model.joblib"
        if target:
            if target.exists() or artifact.exists():
                raise MLResourceError("ML_ARTIFACT_INVALID", "ML staging object already exists.")
            target.parent.mkdir(parents=True, exist_ok=True)
        request["_phase"] = "serialize_artifact"
        with BoundedWriter(
            artifact, MAX_ARTIFACT_BYTES, guarded=request.get("intent_hash") is not None
        ) as sink:
            joblib.dump(
                {
                    "pipelines": fitted,
                    "config": config.to_dict(),
                    "dataset_sha256": sha,
                    "evaluation_protocol": result.get("split", {}).get("evaluation_protocol"),
                    "software_versions": result.get("software_versions", {}),
                    "features": result.get("features", []),
                    "labels": result.get("labels", []),
                },
                sink,
            )
        if artifact.stat().st_size > MAX_ARTIFACT_BYTES:
            artifact.unlink()
            raise MLResourceError("ML_OUTPUT_LIMIT", "ML artifact exceeds 128 MiB.")
        artifact_sha = hashlib.sha256()
        with artifact.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                artifact_sha.update(chunk)
        metadata = {
            "bytes": artifact.stat().st_size,
            "sha256": artifact_sha.hexdigest(),
            "evaluation_protocol_id": result["split"]["evaluation_protocol"]["protocol_id"],
            "models": sorted(completed),
        }
        if target:
            artifact.replace(target)
        return {"version": 1, "status": "ok", "result": result, "artifact_meta": metadata}
    return {"version": 1, "status": "ok", "result": result}


def _main_owned(owned: Path) -> None:
    request_path = owned / "request.json"
    if not owned.is_dir() or request_path.stat().st_size > 256 * 1024:
        raise SystemExit("Invalid ML worker request directory.")
    response: dict[str, Any]
    request: dict[str, Any] = {}
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or request.get("version") != 1:
            raise ValueError("Invalid ML worker request version.")
        _limits(int(request["memory_bytes"]), request.get("job_name"))
        if request.get("intent_hash") is not None:
            from app.research.produced import launch

            launch(owned, request)
        response = _execute(owned, request)
        if response.get("status") == "ok":
            import pyarrow as pa

            # Operational aggregates only: no input rows/paths or credentials.
            native_limit = request["memory_bytes"]
            if sys.platform == "linux":
                import resource

                native_limit = resource.getrlimit(resource.RLIMIT_AS)[0]
            response["runtime_limits"] = {
                "platform": sys.platform,
                "memory_bytes": native_limit,
                "arrow_memory_backend": pa.default_memory_pool().backend_name,
                "pyarrow": pa.__version__,
            }
    except MemoryError:
        response = {
            "version": 1,
            "status": "error",
            "error_code": "ML_MEMORY_LIMIT",
            "message": "ML worker exceeded its memory budget during "
            + str(request.get("_phase", "bootstrap"))
            + ".",
        }
    except Exception as exc:
        # No raw input or exception repr is sent back; codes are stable.
        response = {
            "version": 1,
            "status": "error",
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", getattr(exc, "error_code", "ML_WORKER_FAILED")),
            "message": str(exc)[:1000]
            if hasattr(exc, "code") or hasattr(exc, "error_code")
            else "ML worker could not complete the registered operation.",
        }
    from app.ml.bounded_writer import MAX_RESULT_BYTES, BoundedWriter
    from app.ml.resources import MLResourceError

    temporary = owned / "result.pending"
    result_limit = MAX_RESULT_BYTES
    if request.get("operation") == "egress_sample":
        from app.research.egress_reader import MAX_WIRE_BYTES

        result_limit = MAX_WIRE_BYTES
    try:
        with BoundedWriter(
            temporary, result_limit, guarded=request.get("intent_hash") is not None
        ) as sink:
            for chunk in json.JSONEncoder(allow_nan=False, ensure_ascii=False).iterencode(response):
                sink.write(chunk.encode("utf-8"))
    except MLResourceError:
        temporary.unlink()
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "error",
                    "error_code": "ML_OUTPUT_LIMIT",
                    "message": "ML result exceeds its fixed byte limit.",
                }
            ),
            encoding="utf-8",
        )
    temporary.replace(owned / "result.json")
    if request.get("intent_hash") is not None:
        from app.research.produced import seal

        seal(owned, request)


def main(directory: str) -> None:
    from app.research.output_handles import directories, lexical

    owned = lexical(Path(directory))
    with directories(owned) as handle:
        if os.name == "posix":
            # All subsequent relative operations remain rooted at the held
            # directory even if its original pathname is replaced externally.
            getattr(os, "fchdir")(handle)
            owned = Path(".")
        _main_owned(owned)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("ML worker accepts one private request directory.")
    main(sys.argv[1])
