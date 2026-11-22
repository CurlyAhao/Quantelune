"""Private recorded-process identity checks; no enumeration, signals or key access."""

import ctypes
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, Literal

INSTANCE = uuid.uuid4().hex
_ACTIVE: set[str] = set()
_FLOW: ContextVar[str | None] = ContextVar("research_credential_flow", default=None)
LEASE_MS = 60000
Liveness = Literal["alive", "dead", "unknown"]


def process_identity(pid: int) -> tuple[Liveness, str | None]:
    if type(pid) is not int or not 0 < pid < 2**31:
        return "unknown", None
    if os.name == "nt":
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        opener = kernel.OpenProcess
        opener.argtypes, opener.restype = (
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
            wintypes.HANDLE,
        )
        times = kernel.GetProcessTimes
        times.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FileTime)] * 4
        times.restype = wintypes.BOOL
        close = kernel.CloseHandle
        close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
        handle = opener(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only
        if not handle:
            return ("dead", None) if ctypes.get_last_error() in {87, 1168} else ("unknown", None)
        try:
            created, exited, system, user = (FileTime() for _ in range(4))
            if not times(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(system),
                ctypes.byref(user),
            ):
                return "unknown", None
            birth = f"win:{(created.high << 32) | created.low}"
            return ("dead" if exited.high or exited.low else "alive"), birth
        finally:
            close(handle)
    if os.name == "posix" and Path("/proc").is_dir():
        try:
            # Both fixed system paths; PID comes only from the authenticated journal.
            data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
            fields = data[data.rfind(")") + 2 :].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            if fields[0] == "Z":
                return "dead", f"linux:{boot}:{fields[19]}"
            return "alive", f"linux:{boot}:{fields[19]}"
        except FileNotFoundError:
            return "dead", None
        except (OSError, IndexError, ValueError):
            return "unknown", None
    return "unknown", None


def flow(function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        identifier = uuid.uuid4().hex
        token = _FLOW.set(identifier)
        _ACTIVE.add(identifier)
        try:
            return await function(*args, **kwargs)
        finally:
            _ACTIVE.discard(identifier)
            _FLOW.reset(token)

    return wrapped


def metadata() -> dict[str, Any]:
    identifier = _FLOW.get()
    if identifier is None or identifier not in _ACTIVE:
        raise RuntimeError("Credential operation must have a tracked flow.")
    status, birth = process_identity(os.getpid())
    return {
        "producer_instance": INSTANCE + ":" + identifier,
        "producer_pid": os.getpid(),
        "producer_birth": birth if status == "alive" else None,
        "lease_until_ms": time.time_ns() // 1000000 + LEASE_MS,
    }


def safe_to_reconcile(instance: str | None, pid: int | None, birth: str | None) -> bool:
    if not instance or pid is None or not birth:
        return False  # Legacy and unknown identities stay protected.
    status, observed = process_identity(pid)
    if status == "dead":
        return True
    if status != "alive" or observed is None:
        return False
    if observed != birth:
        return True  # Old process died; reused PID is never touched.
    if pid == os.getpid() and instance.startswith(INSTANCE + ":"):
        return instance.split(":", 1)[1] not in _ACTIVE
    return False  # Another live writer stays protected even when lease expired.
