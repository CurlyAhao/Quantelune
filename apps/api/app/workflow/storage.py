"""Private bounded storage for objects freshly allocated by workflow execution."""

from __future__ import annotations

from hashlib import sha256

import httpx

from app.config import settings
from app.ml.bounded_writer import filesystem_path
from app.services import ml_storage, storage_service
from app.workflow.artifacts import MAX_OUTPUT_BYTES


async def verified_output(
    key: str, expected_bytes: int | None, expected_sha: str, *, limit: int = MAX_OUTPUT_BYTES
) -> bytes:
    if not 0 < limit <= MAX_OUTPUT_BYTES or (
        expected_bytes is not None and not 0 < expected_bytes <= limit
    ):
        raise ValueError("WORKFLOW_ARTIFACT_INVALID")
    byte_limit = expected_bytes if expected_bytes is not None else limit
    if settings.storage_backend == "local":
        path = filesystem_path(storage_service.get_local_object_path(key))
        if not path.is_file() or not 0 < path.stat().st_size <= byte_limit:
            raise ValueError("WORKFLOW_ARTIFACT_CHANGED")
        content = path.read_bytes()
    else:
        url, headers = ml_storage._signed_request("GET", key)
        content_buffer = bytearray()
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    raise ValueError("WORKFLOW_ARTIFACT_UNAVAILABLE")
                async for chunk in response.aiter_bytes(64 * 1024):
                    if len(content_buffer) + len(chunk) > byte_limit:
                        raise ValueError("WORKFLOW_ARTIFACT_CHANGED")
                    content_buffer.extend(chunk)
        content = bytes(content_buffer)
    if (expected_bytes is not None and len(content) != expected_bytes) or sha256(
        content
    ).hexdigest() != expected_sha:
        raise ValueError("WORKFLOW_ARTIFACT_CHANGED")
    return content


def delete_output(key: str) -> None:
    # This callable is registered only alongside a newly generated server key.
    # It is never selected by an API path or applied to a reused dataset.
    if settings.storage_backend == "local":
        storage_service.delete_object(key)
        return
    url, headers = ml_storage._signed_request("DELETE", key)
    with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
        with client.stream("DELETE", url, headers=headers) as response:
            if response.status_code not in {200, 204, 404}:
                raise OSError("Workflow owned-object cleanup failed")
