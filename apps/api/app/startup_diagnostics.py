"""Bounded, content-free startup phases, available before application imports."""

import json
import os
import threading
import time
from pathlib import Path

_PHASES = frozenset(
    {
        "python_entry",
        "import_main",
        "main_imports",
        "main_imported",
        "lifespan_enter",
        "database_enter",
        "database_ready",
        "sqlite_prepare",
        "fresh_create",
        "fresh_created",
        "fresh_stamp",
        "fresh_stamped",
        "fresh_synced",
        "fresh_recognized",
        "fresh_receipt",
        "publish_enter",
        "publish_renamed",
        "published_check",
        "native_snapshot_enter",
        "native_snapshot_ready",
        "published_recognized",
        "sqlite_ready",
        "desktop_user_enter",
        "desktop_user_ready",
        "storage_ready",
        "supervisors_enter",
        "supervisors_ready",
        "lifespan_ready",
    }
)


class StartupTrace:
    """Never log arguments, exception text, paths, environment or business values."""

    def __init__(self, directory: str | None):
        self.started = time.monotonic_ns()
        self.cpu_started = time.process_time_ns()
        self.events = 0
        self.bytes = 0
        self.file = None
        self.lock = threading.Lock()
        if directory:
            try:
                path = Path(directory)
                path.mkdir(parents=True, exist_ok=True)
                self.file = (path / f"sidecar-startup-{os.getpid()}-{self.started}.jsonl").open(
                    "x", encoding="utf-8", buffering=1
                )
            except OSError:
                pass  # Diagnostics must not prevent the business startup checks.

    def note(self, phase: str) -> None:
        if phase not in _PHASES:
            return
        with self.lock:
            if self.file is None:
                return
            if self.events >= 40:
                self.close()
                return
            record = {
                "phase": phase,
                "pid": os.getpid(),
                "seq": self.events,
                "elapsed_ms": (time.monotonic_ns() - self.started) // 1_000_000,
                "cpu_ms": (time.process_time_ns() - self.cpu_started) // 1_000_000,
            }
            line = json.dumps(record, separators=(",", ":")) + "\n"
            if self.bytes + len(line.encode("utf-8")) > 8192:
                self.close()
                return
            try:
                self.file.write(line)
                self.file.flush()
                self.events += 1
                self.bytes += len(line.encode("utf-8"))
            except OSError:
                self.close()

    def close(self) -> None:
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None


_trace = StartupTrace(os.environ.get("LOG_PATH"))
note = _trace.note
