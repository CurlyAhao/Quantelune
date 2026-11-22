import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.preview.workspace import read_record
from app.routers.materials import file_body
from app.schemas.textgrid import (
    IntensitySegmentation,
    TextGridChange,
    TextGridCreate,
    TextGridDocument,
)
from app.services import material_service, textgrid

router = APIRouter(prefix="/api/v1", tags=["time annotations"])


@router.post("/materials/{material_id}/textgrids/intensity")
async def intensity_regions(
    material_id: uuid.UUID,
    body: IntensitySegmentation,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services.intensity_segmentation import generate

    return await generate(db, user, material_id, body)


@router.get("/materials/{material_id}/textgrids")
async def list_grids(
    material_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await textgrid.list_grids(db, material_id, user)


@router.post("/materials/{material_id}/textgrids")
async def import_grid(
    material_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await material_service.load(db, material_id, user.id)
    filename, data, fields = await file_body(request, {"offset"})
    return await textgrid.import_grid(db, material_id, user, filename, data, fields["offset"])


@router.patch("/textgrids/{record_id}")
async def edit_grid(
    record_id: uuid.UUID,
    body: TextGridChange,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await textgrid.edit_grid(db, record_id, user, body)


@router.post("/materials/{material_id}/textgrids/create")
async def create_grid(
    material_id: uuid.UUID,
    body: TextGridCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await textgrid.create_grid(db, material_id, user, body.title)


@router.get("/textgrids/{record_id}/export")
async def export_grid(
    record_id: uuid.UUID,
    format: Literal["long_text", "short_text", "binary"] = "long_text",
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    record = await read_record(db, record_id, user)
    if record["payload"]["kind"] != "textgrid":
        raise textgrid.fail("找不到此时间标注。", 404)
    document = TextGridDocument.model_validate(record["payload"])
    data = textgrid.export(document).encode("utf-8")
    if format != "long_text":
        data = await textgrid.convert_format(data, format)
        if format == "short_text":
            data = textgrid.decode(data).encode("utf-8")
    return Response(
        data, media_type="application/octet-stream" if format == "binary" else "text/plain"
    )
