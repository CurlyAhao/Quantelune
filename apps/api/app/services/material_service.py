"""Owned material metadata and reviewed locations, never arbitrary host paths."""

import codecs
import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.config import settings
from app.exceptions import StatForgeError
from app.models.material import Citation, Material
from app.models.preview import PreviewConfig
from app.models.project import Project
from app.preview import vault as credentials
from app.preview.security import contains_secret
from app.preview.write_fence import acquire_write_fence
from app.schemas.material import CitationReference, CitationRequest
from app.services import storage_service
from app.services.project_access import check_access

PDF_TIMEOUT = 8


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def error(code: str = "MATERIAL_UNAVAILABLE", status: int = 409) -> StatForgeError:
    return StatForgeError(status, code, "Material or source location is unavailable.")


@dataclass(frozen=True)
class WriteSecretSnapshot:
    user_id: uuid.UUID
    exists: bool
    revision: int | None
    namespace: str | None
    key_configured: bool
    secret: str | None = field(repr=False)

    @property
    def binding(self) -> tuple:
        return (self.exists, self.revision, self.namespace, self.key_configured)


async def key_snapshot(db: AsyncSession, user_id: uuid.UUID) -> WriteSecretSnapshot:
    """Read only the current actor's immutable binding and owned target."""
    with db.no_autoflush:
        row = (
            await db.execute(
                select(
                    PreviewConfig.revision,
                    PreviewConfig.vault_namespace,
                    PreviewConfig.key_configured,
                ).where(PreviewConfig.user_id == user_id)
            )
        ).one_or_none()
    if row is None:
        return WriteSecretSnapshot(user_id, False, None, None, False, None)
    if not row.key_configured:
        return WriteSecretSnapshot(user_id, True, row.revision, row.vault_namespace, False, None)
    try:
        secret = credentials.vault.get(row.vault_namespace, user_id)
    except Exception:
        raise error("MATERIAL_SECURE_REVIEW_REQUIRED") from None
    if not secret:
        raise error("MATERIAL_SECURE_REVIEW_REQUIRED")
    return WriteSecretSnapshot(user_id, True, row.revision, row.vault_namespace, True, secret)


async def fence_snapshot(
    db: AsyncSession, user_id: uuid.UUID, expected: WriteSecretSnapshot
) -> WriteSecretSnapshot:
    if expected.user_id != user_id:
        raise error("MATERIAL_SECURE_REVIEW_REQUIRED")
    await acquire_write_fence(db, user_id)
    fresh = await key_snapshot(db, user_id)
    if fresh.binding != expected.binding:
        raise error("MATERIAL_SECURE_REVIEW_REQUIRED")
    return fresh


def reject_secret(value: Any, secret: str | None) -> None:
    if secret and contains_secret(value, secret):
        raise error("MATERIAL_INPUT_INVALID", 422)


async def project_access(db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    with db.no_autoflush:
        identity = (
            await db.execute(
                select(Project.owner_id, Project.is_deleted).where(Project.id == project_id)
            )
        ).one_or_none()

    def unavailable():
        return error(status=404)

    check_access(identity, user_id, missing=unavailable, denied=unavailable)


async def load(
    db: AsyncSession,
    material_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_project_id: uuid.UUID | None = None,
) -> Material:
    with db.no_autoflush:
        identity = (
            await db.execute(
                select(Material.user_id, Material.project_id).where(Material.id == material_id)
            )
        ).one_or_none()
    if (
        identity is None
        or identity.user_id != user_id
        or (expected_project_id is not None and identity.project_id != expected_project_id)
    ):
        raise error(status=404)
    await project_access(db, identity.project_id, user_id)
    with db.no_autoflush:
        material = await db.scalar(
            select(Material)
            .options(noload("*"))
            .where(Material.id == material_id)
            .execution_options(populate_existing=True)
        )
    assert material is not None
    return material


def public(material: Material) -> dict[str, Any]:
    return {
        "id": str(material.id),
        "project_id": str(material.project_id),
        "filename": material.filename,
        "mime_type": material.mime_type,
        "file_size": material.file_size,
        "sha256": material.sha256,
        "version": material.version,
        "metadata": json.loads(material.metadata_json),
    }


def temp_parent() -> Path:
    parent = Path(settings.app_data_path or settings.upload_temp_dir) / "m"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def pdf_extract(path: Path) -> dict[str, Any]:
    from app.services.parser_control import run

    try:
        return run("pdf", str(path), timeout=PDF_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "code": "PDF_TIMEOUT", "pages": []}
    except Exception:
        return {"status": "unavailable", "code": "PDF_EXTRACTION_FAILED", "pages": []}


def inspect(path: Path, filename: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    with path.open("rb") as stream:
        data = stream.read(64)
    extension = Path(filename).suffix.lower()
    extraction: dict[str, Any] = {
        "status": "unavailable",
        "code": "NO_TEXT_EXTRACTION",
        "pages": [],
    }
    metadata: dict[str, Any] = {"perception": "unavailable", "location": "human"}
    if data.startswith(b"%PDF-") and extension == ".pdf":
        extraction = pdf_extract(path)
        mime = "application/pdf"
    elif extension in {".txt", ".md"}:
        try:
            data = path.read_bytes()
            text = data.decode("utf-8-sig")
            if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                raise ValueError()
            if text.lstrip().lower().startswith(("<html", "<!doctype html", "<svg", "<?xml")):
                raise ValueError()
        except (UnicodeError, ValueError):
            raise error("MATERIAL_FORMAT_UNSUPPORTED", 415) from None
        extraction = {
            "status": "candidate",
            "extractor": "utf8-sig:codepoint-v1",
            "code": None,
            "pages": [
                {"page": 1, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
            ],
        }
        mime = "text/plain"
    elif data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        if not width or not height:
            raise error("MATERIAL_IMAGE_LIMIT", 413)
        metadata.update(width=width, height=height)
        mime = "image/png"
    elif data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        if not width or not height:
            raise error("MATERIAL_IMAGE_LIMIT", 413)
        metadata.update(width=width, height=height)
        mime = "image/gif"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            with wave.open(str(path)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            if not duration > 0:
                raise ValueError()
        except Exception:
            raise error("MATERIAL_FORMAT_UNSUPPORTED", 415) from None
        metadata.update(duration_seconds=duration, duration_status="server_header_verified")
        mime = "audio/wav"
    elif extension == ".mp3":
        from app.services.multimodal_cases import runtime_config

        config = runtime_config()
        try:
            probe = subprocess.run(
                [
                    config["options"]["ffprobe_path"],
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            info = json.loads(probe.stdout)
            stream = next(item for item in info["streams"] if item.get("codec_type") == "audio")
            if stream.get("codec_name") != "mp3":
                raise ValueError()
        except Exception:
            raise error("MATERIAL_FORMAT_UNSUPPORTED", 415) from None
        # The decoded sample duration is obtained by the playback worker. MP3
        # container duration may include encoder padding and is not a time origin.
        metadata.update(duration_seconds=None, duration_status="decoded_on_open")
        mime = "audio/mpeg"
    elif (
        len(data) >= 12
        and data[4:8] == b"ftyp"
        and data[8:12] in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V "}
    ):
        metadata.update(duration_seconds=None, duration_status="unknown_candidate")
        mime = "video/mp4"
    else:
        raise error("MATERIAL_FORMAT_UNSUPPORTED", 415)
    metadata.update(extraction_status=extraction["status"], extraction_code=extraction.get("code"))
    return mime, metadata, extraction


def reject_file_secret(path: Path, secret: str | None) -> None:
    """Match the original UTF-8-with-replacement scan, including chunk boundaries."""
    if not secret:
        return
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    tail = ""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            text = tail + decoder.decode(chunk)
            if secret in text:
                raise error("MATERIAL_INPUT_INVALID", 422)
            tail = text[-(len(secret) - 1) :] if len(secret) > 1 else ""
        if secret in tail + decoder.decode(b"", final=True):
            raise error("MATERIAL_INPUT_INVALID", 422)


async def upload(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    data: bytes,
    expected_snapshot: WriteSecretSnapshot | None = None,
    *,
    source_path: str | None = None,
) -> dict:
    await project_access(db, project_id, user_id)
    with tempfile.TemporaryDirectory(prefix="u-", dir=temp_parent()) as directory:
        path = Path(directory) / "source"
        await anyio.to_thread.run_sync(path.write_bytes, data)
        return await upload_path(
            db, project_id, user_id, filename, path, expected_snapshot, source_path=source_path
        )


async def upload_path(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    path: Path,
    expected_snapshot: WriteSecretSnapshot | None = None,
    *,
    source_path: str | None = None,
) -> dict:
    await project_access(db, project_id, user_id)
    size = path.stat().st_size
    if not size:
        raise error("MATERIAL_SIZE_LIMIT", 413)
    filename = filename.replace("\\", "/").split("/")[-1][:255]
    if not filename or any(ord(c) < 32 for c in filename):
        raise error("MATERIAL_FORMAT_UNSUPPORTED", 415)
    snapshot = expected_snapshot or await key_snapshot(db, user_id)
    if source_path:
        source_path = source_path.replace("\\", "/")
        if (
            len(source_path) > 1024
            or source_path.startswith("/")
            or ":" in source_path
            or ".." in source_path.split("/")
            or any(ord(c) < 32 for c in source_path)
        ):
            raise error("MATERIAL_INPUT_INVALID", 422)
    raw = {"filename": filename, "source_path": source_path}
    reject_secret(raw, snapshot.secret)
    await anyio.to_thread.run_sync(reject_file_secret, path, snapshot.secret)
    material_id = uuid.uuid4()
    key = f"m/{material_id.hex}/source"
    from app.services.local_workspaces import register_object

    register_object(project_id, key)
    mime, metadata, extraction = await anyio.to_thread.run_sync(inspect, path, filename)
    if source_path:
        metadata["source_path"] = source_path
    candidate = {"metadata": metadata, "extraction": extraction}
    reject_secret(candidate, snapshot.secret)
    fresh = await fence_snapshot(db, user_id, snapshot)
    reject_secret({"raw": raw, "candidate": candidate}, fresh.secret)
    await anyio.to_thread.run_sync(reject_file_secret, path, fresh.secret)
    await project_access(db, project_id, user_id)
    await anyio.to_thread.run_sync(storage_service.upload_file, key, str(path), mime)

    def digest():
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    material = Material(
        id=material_id,
        user_id=user_id,
        project_id=project_id,
        filename=filename,
        mime_type=mime,
        file_size=size,
        sha256=await anyio.to_thread.run_sync(digest),
        version=1,
        object_key=key,
        metadata_json=canonical(metadata),
        extraction_json=canonical(extraction),
    )
    db.add(material)
    await db.flush()
    return public(material)


async def verify_version(material: Material) -> None:
    if not re.fullmatch(r"m/[a-f0-9]{32}/source", material.object_key):
        raise error("MATERIAL_VERSION_CHANGED")

    def verify() -> None:
        if settings.storage_backend == "local":
            path = storage_service.get_local_object_path(material.object_key)
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != material.sha256:
                raise error("MATERIAL_VERSION_CHANGED")
            return
        with tempfile.TemporaryDirectory(prefix="v-", dir=temp_parent()) as directory:
            path = Path(directory) / "source"
            storage_service.download_to_path(material.object_key, str(path))
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != material.sha256:
                raise error("MATERIAL_VERSION_CHANGED")

    try:
        await anyio.to_thread.run_sync(verify)
    except StatForgeError:
        raise
    except Exception:
        raise error() from None


async def list_materials(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, offset: int = 0
) -> list[dict]:
    await project_access(db, project_id, user_id)
    with db.no_autoflush:
        rows = (
            await db.scalars(
                select(Material)
                .options(noload("*"))
                .where(Material.project_id == project_id, Material.user_id == user_id)
                .order_by(Material.created_at, Material.id)
                .offset(offset)
                .limit(100)
            )
        ).all()
    return [public(m) for m in rows]


def validate_locator(material: Material, body: CitationRequest) -> None:
    if body.material_sha256 != material.sha256 or body.material_version != material.version:
        raise error("MATERIAL_VERSION_CHANGED")
    locator = body.locator
    metadata = json.loads(material.metadata_json)
    if locator.kind == "text":
        extraction = json.loads(material.extraction_json)
        pages = extraction.get("pages", [])
        page = next((p for p in pages if p["page"] == locator.page), None)
        if page is None or extraction.get("status") != "candidate":
            raise error("MATERIAL_LOCATION_INVALID")
        text = page["text"]
        if (
            page["sha256"] != hashlib.sha256(text.encode()).hexdigest()
            or locator.text_sha256 != page["sha256"]
            or locator.end <= locator.start
            or locator.end > len(text)
            or text[locator.start : locator.end] != locator.quote
        ):
            raise error("MATERIAL_LOCATION_INVALID")
    elif locator.kind == "region":
        if not material.mime_type.startswith("image/"):
            raise error("MATERIAL_LOCATION_INVALID")
    elif locator.kind == "time":
        if material.mime_type not in {"audio/wav", "audio/mpeg", "video/mp4"}:
            raise error("MATERIAL_LOCATION_INVALID")
        duration = metadata.get("duration_seconds")
        # HTML media reports time to microseconds; keep the sample-clock boundary.
        if duration is not None and duration < locator.end_seconds <= duration + 0.000001:
            locator.end_seconds = duration
        invalid_range = (
            locator.end_seconds != locator.start_seconds
            if locator.point
            else locator.end_seconds <= locator.start_seconds
        )
        if invalid_range or (duration is not None and locator.end_seconds > duration):
            raise StatForgeError(
                422, "MATERIAL_LOCATION_INVALID", "选段超出录音时长，请在播放器中调整起止时间。"
            )


def citation_public(citation: Citation, material: Material) -> dict:
    body = {
        "id": str(citation.id),
        "material_id": str(material.id),
        "project_id": str(material.project_id),
        "filename": material.filename,
        "material_sha256": citation.material_sha256,
        "material_version": citation.material_version,
        "revision": citation.revision,
        "reviewed": citation.reviewed,
        "locator": json.loads(citation.locator_json),
        "location_status": "unknown_candidate"
        if material.mime_type == "video/mp4"
        else "human_review",
        "perception": "unavailable",
    }
    body["citation_sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
    return body


async def save_citation(
    db: AsyncSession,
    material_id: uuid.UUID,
    user_id: uuid.UUID,
    body: CitationRequest,
    citation_id: uuid.UUID | None = None,
    expected_snapshot: WriteSecretSnapshot | None = None,
) -> dict:
    material = await load(db, material_id, user_id)
    citation = None
    if citation_id is not None:
        citation, owned_material = await load_citation(
            db, citation_id, user_id, material.project_id, material.id
        )
        if owned_material.id != material.id or citation.revision != body.expected_revision:
            raise error("MATERIAL_CITATION_CHANGED")
    snapshot = expected_snapshot or await key_snapshot(db, user_id)
    candidate = body.model_dump(mode="json")
    reject_secret(candidate, snapshot.secret)
    await verify_version(material)
    validate_locator(material, body)
    fresh = await fence_snapshot(db, user_id, snapshot)
    reject_secret(candidate, fresh.secret)
    material = await load(db, material_id, user_id)
    if citation_id is not None:
        citation, _ = await load_citation(
            db, citation_id, user_id, material.project_id, material.id
        )
        if citation.revision != body.expected_revision:
            raise error("MATERIAL_CITATION_CHANGED")
    await verify_version(material)
    validate_locator(material, body)
    if citation_id is None:
        citation = Citation(
            id=uuid.uuid4(),
            material_id=material.id,
            user_id=user_id,
            project_id=material.project_id,
            material_sha256=material.sha256,
            material_version=material.version,
            revision=1,
            reviewed=body.reviewed,
            locator_json=canonical(body.locator.model_dump(mode="json")),
        )
        db.add(citation)
        await db.flush()
    else:
        assert citation is not None
        result = await db.scalar(
            update(Citation)
            .where(Citation.id == citation_id, Citation.revision == body.expected_revision)
            .values(
                locator_json=canonical(body.locator.model_dump(mode="json")),
                reviewed=body.reviewed,
                revision=citation.revision + 1,
            )
            .returning(Citation.id)
        )
        if result is None:
            raise error("MATERIAL_CITATION_CHANGED")
        await db.refresh(citation)
    return citation_public(citation, material)


async def load_citation(
    db: AsyncSession,
    citation_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_project_id: uuid.UUID | None = None,
    expected_material_id: uuid.UUID | None = None,
) -> tuple[Citation, Material]:
    with db.no_autoflush:
        identity = (
            await db.execute(
                select(Citation.user_id, Citation.project_id, Citation.material_id).where(
                    Citation.id == citation_id
                )
            )
        ).one_or_none()
    if (
        identity is None
        or identity.user_id != user_id
        or (expected_project_id is not None and identity.project_id != expected_project_id)
        or (expected_material_id is not None and identity.material_id != expected_material_id)
    ):
        raise error(status=404)
    await project_access(db, identity.project_id, user_id)
    material = await load(db, identity.material_id, user_id, identity.project_id)
    if material.project_id != identity.project_id:
        raise error(status=404)
    with db.no_autoflush:
        citation = await db.scalar(
            select(Citation)
            .where(Citation.id == citation_id)
            .execution_options(populate_existing=True)
        )
    assert citation is not None
    return citation, material


async def list_citations(db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> list[dict]:
    await project_access(db, project_id, user_id)
    with db.no_autoflush:
        ids = (
            await db.scalars(
                select(Citation.id)
                .where(Citation.project_id == project_id, Citation.user_id == user_id)
                .limit(100)
            )
        ).all()
    result = []
    for citation_id in ids:
        citation, material = await load_citation(db, citation_id, user_id)
        result.append(citation_public(citation, material))
    return result


async def resolve_references(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, references: list[CitationReference]
) -> list[dict]:
    if len({r.citation_id for r in references}) != len(references):
        raise error("MATERIAL_CITATION_CHANGED")
    result = []
    for ref in references:
        citation, material = await load_citation(db, ref.citation_id, user_id, project_id)
        if material.project_id != project_id or not citation.reviewed:
            raise error("MATERIAL_CITATION_UNREVIEWED")
        await verify_version(material)
        validate_locator(
            material,
            CitationRequest(
                material_sha256=citation.material_sha256,
                material_version=citation.material_version,
                reviewed=citation.reviewed,
                locator=json.loads(citation.locator_json),
            ),
        )
        value = citation_public(citation, material)
        if ref.revision != citation.revision or ref.citation_sha256 != value["citation_sha256"]:
            raise error("MATERIAL_CITATION_CHANGED")
        result.append(value)
    if len(canonical(result).encode()) > 8192:
        raise error("MATERIAL_REFERENCE_LIMIT", 413)
    return result
