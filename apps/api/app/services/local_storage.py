"""Local file-system storage adapter — replaces MinIO for desktop/offline mode.

Mirrors the storage_service API (ensure_bucket, upload_file, download_to_path,
object_exists, delete_object, get_presigned_download_url) using a local project
directory so StatForge can run without MinIO or Docker.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.config import settings


class LocalStorage:
    """Local directory-based storage (no MinIO dependency)."""

    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root or settings.sqlite_path).parent / "storage"

    @property
    def root(self) -> Path:
        return self._root

    def ensure_bucket(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def object_exists(self, object_key: str) -> bool:
        return (self._root / object_key).exists()

    def upload_file(self, object_key: str, file_path: str, content_type: str = "") -> None:
        """Copy a file from file_path into local storage under object_key."""
        dest = self._root / object_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest)

    def download_to_path(self, object_key: str, dest_path: str) -> None:
        """Copy a stored file to dest_path."""
        src = self._root / object_key
        if not src.exists():
            from app.exceptions import StorageError

            raise StorageError(f"Object not found: {object_key}")
        shutil.copy2(src, dest_path)

    def delete_object(self, object_key: str) -> None:
        p = self._root / object_key
        if p.exists():
            p.unlink()

    def get_presigned_download_url(self, object_key: str, expires_hours: int = 1) -> str:
        """Return a file:// URL for local storage (no presigned needed)."""
        return f"file://{(self._root / object_key).resolve()}"

    def generate_object_key(
        self, user_id: uuid.UUID, project_id: uuid.UUID, dataset_id: uuid.UUID, safe_filename: str
    ) -> str:
        return f"users/{user_id}/projects/{project_id}/datasets/{dataset_id}/{safe_filename}"


# Global instance; the router/service picks LocalStorage or MinIO based on config.
_local = LocalStorage()


def get_local_storage() -> LocalStorage:
    return _local
