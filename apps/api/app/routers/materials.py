"""Explicit owned materials; upload permission is checked before parsing its body."""

import uuid
from io import BytesIO

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.material import CitationRequest
from app.schemas.material_updates import MaterialSourceCheck, MaterialSourceUpdate
from app.services import material_service as service
from app.services import material_updates, storage_service

router = APIRouter(prefix="/api/v1", tags=["materials"])


class AudioPreviewRange(BaseModel):
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, allow_inf_nan=False)


@router.post("/materials/{material_id}/playback")
async def audio_playback(
    material_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import audio_playback as playback

    return await playback.issue(await service.load(db, material_id, user.id))


@router.post("/materials/{material_id}/audio-preview")
async def audio_preview(
    material_id: uuid.UUID,
    body: AudioPreviewRange,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import audio_playback as playback

    value = await service.load(db, material_id, user.id)
    return playback.public_preview(await playback.preview(value, body.start, body.end))


@router.api_route("/materials/{material_id}/playback/{ticket}", methods=["GET", "HEAD"])
async def audio_stream(
    material_id: uuid.UUID, ticket: str, db: AsyncSession = Depends(get_db_session)
):
    from app.services import audio_playback as playback

    path = await playback.resolve(db, material_id, ticket)
    return playback.AudioFileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


async def file_body(request: Request, fields: set[str] | frozenset[str] = frozenset()) -> tuple:
    try:
        async with request.form(max_files=1, max_fields=len(fields)) as form:
            items = list(form.multi_items())
            if len({key for key, _ in items}) != len(items) or set(form) != {"file", *fields}:
                raise ValueError()
            file = form["file"]
            if not isinstance(file, UploadFile):
                raise ValueError()
            data = await file.read()
            if not data:
                raise service.error("MATERIAL_SIZE_LIMIT", 413)
            return file.filename or "", data, {key: form[key] for key in fields}
    except service.StatForgeError:
        raise
    except Exception:
        raise service.error("MATERIAL_INPUT_INVALID", 422) from None


@router.post("/projects/{project_id}/materials", status_code=201)
async def upload_material(
    project_id: uuid.UUID,
    request: Request,
    source_path: str | None = Query(default=None, max_length=1024),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    await service.project_access(db, project_id, user.id)
    snapshot = await service.key_snapshot(db, user.id)
    filename, data, _ = await file_body(request)
    return await service.upload(
        db, project_id, user.id, filename, data, snapshot, source_path=source_path
    )


@router.get("/projects/{project_id}/materials")
async def materials(
    project_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> list:
    return await service.list_materials(db, project_id, user.id, offset)


@router.get("/materials/{material_id}/source-versions")
async def source_versions(
    material_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await material_updates.versions(db, material_id, user.id)


@router.post("/materials/{material_id}/source-check")
async def source_check(
    material_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await service.load(db, material_id, user.id)
    snapshot = await service.key_snapshot(db, user.id)
    filename, data, fields = await file_body(request, {"source_sha256"})
    try:
        body = MaterialSourceCheck.model_validate(fields)
    except ValueError:
        raise service.error("MATERIAL_INPUT_INVALID", 422) from None
    return await material_updates.compare(db, material_id, user.id, filename, data, body, snapshot)


@router.post("/materials/{material_id}/source-update")
async def source_update(
    material_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await service.load(db, material_id, user.id)
    snapshot = await service.key_snapshot(db, user.id)
    filename, data, fields = await file_body(
        request, {"source_sha256", "comparison_sha256", "mode"}
    )
    try:
        body = MaterialSourceUpdate.model_validate(fields)
    except ValueError:
        raise service.error("MATERIAL_INPUT_INVALID", 422) from None
    return await material_updates.apply(db, material_id, user.id, filename, data, body, snapshot)


@router.get("/materials/{material_id}")
async def material(
    material_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    return service.public(await service.load(db, material_id, user.id))


@router.get("/materials/{material_id}/text")
async def text(
    material_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    value = await service.load(db, material_id, user.id)
    await service.verify_version(value)
    return {
        "material_sha256": value.sha256,
        "material_version": value.version,
        "offset_unit": "unicode_codepoint_0_based_half_open",
        **service.json.loads(value.extraction_json),
    }


def _text_page_start(source: str, nominal: int, limit: int) -> int:
    if nominal <= 0 or nominal >= len(source):
        return min(nominal, len(source))
    # Each boundary depends only on its nominal offset. Adjacent pages therefore
    # meet at exactly the same code point even when opened in reverse order.
    newline = source.rfind("\n", max(0, nominal - min(2048, limit // 2)), nominal)
    return newline + 1 if newline >= 0 else nominal


@router.get("/materials/{material_id}/text-range")
async def text_range(
    material_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=12000, ge=1, le=20000),
    page_index: int | None = Query(default=None, ge=0),
    locate: bool = False,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Return a bounded span of the existing extraction using its original offsets."""
    value = await service.load(db, material_id, user.id)
    await service.verify_version(value)
    if value.mime_type not in {"text/plain", "text/markdown"}:
        raise service.error("MATERIAL_INPUT_INVALID", 422)
    extraction = service.json.loads(value.extraction_json)
    pages = extraction.get("pages", [])
    if extraction.get("status") != "candidate" or len(pages) != 1 or pages[0]["page"] != 1:
        raise service.error("MATERIAL_LOCATION_INVALID")
    page = pages[0]
    source = page["text"]
    if offset > len(source):
        raise service.error("MATERIAL_LOCATION_INVALID", 422)
    start, end = offset, min(offset + limit, len(source))
    if page_index is not None or locate:
        count = max(1, (len(source) + limit - 1) // limit)
        index = page_index if page_index is not None else min(offset // limit, count - 1)
        if index >= count:
            raise service.error("MATERIAL_LOCATION_INVALID", 422)
        end = _text_page_start(source, (index + 1) * limit, limit)
        if page_index is None and offset >= end and index + 1 < count:
            index += 1
            end = _text_page_start(source, (index + 1) * limit, limit)
        start = _text_page_start(source, index * limit, limit)
    else:
        index = None
    return {
        "material_sha256": value.sha256,
        "material_version": value.version,
        "page": 1,
        "sha256": page["sha256"],
        "start": start,
        "index": index,
        "total": len(source),
        "text": source[start:end],
    }


@router.get("/materials/{material_id}/content")
async def content(
    material_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> FileResponse:
    value = await service.load(db, material_id, user.id)
    await service.verify_version(value)
    if settings.storage_backend != "local":
        raise service.error("MATERIAL_REMOTE_VIEW_UNVERIFIED", 503)
    return FileResponse(
        storage_service.get_local_object_path(value.object_key),
        media_type=value.mime_type,
        filename=value.filename,
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/materials/{material_id}/pdf-page")
async def pdf_page(
    material_id: uuid.UUID,
    material_sha256: str = Query(min_length=64, max_length=64),
    material_version: int = Query(ge=1),
    page: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    """Read one original PDF page, or its page count if page is omitted."""
    from pypdf import PdfReader, PdfWriter

    value = await service.load(db, material_id, user.id)
    if value.sha256 != material_sha256 or value.version != material_version:
        raise service.error("MATERIAL_VERSION_CHANGED")
    await service.verify_version(value)
    if settings.storage_backend != "local":
        raise service.error("MATERIAL_REMOTE_VIEW_UNVERIFIED", 503)
    if value.mime_type != "application/pdf":
        raise service.error("MATERIAL_INPUT_INVALID", 422)
    try:
        with storage_service.get_local_object_path(value.object_key).open("rb") as source:
            reader = PdfReader(source)
            if reader.is_encrypted:
                raise ValueError("encrypted")
            count = len(reader.pages)
            if page is None:
                return {"page_count": count}
            if page > count:
                raise service.error("MATERIAL_INPUT_INVALID", 422)
            writer = PdfWriter()
            writer.add_page(reader.pages[page - 1])
            output = BytesIO()
            writer.write(output)
    except service.StatForgeError:
        raise
    except Exception:
        raise service.error("MATERIAL_INPUT_INVALID", 422) from None
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="page.pdf"',
        },
    )


async def citation_body(request: Request) -> CitationRequest:
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 16000:
                raise ValueError()
            raw.extend(chunk)
        return CitationRequest.model_validate_json(bytes(raw))
    except Exception:
        raise service.error("MATERIAL_INPUT_INVALID", 422) from None


@router.post("/materials/{material_id}/citations", status_code=201)
async def cite(
    material_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    await service.load(db, material_id, user.id)
    snapshot = await service.key_snapshot(db, user.id)
    return await service.save_citation(
        db, material_id, user.id, await citation_body(request), expected_snapshot=snapshot
    )


@router.patch("/materials/{material_id}/citations/{citation_id}")
async def edit_citation(
    material_id: uuid.UUID,
    citation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    await service.load_citation(db, citation_id, user.id, expected_material_id=material_id)
    snapshot = await service.key_snapshot(db, user.id)
    return await service.save_citation(
        db, material_id, user.id, await citation_body(request), citation_id, snapshot
    )


@router.get("/projects/{project_id}/citations")
async def citations(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> list:
    return await service.list_citations(db, project_id, user.id)


@router.get("/materials/{material_id}/citations/{citation_id}")
async def citation_detail(
    material_id: uuid.UUID,
    citation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict:
    citation, material = await service.load_citation(
        db, citation_id, user.id, expected_material_id=material_id
    )
    await service.verify_version(material)
    service.validate_locator(
        material,
        CitationRequest(
            material_sha256=citation.material_sha256,
            material_version=citation.material_version,
            reviewed=citation.reviewed,
            locator=service.json.loads(citation.locator_json),
        ),
    )
    return service.citation_public(citation, material)
