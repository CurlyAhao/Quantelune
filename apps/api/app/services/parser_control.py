"""Run fixed document parsers in a bounded, disposable process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.ml.worker_control import WindowsJob, worker_environment

MEMORY_BYTES = 2 * 1024 * 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024


def run(operation: str, source: str, *, timeout: float, options: dict | None = None) -> dict:
    # No untrusted parsing occurs until the child is attached to its Windows Job.
    with tempfile.TemporaryDirectory(prefix="sf-parser-") as directory:
        root = Path(directory)
        request = root / "request.json"
        result = root / "result.json"
        request.write_text(
            json.dumps(
                {
                    "operation": operation,
                    "source": str(Path(source).resolve()),
                    "result": str(result),
                    "options": options or {},
                }
            ),
            encoding="utf-8",
        )
        env = worker_environment(root)
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--parser-worker", str(request)]
        else:
            command = [
                sys.executable,
                "-I",
                str(Path(__file__).parents[1] / "parser_worker.py"),
                str(request),
            ]
        job = WindowsJob(MEMORY_BYTES) if os.name == "nt" else None
        process = None
        try:
            process = subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000004 if job else 0,
                start_new_session=os.name != "nt",
            )
            if job:
                job.attach_and_resume(process.pid)
            process.wait(timeout=timeout)
            if process.returncode != 0 or result.stat().st_size > MAX_RESULT_BYTES:
                raise ValueError("parser worker failed")
            value = json.loads(result.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("invalid parser result")
            return value
        finally:
            try:
                if job:
                    job.close()
                elif process and process.poll() is None:
                    import signal

                    os.killpg(process.pid, signal.SIGKILL)
            finally:
                if process:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
