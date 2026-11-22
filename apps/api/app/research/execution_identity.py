"""Fixed host/process observations for owned execution; no signals or enumeration."""

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.research.credential_liveness import process_identity

logger = logging.getLogger("statforge.research.execution")


def host_identity() -> str:
    if os.name == "nt":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, kind = winreg.QueryValueEx(key, "MachineGuid")
        if kind != winreg.REG_SZ:
            raise RuntimeError("Cannot verify this execution host.")
        identity = str(uuid.UUID(value))
    elif os.name == "posix":
        identity = Path("/etc/machine-id").read_text(encoding="ascii").strip()
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise RuntimeError("Cannot verify this execution host.")
    else:
        raise RuntimeError("Execution host observation is unsupported.")
    return hashlib.sha256(("statforge-execution-host-v1\0" + identity).encode()).hexdigest()


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    host: str
    pid: int
    birth: str

    @classmethod
    def current(cls) -> "WorkerIdentity":
        pid = os.getpid()
        status, birth = process_identity(pid)
        if status != "alive" or birth is None:
            raise RuntimeError("Cannot verify this execution producer.")
        return cls(uuid.uuid4().hex, host_identity(), pid, birth)

    def validate_current(self) -> bool:
        try:
            status, observed = process_identity(os.getpid())
            return (
                len(self.worker_id) == 32
                and uuid.UUID(self.worker_id).hex == self.worker_id
                and self.host == host_identity()
                and self.pid == os.getpid()
                and status == "alive"
                and self.birth == observed
            )
        except (OSError, ValueError, RuntimeError):
            logger.warning("EXECUTION_IDENTITY_UNKNOWN: current producer was not verified.")
            return False


def producer_dead(host: str, pid: int, birth: str) -> bool:
    try:
        current_host = host_identity()
    except (OSError, ValueError, RuntimeError):
        logger.warning("EXECUTION_IDENTITY_UNKNOWN: host probe unavailable; lease retained.")
        return False
    if (
        not isinstance(birth, str)
        or not re.fullmatch(r"win:[1-9][0-9]*|linux:[0-9a-f-]{36}:[0-9]+", birth)
        or host != current_host
    ):
        return False
    try:
        status, observed = process_identity(pid)
    except (OSError, ValueError, RuntimeError):
        logger.warning("EXECUTION_IDENTITY_UNKNOWN: process probe unavailable; lease retained.")
        return False
    return status == "dead" or (status == "alive" and observed is not None and observed != birth)
