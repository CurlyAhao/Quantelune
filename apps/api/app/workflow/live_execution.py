"""Bounded, owner-filtered live observations; never a durable business checkpoint."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class _Worker:
    process: subprocess.Popen[bytes]
    operation: str
    dataset_sha256: str
    started_at: str


@dataclass
class _Execution:
    run_id: str
    workflow_id: str
    workflow_version: int
    project_id: str
    owner_id: str
    dataset_id: str
    dataset_sha256: str
    started_at: str
    workers: dict[int, _Worker] = field(default_factory=dict)


_lock = threading.RLock()
_executions: dict[str, _Execution] = {}
_current: ContextVar[str | None] = ContextVar("workflow_live_execution", default=None)
MAX_OBSERVATIONS = 16


@contextmanager
def observe_execution(
    *,
    run_id: str,
    workflow_id: str,
    workflow_version: int,
    project_id: str,
    owner_id: str,
    dataset_id: str,
    dataset_sha256: str,
) -> Iterator[None]:
    entry = _Execution(
        run_id,
        workflow_id,
        workflow_version,
        project_id,
        owner_id,
        dataset_id,
        dataset_sha256,
        datetime.now(UTC).isoformat(),
    )
    with _lock:
        admitted = len(_executions) < MAX_OBSERVATIONS and run_id not in _executions
        if admitted:
            _executions[run_id] = entry
    token = _current.set(run_id if admitted else None)
    try:
        yield
    finally:
        _current.reset(token)
        if admitted:
            with _lock:
                _executions.pop(run_id, None)


def register_worker(
    process: subprocess.Popen[bytes],
    operation: str,
    dataset_sha256: str,
) -> tuple[str, int] | None:
    run_id = _current.get()
    with _lock:
        entry = _executions.get(run_id) if run_id else None
        if entry is None:
            return None
        entry.workers[process.pid] = _Worker(
            process,
            operation,
            dataset_sha256,
            datetime.now(UTC).isoformat(),
        )
        return entry.run_id, process.pid


def unregister_worker(binding: tuple[str, int] | None) -> None:
    if binding is not None:
        with _lock:
            entry = _executions.get(binding[0])
            if entry is not None:
                entry.workers.pop(binding[1], None)


def snapshot(
    workflow_id: str,
    owner_id: str,
    project_id: str,
    dataset_id: str,
    dataset_sha256: str,
) -> list[dict[str, Any]]:
    with _lock:
        result: list[dict[str, Any]] = []
        for entry in _executions.values():
            if (
                entry.workflow_id != workflow_id
                or entry.owner_id != owner_id
                or entry.project_id != project_id
                or entry.dataset_id != dataset_id
                or entry.dataset_sha256 != dataset_sha256
            ):
                continue
            workers = [
                {
                    "pid": worker.process.pid,
                    "operation": worker.operation,
                    "dataset_sha256": worker.dataset_sha256,
                    "started_at": worker.started_at,
                }
                for worker in entry.workers.values()
                if worker.process.poll() is None
            ]
            result.append(
                {
                    "run_id": entry.run_id,
                    "workflow_id": entry.workflow_id,
                    "workflow_version": entry.workflow_version,
                    "project_id": entry.project_id,
                    "owner_id": entry.owner_id,
                    "dataset_id": entry.dataset_id,
                    "dataset_sha256": entry.dataset_sha256,
                    "started_at": entry.started_at,
                    "sidecar_pid": os.getpid(),
                    "status": "running",
                    "workers": workers,
                    "durable": False,
                    "business_checkpoint": False,
                }
            )
        return result
