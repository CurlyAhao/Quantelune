"""Fixed-output byte enforcement for trusted serializers on every platform."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from app.ml.resources import MLResourceError

MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024


def filesystem_path(path: Path) -> Path:
    """Keep an internally authorized absolute path usable beyond Win32 MAX_PATH."""
    absolute = str(path.resolve())
    if os.name == "nt" and not absolute.startswith("\\\\?\\"):
        absolute = (
            "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
        )
    return Path(absolute)


class BoundedWriter(io.RawIOBase):
    """No writable file descriptor/seek escape; reject before touching disk."""

    def __init__(self, path: Path, max_bytes: int, *, guarded: bool = False) -> None:
        super().__init__()
        if not 0 < max_bytes <= MAX_ARTIFACT_BYTES:
            raise ValueError("Invalid fixed output limit.")
        self._guard = None
        if guarded:
            from app.research.output_handles import opened

            self._guard = opened(path, write=True, create=True)
            self._file = self._guard.__enter__()
        else:
            self._file = filesystem_path(path).open("xb")
        self._limit = max_bytes
        self._written = 0

    def writable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._written

    def write(self, data: Any) -> int:
        if self.closed:
            raise ValueError("Output writer is closed.")
        size = memoryview(data).nbytes
        if size > self._limit - self._written:
            raise MLResourceError("ML_OUTPUT_LIMIT", "ML output exceeds its fixed byte limit.")
        count = self._file.write(data)
        self._written += count
        if count != size:
            raise OSError("Incomplete ML output write.")
        return count

    def flush(self) -> None:
        stream = getattr(self, "_file", None)
        if stream is not None and not stream.closed:
            stream.flush()

    def close(self) -> None:
        if not self.closed:
            try:
                super().close()
            finally:
                stream = getattr(self, "_file", None)
                if stream is not None:
                    stream.close()
                guard = getattr(self, "_guard", None)
                if guard is not None:
                    guard.__exit__(None, None, None)
