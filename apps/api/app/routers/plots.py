import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.ml.resources import MLResourceError
from app.models.user import User
from app.research.draft_advice import load_plot_context
from app.routers.preview import safe_body
from app.schemas.draft_advice import PlotAdviceContextRequest
from app.schemas.plot_style import PlotStyle
from app.services import plot_code_files, plot_packages, plots

router = APIRouter(prefix="/api/v1/projects/{project_id}/plots", tags=["plots"])


class OpenCode(BaseModel):
    revision: int = Field(ge=1)
    editor: Literal["vscode", "cursor", "vscode-insiders", "notepadpp", "system"]
    as_new: bool = False


@router.post("/{draft_id}/advice-context")
async def advice_context(
    project_id: uuid.UUID,
    draft_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    body = await safe_body(request, PlotAdviceContextRequest)
    try:
        context = await load_plot_context(db, project_id, draft_id, user, body.revision)
    except ValueError:
        raise StatForgeError(
            422, "ADVICE_CONTEXT_INVALID", "请减少所选内容并检查代码后重试。"
        ) from None
    return {"source_version": context.source_version, "context": context.document}


class ImportCode(BaseModel):
    revision: int = Field(ge=1)
    file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    replace: bool = False


@router.get("/editors")
async def editors(user: User = Depends(get_current_user)):
    return plot_code_files.editors()


@router.get("/{draft_id}/code-file")
async def code_file(
    project_id: uuid.UUID,
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plot_code_files.status(db, project_id, draft_id, user)


@router.post("/{draft_id}/code-file/open")
async def open_code(
    project_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: OpenCode,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plot_code_files.open_file(
        db, project_id, draft_id, body.revision, body.editor, body.as_new, user
    )


@router.post("/{draft_id}/code-file/import")
async def import_code(
    project_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: ImportCode,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plot_code_files.import_file(
        db, project_id, draft_id, body.revision, body.file_sha256, body.replace, user
    )


class Start(BaseModel):
    revision: int = Field(ge=1)
    request_id: uuid.UUID


class StyleRequest(BaseModel):
    style: PlotStyle
    request_id: uuid.UUID


class AcousticOverviewRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    request_id: uuid.UUID


@router.post("/runs/{run_id}/acoustic-overview")
async def acoustic_overview(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    body: AcousticOverviewRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plots.acoustic_overview(
        db, project_id, run_id, body.expected_revision, body.request_id, user
    )


@router.post("/runs/{run_id}/style")
async def style(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    body: StyleRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plots.start_style(db, project_id, run_id, body.style, body.request_id, user)


@router.post("/{draft_id}/runs")
async def start(
    project_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: Start,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plots.start(db, project_id, draft_id, body.revision, body.request_id, user)


@router.get("/runs")
async def listing(
    project_id: uuid.UUID,
    draft_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return {"runs": await plots.list_runs(db, project_id, draft_id, user)}


@router.get("/runs/{run_id}")
async def read(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plots.get_run(db, project_id, run_id, user)


@router.post("/runs/{run_id}/cancel")
async def cancel(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await plots.cancel(db, project_id, run_id, user)


@router.get("/runs/{run_id}/files/{extension}")
async def download(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    extension: Literal["svg", "png", "pdf", "py"],
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    content, mime = await plots.artifact(db, project_id, run_id, extension, user)
    return Response(
        content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="plot-{run_id}.{extension}"'},
    )


@router.get("/runs/{run_id}/package")
async def package(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    try:
        content = await plot_packages.export(db, project_id, run_id, user, request.is_disconnected)
    except MLResourceError as error:
        code = error.code
        message = {
            "ML_BUSY": "正在处理其他计算，请稍后导出。",
            "ML_CANCELLED": "导出已取消。",
            "ML_TIMEOUT": "导出超时，请减少所选字段后重新运行。",
            "ML_OUTPUT_LIMIT": "数据副本过大，请减少所选字段后重新运行。",
        }.get(code, "数据副本无法生成，请重新运行后导出。")
        raise StatForgeError(409, code, message) from None
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="plot-{run_id}.zip"'},
    )
