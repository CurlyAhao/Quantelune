"""OS-enforced lifecycle for the registered ML worker, with private inputs."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import inspect
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

from app.ml.resources import MLResourceError, WorkerLimits

_SLOT = threading.Lock()


class MLTaskScope:
    """One admission and absolute deadline for input, fit and publication."""

    def __init__(
        self,
        limits: WorkerLimits | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.limits = limits or WorkerLimits()
        self.deadline = time.monotonic() + self.limits.wall_seconds
        self.cancel_check = cancel_check
        self.active = False

    async def __aenter__(self) -> MLTaskScope:
        if not _SLOT.acquire(blocking=False):
            raise MLResourceError("ML_BUSY", "Another ML task is already running.")
        self.active = True
        try:
            await self.check()
        except BaseException:
            self.active = False
            _SLOT.release()
            raise
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.active = False
        _SLOT.release()

    async def check(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise MLResourceError("ML_TIMEOUT", "ML exceeded its complete task time budget.")
        if self.cancel_check is not None:
            try:
                cancelled = await asyncio.wait_for(self.cancel_check(), timeout=remaining)
            except TimeoutError as exc:
                raise MLResourceError("ML_TIMEOUT", "ML exceeded its task time budget.") from exc
            if cancelled:
                raise MLResourceError("ML_CANCELLED", "ML execution was cancelled.")
        if time.monotonic() >= self.deadline:
            raise MLResourceError("ML_TIMEOUT", "ML exceeded its complete task time budget.")

    async def wait(self, awaitable: Awaitable[Any]) -> Any:
        """Cancel actual async I/O, rather than abandon an unkillable thread."""
        try:
            await self.check()
        except BaseException:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise
        operation = asyncio.ensure_future(awaitable)
        try:
            while True:
                await self.check()
                done, _ = await asyncio.wait(
                    {operation}, timeout=min(0.025, self.deadline - time.monotonic())
                )
                if done:
                    value = operation.result()
                    await self.check()
                    return value
        except BaseException:
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await operation
            raise


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class WindowsJob:
    """Own one non-inheritable Job handle; closing it kills its entire tree."""

    def __init__(self, memory_bytes: int) -> None:
        if os.name != "nt":
            raise MLResourceError("ML_RESOURCE_UNAVAILABLE", "Windows Job is unavailable.")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "GetCurrentProcess": ([], wintypes.HANDLE),
            "GetProcessAffinityMask": (
                [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)],
                wintypes.BOOL,
            ),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "QueryInformationJobObject": (
                [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                ],
                wintypes.BOOL,
            ),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes = args
            function.restype = result
        self.name = "StatForge-ML-" + uuid.uuid4().hex
        self.handle = self.kernel.CreateJobObjectW(None, self.name)
        if not self.handle:
            self._fail()
        try:
            available, system = ctypes.c_size_t(), ctypes.c_size_t()
            if not self.kernel.GetProcessAffinityMask(
                self.kernel.GetCurrentProcess(), ctypes.byref(available), ctypes.byref(system)
            ):
                self._fail()
            limits = _ExtendedLimits()
            # One CPU, at most bootloader + worker, process and total memory,
            # and unconditional tree cleanup on last handle close.
            limits.BasicLimitInformation.LimitFlags = 0x10 | 0x8 | 0x100 | 0x200 | 0x2000
            limits.BasicLimitInformation.ActiveProcessLimit = 2
            limits.BasicLimitInformation.Affinity = available.value & -available.value
            limits.ProcessMemoryLimit = memory_bytes
            limits.JobMemoryLimit = memory_bytes
            if not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                self._fail()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _fail() -> None:
        raise MLResourceError(
            "ML_RESOURCE_UNAVAILABLE",
            "Cannot enforce the ML process limit (Windows error "
            + str(ctypes.get_last_error())
            + ").",
        )

    def attach_and_resume(self, pid: int) -> None:
        """The process was created suspended; no child can escape assignment."""
        process = self.kernel.OpenProcess(0x0100 | 0x0001 | 0x0400, False, pid)
        if not process:
            self._fail()
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                self._fail()
        finally:
            self.kernel.CloseHandle(process)
        snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            self._fail()
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            threads = []
            more = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == pid:
                    threads.append(int(entry.th32ThreadID))
                more = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if len(threads) != 1:
                self._fail()
            thread = self.kernel.OpenThread(0x0002, False, threads[0])
            if not thread:
                self._fail()
            try:
                if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                    self._fail()
            finally:
                self.kernel.CloseHandle(thread)
        finally:
            self.kernel.CloseHandle(snapshot)

    def close(self) -> None:
        if self.handle:
            try:
                self.kernel.TerminateJobObject(self.handle, 0xC000013A)
                deadline = time.monotonic() + 5
                while True:
                    accounting = _Accounting()
                    written = wintypes.DWORD()
                    if not self.kernel.QueryInformationJobObject(
                        self.handle,
                        1,
                        ctypes.byref(accounting),
                        ctypes.sizeof(accounting),
                        ctypes.byref(written),
                    ):
                        self._fail()
                    if accounting.ActiveProcesses == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise MLResourceError(
                            "ML_TERMINATION_FAILED", "ML process tree did not terminate."
                        )
                    time.sleep(0.01)
            finally:
                self.kernel.CloseHandle(self.handle)
                self.handle = None


def worker_environment(job_dir: Path) -> dict[str, str]:
    """Keep runtime essentials; do not inherit credentials, traces or user RC."""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    # A frozen worker uses the same bundled runtime; these are bootloader paths
    # and process-level markers, never provider configuration.
    if getattr(sys, "frozen", False):
        env.update({key: value for key, value in os.environ.items() if key.startswith("_PYI_")})
    env.update(
        {
            "TEMP": str(job_dir),
            "TMP": str(job_dir),
            "HOME": str(job_dir),
            "USERPROFILE": str(job_dir),
            "APPDATA": str(job_dir),
            "LOCALAPPDATA": str(job_dir),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "POLARS_MAX_THREADS": "1",
            # Arrow's default mimalloc/jemalloc pool can reserve virtual arenas
            # incompatible with Linux RLIMIT_AS even for a tiny dataframe.
            # Select its supported system allocator; never raise the hard cap.
            "ARROW_DEFAULT_MEMORY_POOL": "system",
            "MALLOC_ARENA_MAX": "1",
            "MPLCONFIGDIR": str(job_dir / "matplotlib"),
            "MPLBACKEND": "Agg",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "AI_ENABLED": "false",
            "AI_API_KEY": "",
            "AI_BASE_URL": "",
            "AI_MODEL": "",
            "STATFORGE_SOURCE_PREVIEW": "1",
        }
    )
    return env


async def run_ml_worker(
    input_path: str,
    file_format: str,
    config: dict[str, Any],
    dataset_sha256: str,
    *,
    operation: str = "execute",
    limits: WorkerLimits | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    task: MLTaskScope | None = None,
    artifact_target: Path | None = None,
    produced_directory: Path | None = None,
    workflow_sources: dict | None = None,
    intent_hash: str | None = None,
    sheet_name: str | None = None,
    csv_encoding: str | None = None,
) -> dict[str, Any]:
    """Fixed registered ML operations; no user command, path or code execution."""
    if operation == "egress_sample":
        if artifact_target is not None:
            raise MLResourceError("EGRESS_CONFIGURATION", "Projection has no artifact channel.")
        if limits is None and task is None:
            limits = WorkerLimits(wall_seconds=45, memory_bytes=512 * 1024 * 1024)
        effective = task.limits if task is not None else limits
        if (
            effective is None
            or effective.wall_seconds > 45
            or effective.memory_bytes > 512 * 1024 * 1024
        ):
            raise MLResourceError(
                "EGRESS_RESOURCE_LIMIT", "Projection cannot enlarge its fixed limits."
            )
    if task is None:
        async with MLTaskScope(limits, cancel_check) as admitted:
            return await run_ml_worker(
                input_path,
                file_format,
                config,
                dataset_sha256,
                operation=operation,
                task=admitted,
                artifact_target=artifact_target,
                produced_directory=produced_directory,
                workflow_sources=workflow_sources,
                intent_hash=intent_hash,
                sheet_name=sheet_name,
                csv_encoding=csv_encoding,
            )
    if not task.active:
        raise MLResourceError("ML_RESOURCE_UNAVAILABLE", "ML admission is not active.")
    await task.check()
    if operation not in {
        "execute",
        "preflight",
        "stats",
        "partition",
        "frame",
        "egress_sample",
        "research_quality",
        "research_statistics",
        "manual_statistics",
        "research_workflow",
        "plot_render",
        "plot_style",
        "plot_data",
    }:
        raise MLResourceError("ML_OPERATION_INVALID", "Unknown ML worker operation.")
    limits = task.limits
    file_format = file_format.lower().lstrip(".")
    if file_format not in (
        {"figure_state"} if operation == "plot_style" else {"csv", "xlsx", "parquet"}
    ):
        raise MLResourceError("ML_FORMAT_INVALID", "Unknown ML file format.")
    if operation == "egress_sample" and file_format not in {"csv", "parquet"}:
        raise MLResourceError(
            "EGRESS_FORMAT_INVALID", "Projection accepts registered CSV/Parquet only."
        )
    process: subprocess.Popen[bytes] | None = None
    job: WindowsJob | None = None
    temporary: tempfile.TemporaryDirectory[str] | None = None
    live_binding: tuple[str, int] | None = None
    directory_guard = contextlib.ExitStack()
    try:
        if produced_directory is not None:
            if (
                intent_hash is None
                or len(intent_hash) != 64
                or any(c not in "0123456789abcdef" for c in intent_hash)
                or artifact_target is not None
                or operation
                not in {"execute", "research_quality", "research_statistics", "research_workflow"}
            ):
                raise MLResourceError("ML_INTENT_INVALID", "Invalid durable worker registration.")
            from app.research.output_handles import directories, lexical

            produced_directory = lexical(produced_directory)
            directory_guard.enter_context(directories(produced_directory))
            if any(produced_directory.iterdir()):
                raise MLResourceError(
                    "ML_STAGE_EXISTS",
                    "Durable stage must be exclusively empty; recover retained result.",
                )
            directory = str(produced_directory)
        else:
            if intent_hash is not None:
                raise MLResourceError("ML_INTENT_INVALID", "Durable intent requires owned stage.")
            temporary = tempfile.TemporaryDirectory(prefix="sf_ml_worker_")
            directory = temporary.name
        # Cleanup happens after process-tree termination, including cancellation.
        if directory:
            owned = Path(directory)
            # The Job-controlled child performs the bounded snapshot read.
            # These paths are selected by trusted Dataset/storage orchestration,
            # never accepted as request fields from an API caller.
            request = {
                "version": 1,
                "operation": operation,
                "file_format": file_format,
                "config": config,
                "dataset_sha256": dataset_sha256,
                "memory_bytes": limits.memory_bytes,
                "source_path": str(Path(input_path).resolve()),
                "artifact_target": str(artifact_target) if artifact_target else None,
            }
            if sheet_name is not None:
                if (
                    file_format != "xlsx"
                    or not isinstance(sheet_name, str)
                    or not 1 <= len(sheet_name) <= 31
                ):
                    raise MLResourceError("ML_FORMAT_INVALID", "Invalid Excel worksheet.")
                request["sheet_name"] = sheet_name
            if csv_encoding is not None:
                from app.services.csv_reader import CSV_ENCODINGS

                if file_format != "csv" or csv_encoding not in CSV_ENCODINGS:
                    raise MLResourceError("ML_FORMAT_INVALID", "Invalid CSV encoding.")
                request["csv_encoding"] = csv_encoding
            if operation == "research_workflow":
                if produced_directory is None or workflow_sources is None:
                    raise MLResourceError(
                        "ML_INTENT_INVALID", "Workflow needs frozen native sources."
                    )
                request["workflow_sources"] = workflow_sources
            elif workflow_sources is not None:
                raise MLResourceError("ML_INTENT_INVALID", "Unexpected Workflow sources.")
            if produced_directory is not None:
                request["intent_hash"] = intent_hash
            # One exclusive native worker registration per stage; retry does
            # not overwrite the prior request or silently fit again.
            with (owned / "request.json").open("x", encoding="utf-8") as initial_request:
                json.dump(request, initial_request, allow_nan=False, ensure_ascii=False)
                initial_request.flush()
                os.fsync(initial_request.fileno())
            args = (
                [sys.executable, "--ml-worker", str(owned)]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "app.ml.worker", str(owned)]
            )
            await task.check()
            # Business errors use the bounded atomic JSON channel. Inheritable
            # log-file handles otherwise outlive a killed venv launcher.
            with open(os.devnull, "wb") as stdout, open(os.devnull, "wb") as stderr:
                if os.name == "nt":
                    job = WindowsJob(limits.memory_bytes)
                    request["job_name"] = job.name
                    if produced_directory is not None:
                        from app.research.output_handles import opened

                        with opened(owned / "request.json", write=True) as packet:
                            packet.seek(0)
                            packet.write(
                                json.dumps(request, allow_nan=False, ensure_ascii=False).encode()
                            )
                            packet.truncate()
                            packet.flush()
                            os.fsync(packet.fileno())
                    else:
                        (owned / "request.json").write_text(
                            json.dumps(request, allow_nan=False, ensure_ascii=False),
                            encoding="utf-8",
                        )
                await task.check()
                process = subprocess.Popen(
                    args,
                    cwd=Path(__file__).resolve().parents[2],
                    env=worker_environment(owned),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=(0x00000004 | 0x08000000) if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                if job:
                    job.attach_and_resume(process.pid)
                from app.workflow.live_execution import register_worker

                live_binding = register_worker(process, operation, dataset_sha256)
                while process.poll() is None:
                    await task.check()
                    await asyncio.sleep(0.025)
            result_path = owned / "result.json"
            await task.check()
            if process.returncode != 0 or not result_path.exists():
                raise MLResourceError(
                    "ML_WORKER_INTERRUPTED", "ML worker ended without a committed result."
                )
            result_limit = 128000 if operation == "egress_sample" else 16 * 1024 * 1024
            if result_path.stat().st_size > result_limit:
                raise MLResourceError("ML_OUTPUT_LIMIT", "ML result exceeds its bound.")
            response = json.loads(result_path.read_text(encoding="utf-8"))
            if response.get("status") == "error":
                raise MLResourceError(
                    response.get("error_code", "ML_WORKER_FAILED"),
                    response.get("message", "ML worker failed."),
                )
            if produced_directory is not None:
                from app.research.produced import verify

                assert intent_hash is not None  # Registration checked before native launch.
                _manifest, response = verify(owned, intent_hash, operation, dataset_sha256, config)
            artifact_path = owned / (
                "frame.arrow"
                if operation == "frame"
                else "plot.zip"
                if operation in {"plot_render", "plot_style"}
                else "data.parquet"
                if operation == "plot_data"
                else "model.joblib"
            )
            if operation == "egress_sample" and artifact_path.exists():
                raise MLResourceError("EGRESS_CHANNEL_INVALID", "Projection produced an artifact.")
            if artifact_path.exists() and artifact_path.stat().st_size > 128 * 1024 * 1024:
                raise MLResourceError("ML_OUTPUT_LIMIT", "ML artifact exceeds 128 MiB.")
            response["artifact"] = artifact_path.read_bytes() if artifact_path.exists() else b""
            await task.check()
            if operation == "egress_sample":
                from app.research.egress_reader import verify_projection

                if set(response) != {
                    "version",
                    "status",
                    "projection",
                    "runtime_limits",
                    "artifact",
                }:
                    raise MLResourceError("EGRESS_CHANNEL_INVALID", "Invalid projection envelope.")
                verify_projection(response, config, dataset_sha256)
            elif operation == "frame":
                import hashlib

                meta = response.get("frame_meta", {})
                if (
                    not response["artifact"]
                    or meta.get("bytes") != len(response["artifact"])
                    or meta.get("sha256") != hashlib.sha256(response["artifact"]).hexdigest()
                ):
                    raise MLResourceError(
                        "ML_ARTIFACT_INVALID", "Workflow frame channel failed verification."
                    )
            elif operation == "plot_data":
                from app.plotting.worker import verify_data

                verify_data(response, config, dataset_sha256)
            elif operation in {"plot_render", "plot_style"}:
                from app.plotting.worker import verify_bundle

                verify_bundle(
                    response,
                    config,
                    config["source_sha256"] if operation == "plot_style" else dataset_sha256,
                )
            elif response.get("result", {}).get("status") != "completed" and response["artifact"]:
                raise MLResourceError(
                    "ML_ARTIFACT_INVALID", "Failed ML worker produced an artifact."
                )
            return response
        raise MLResourceError("ML_RESOURCE_UNAVAILABLE", "ML private directory was not allocated.")
    finally:
        from app.workflow.live_execution import unregister_worker

        unregister_worker(live_binding)
        # Close the Job before the process handle: this kills descendants too.
        cleanup_error: Exception | None = None
        try:
            if job:
                try:
                    job.close()
                except Exception as exc:
                    cleanup_error = exc
            # This fallback must still execute when querying/closing the Job
            # fails. The non-inheritable KILL_ON_CLOSE handle already closes.
            if process and process.poll() is None:
                if sys.platform == "win32":
                    process.kill()
                elif sys.platform == "linux":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=5)
        finally:
            if temporary:
                # Exactly the internally allocated directory, after termination.
                try:
                    temporary.cleanup()
                except Exception as exc:
                    cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            directory_guard.close()
            raise MLResourceError(
                "ML_CLEANUP_FAILED", "ML worker cleanup did not complete."
            ) from cleanup_error
        directory_guard.close()
