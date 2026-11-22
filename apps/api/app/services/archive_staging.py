"""Shared disk and expanded-size limits for request-scoped archive staging."""

import shutil
from pathlib import Path

from app.config import settings

# Leave room for the OS and database alongside the staging area.
MIN_FREE_BYTES = 64 * 1024**2


def expanded_limit() -> int:
    return settings.archive_max_expanded_size_mb * 1024**2


def require_disk_space(directory: Path, size: int) -> None:
    if shutil.disk_usage(directory).free < size + MIN_FREE_BYTES:
        raise ValueError("磁盘可用空间不足，无法暂存压缩包。")


class ExpandedBudget:
    """Count actual bytes read from expanded members, not advertised ZIP sizes."""

    def __init__(self):
        self.total = 0

    def consume(self, size: int) -> None:
        self.total += size
        if self.total > expanded_limit():
            raise ValueError("压缩包解压后超过大小上限。")

    def chunks(self, stream):
        while chunk := stream.read(1024 * 1024):
            self.consume(len(chunk))
            yield chunk

    def copy(self, source, dest, directory: Path) -> None:
        for chunk in self.chunks(source):
            require_disk_space(directory, len(chunk))
            dest.write(chunk)

    def read(self, source, limit: int) -> bytes:
        parts, total = [], 0
        for chunk in self.chunks(source):
            total += len(chunk)
            if total > limit:
                raise ValueError("压缩包中的文本或 XML 超过大小上限。")
            parts.append(chunk)
        return b"".join(parts)
