"""Object storage service — dispatches to MinIO or local file system.

Set STORAGE_BACKEND=local in .env for desktop/offline mode (no MinIO/Docker).
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import settings
from app.ml.bounded_writer import filesystem_path

logger = logging.getLogger("statforge.storage")

# ── Local storage (used when STORAGE_BACKEND=local) ────────────────────────

_local_root: Path | None = None


def _get_local_root() -> Path:
    global _local_root
    if _local_root is None:
        _local_root = Path(settings.local_storage_path).resolve()
    filesystem_path(_local_root).mkdir(parents=True, exist_ok=True)
    return _local_root


def _is_local() -> bool:
    return settings.storage_backend == "local"


# ── MinIO client (lazy) ──────────────────────────────────────────────────────

_minio_client = None


def _get_minio():
    global _minio_client
    if _minio_client is None:
        from minio import Minio

        _minio_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _minio_client


# ── Public API ────────────────────────────────────────────────────────────────


def ensure_bucket() -> None:
    if _is_local():
        _get_local_root()
        return
    try:
        c = _get_minio()
        if not c.bucket_exists(settings.minio_bucket):
            c.make_bucket(settings.minio_bucket)
    except Exception as exc:
        logger.warning("MinIO init skipped (set STORAGE_BACKEND=local for offline): %s", exc)


def generate_object_key(
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    safe_filename: str,
) -> str:
    seg = uuid.uuid4().hex[:12]
    return str(
        PurePosixPath(
            "users",
            str(user_id),
            "projects",
            str(project_id),
            "datasets",
            str(dataset_id),
            seg,
            safe_filename,
        )
    )


def _safe_resolve(root: Path, key: str) -> Path:
    """Resolve object_key under root, rejecting path traversal."""
    parts = PurePosixPath(key).parts
    if (
        not parts
        or PurePosixPath(key).is_absolute()
        or "\\" in key
        or ":" in key
        or "\x00" in key
        or any(part in {".", ".."} for part in parts)
    ):
        raise ValueError(f"Path traversal detected: {key}")
    # Windows can transparently redirect individual AppData namespaces while
    # reporting the storage root itself at its logical location. Resolve the
    # namespace once, rejecting actual links before accepting that OS mapping.
    logical_namespace = root / parts[0]
    try:
        attributes = logical_namespace.lstat()
        if logical_namespace.is_symlink() or getattr(attributes, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Storage namespace is a link: {key}")
    except FileNotFoundError:
        pass
    namespace = logical_namespace.resolve()
    dest = namespace.joinpath(*parts[1:]).resolve()
    if not dest.is_relative_to(namespace):
        raise ValueError(f"Path traversal detected: {key}")
    return filesystem_path(dest)


def upload_file(object_key: str, file_path: str, content_type: str) -> None:
    if _is_local():
        from app.services.local_workspaces import object_location

        linked = object_location(object_key)
        if linked and linked[1]:
            raise ValueError(
                "Workspace source files are read in place, never overwritten by storage writes."
            )
        dest = linked[0] if linked else _safe_resolve(_get_local_root(), object_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest)
        logger.info("Local upload: %s (%d bytes)", object_key, os.path.getsize(file_path))
        return
    c = _get_minio()
    c.fput_object(settings.minio_bucket, object_key, file_path, content_type)


def delete_object(object_key: str) -> None:
    if _is_local():
        from app.services.local_workspaces import object_location

        linked = object_location(object_key)
        if linked and linked[1]:
            return  # Removing an app reference never deletes the user's source.
        p = linked[0] if linked else _safe_resolve(_get_local_root(), object_key)
        if p.exists():
            p.unlink()
        return
    try:
        _get_minio().remove_object(settings.minio_bucket, object_key)
    except Exception:
        pass


def object_exists(object_key: str) -> bool:
    if _is_local():
        return get_local_object_path(object_key).exists()
    try:
        _get_minio().stat_object(settings.minio_bucket, object_key)
        return True
    except Exception:
        return False


def get_local_object_path(object_key: str) -> Path:
    """Resolve a local object for an authenticated download response."""
    if not _is_local():
        raise RuntimeError("A filesystem path is only available for local storage.")
    from app.services.local_workspaces import object_location

    linked = object_location(object_key)
    legacy = _safe_resolve(_get_local_root(), object_key)
    if linked:
        return linked[0] if linked[1] or linked[0].exists() or not legacy.exists() else legacy
    return legacy


def get_presigned_download_url(object_key: str, expires_hours: int = 1) -> str:
    if _is_local():
        return f"file://{get_local_object_path(object_key)}"
    return _get_minio().presigned_get_object(
        settings.minio_bucket, object_key, expires=timedelta(hours=expires_hours)
    )


def download_to_path(object_key: str, dest_path: str) -> None:
    if _is_local():
        src = get_local_object_path(object_key)
        if not src.exists():
            raise FileNotFoundError(f"Object not found: {object_key}")
        shutil.copy2(src, dest_path)
        return
    _get_minio().fget_object(settings.minio_bucket, object_key, dest_path)


def storage_health_check() -> dict[str, Any]:
    try:
        if _is_local():
            root = _get_local_root()
            return {"status": "ok", "backend": "local", "path": str(root)}
        c = _get_minio()
        c.bucket_exists(settings.minio_bucket)
        return {"status": "ok", "backend": "minio", "bucket": settings.minio_bucket}
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)}
