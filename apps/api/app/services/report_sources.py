"""Owned, immutable report source frames shared by document revisions."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import Field

from app.config import settings
from app.exceptions import StatForgeError
from app.research.contracts import canonical_bytes, parse_document
from app.schemas.workspace import ReportSource, StrictModel
from app.services import storage_service

VERSION = "statforge-report-source-v1"
LIMIT = 32 * 1024 * 1024


class Frame(StrictModel):
    storage: str = Field(pattern=r"^statforge-report-source-v1$")
    object_key: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1, le=LIMIT, strict=True)


def fail():
    return StatForgeError(
        409, "REPORT_SOURCE_UNAVAILABLE", "这份来源暂时无法读取，请保留当前成果并核对项目文件。"
    )


def external(value):
    return isinstance(value, dict) and "storage" in value


def key_for(project, user, sha):
    return f"users/{user.id}/projects/{project.id}/report-sources/{sha}.json"


def owned(frame, project, user):
    if frame.object_key != key_for(project, user, frame.sha256):
        raise fail()


def decode(content, frame, ref):
    from app.services.project_research import fingerprint

    if len(content) != frame.bytes or hashlib.sha256(content).hexdigest() != frame.sha256:
        raise fail()
    try:
        value = parse_document(content.decode("utf-8"))
        if (
            set(value) != {"version", "reference", "payload"}
            or value["version"] != VERSION
            or value["reference"] != ref.model_dump(mode="json")
            or not isinstance(value["payload"], dict)
            or fingerprint(ref.kind, value["payload"]) != ref.sha256
            or canonical_bytes(value) != content
        ):
            raise fail()
        return value["payload"]
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise fail() from None


def read(value, ref, project, user, mapping=None):
    from app.plotting.archives import mapped

    try:
        frame = Frame.model_validate(value)
        if mapping:
            frame.object_key = mapped(frame.object_key, mapping)
        owned(frame, project, user)
        with tempfile.TemporaryDirectory(prefix="sf-report-source-") as directory:
            path = Path(directory) / "source.json"
            storage_service.download_to_path(frame.object_key, str(path))
            if path.stat().st_size != frame.bytes:
                raise fail()
            return decode(path.read_bytes(), frame, ref)
    except (ValueError, OSError):
        raise fail() from None


def store(payload, ref, project, user):
    from app.services.project_research import fingerprint

    if fingerprint(ref.kind, payload) != ref.sha256:
        raise fail()
    content = canonical_bytes(
        {
            "version": VERSION,
            "reference": ref.model_dump(mode="json"),
            "payload": payload,
        }
    )
    sha = hashlib.sha256(content).hexdigest()
    frame = Frame(
        storage=VERSION, object_key=key_for(project, user, sha), sha256=sha, bytes=len(content)
    )
    if storage_service.object_exists(frame.object_key):
        read(frame.model_dump(), ref, project, user)
        return frame.model_dump()
    if settings.storage_backend == "local":
        destination = storage_service.get_local_object_path(frame.object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".report-source-", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Readers see a complete, hash-bound frame, including concurrent
            # lazy reads of the same old inline report. No user original is moved.
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return frame.model_dump()
    with tempfile.TemporaryDirectory(prefix="sf-report-save-") as directory:
        path = Path(directory) / "source.json"
        path.write_bytes(content)
        storage_service.upload_file(frame.object_key, str(path), "application/json")
    return frame.model_dump()


def retained(value, ref, project, user):
    """Only call with an owned prior revision, never a newly supplied pointer."""
    if value is None:
        return None
    if external(value):
        frame = Frame.model_validate(value)
        owned(frame, project, user)
        # Keep authored text saveable even while a previously saved frame is
        # unavailable. Reading/exporting it still verifies the actual bytes.
        return frame.model_dump()
    return store(value, ref, project, user)


async def transform(records, change, depth=0):
    """Rewrite only a package copy, including its archived project ancestry."""
    if depth > 8:
        raise fail()
    ids = {
        row["id"]
        for row in records.get("workspace_preview_records", [])
        if row.get("kind") == "report"
    }
    rows = [
        row for row in records.get("workspace_preview_records", []) if row.get("kind") == "report"
    ]
    rows += [
        row for row in records.get("workspace_preview_revisions", []) if row.get("record_id") in ids
    ]
    for row in rows:
        document = json.loads(row["payload_json"])
        for block in document.get("blocks", []):
            if block.get("source") and block.get("source_snapshot") is not None:
                ref = ReportSource.model_validate(block["source"])
                block["source_snapshot"] = await change(block["source_snapshot"], ref)
        row["payload_json"] = canonical_bytes(document).decode()
    for project in records.get("projects", []):
        raw = project.get("snapshot_json")
        snapshot = json.loads(raw) if isinstance(raw, str) else raw
        nested = (snapshot or {}).get("imported_project", {}).get("records")
        if nested is not None:
            await transform(nested, change, depth + 1)
            project["snapshot_json"] = canonical_bytes(snapshot).decode()


async def capture(db, records, project, user):
    from app.plotting.archives import references
    from app.preview.workspace import credential_free

    mapping = references(records)
    objects = {}

    async def compact(value, ref):
        payload = read(value, ref, project, user, mapping) if external(value) else value
        await credential_free(db, user, payload)
        frame = store(payload, ref, project, user)
        objects[frame["object_key"]] = frame["sha256"]
        return frame

    await transform(records, compact)
    return objects


async def restore(db, records, blobs, project, user, created_keys):
    """An imported frame is data, never a native Run or execution permission."""
    from app.plotting.archives import mapped, references
    from app.preview.workspace import credential_free

    mapping = references(records)
    result = {}

    async def copy(value, ref):
        old_key = None
        if external(value):
            frame = Frame.model_validate(value)
            old_key = frame.object_key
            package_key = mapped(old_key, mapping)
            content = blobs.get(package_key)
            if content is None:
                raise fail()
            if isinstance(content, bytes):
                data = content
            else:
                if content.stat().st_size != frame.bytes:
                    raise fail()
                data = content.read_bytes()
            payload = decode(data, frame, ref)
        else:
            payload = value
        await credential_free(db, user, payload)
        saved = store(payload, ref, project, user)
        if saved["object_key"] not in created_keys:
            created_keys.append(saved["object_key"])
        if old_key:
            result[old_key] = saved["object_key"]
            result[mapped(old_key, mapping)] = saved["object_key"]
        return saved

    await transform(records, copy)
    return result
