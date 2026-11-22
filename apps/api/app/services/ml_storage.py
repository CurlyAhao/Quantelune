"""Bounded ML storage I/O. Authentication remains in the API process."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from minio.credentials import Credentials
from minio.signer import sign_v4_s3

from app.config import settings
from app.ml.resources import MLResourceError
from app.ml.worker_control import MLTaskScope
from app.services import storage_service


def _signed_request(method: str, key: str, body: bytes = b"") -> tuple[str, dict[str, str]]:
    """Pinned MinIO SigV4 implementation; secrets never appear in the URL."""
    date = datetime.now(UTC)
    url = (
        ("https://" if settings.minio_secure else "http://")
        + settings.minio_endpoint
        + "/"
        + quote(settings.minio_bucket, safe="")
        + "/"
        + quote(key, safe="/")
    )
    digest = sha256(body).hexdigest()
    headers: dict[str, str | list[str] | tuple[str]] = {
        "Host": urlsplit(url).netloc,
        "X-Amz-Date": date.strftime("%Y%m%dT%H%M%SZ"),
        "X-Amz-Content-Sha256": digest,
    }
    signed = sign_v4_s3(
        method=method,
        url=urlsplit(url),
        region=settings.minio_region,
        headers=headers,
        credentials=Credentials(settings.minio_access_key, settings.minio_secret_key),
        content_sha256=digest,
        date=date,
    )
    return url, {name: str(value) for name, value in signed.items()}


async def download_input(key: str, destination: str, task: MLTaskScope) -> None:
    """No retries/redirects, actual byte cap and cancellable connection/stream."""
    await task.check()
    url, headers = _signed_request("GET", key)
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    raise MLResourceError("ML_STORAGE_FAILED", "ML input storage request failed.")
                size = 0
                with Path(destination).open("wb") as stream:
                    async for chunk in response.aiter_bytes(64 * 1024):
                        await task.check()
                        size += len(chunk)
                        stream.write(chunk)
                await task.check()
    except httpx.HTTPError as exc:
        # Do not persist raw HTTP exceptions, Authorization or response content.
        raise MLResourceError("ML_STORAGE_FAILED", "ML input storage request failed.") from exc


async def upload_artifact(
    key: str, artifact: bytes, task: MLTaskScope, *, media_type: str = "application/octet-stream"
) -> None:
    if len(artifact) > 128 * 1024 * 1024:
        raise MLResourceError("ML_OUTPUT_LIMIT", "ML artifact exceeds 128 MiB.")
    url, headers = _signed_request("PUT", key, artifact)
    headers["Content-Type"] = media_type
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            async with client.stream("PUT", url, headers=headers, content=artifact) as response:
                if response.status_code not in {200, 201, 204}:
                    raise MLResourceError(
                        "ML_STORAGE_FAILED", "ML artifact storage request failed."
                    )
                await task.check()
    except httpx.HTTPError as exc:
        raise MLResourceError("ML_STORAGE_FAILED", "ML artifact storage request failed.") from exc


def delete_staged_object(key: str) -> None:
    """Strict compensation, limited to newly generated ML object keys."""
    if not key.startswith("ml-artifacts/") or ".." in key or len(key) > 256:
        raise ValueError("Invalid owned ML artifact key.")
    if settings.storage_backend == "local":
        storage_service.delete_object(key)
        return
    url, headers = _signed_request("DELETE", key)
    with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
        with client.stream("DELETE", url, headers=headers) as response:
            if response.status_code not in {200, 204, 404}:
                raise MLResourceError("ML_CLEANUP_FAILED", "ML object cleanup failed.")
