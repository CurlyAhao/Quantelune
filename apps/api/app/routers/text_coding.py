import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.models.user import User
from app.research.draft_advice import load_coding_context
from app.routers.preview import safe_body
from app.schemas.coding import CodingChange, CodingDocument
from app.schemas.coding_agreement import AgreementRequest
from app.schemas.coding_attributes import AttributeBindingRequest
from app.schemas.coding_matrix import MatrixRequest
from app.schemas.coding_relations import RelationsRequest
from app.schemas.draft_advice import CodingAdviceContextRequest
from app.services import coding_suggestions, text_coding
from app.services.survey_import import Mapping as SurveyMapping

router = APIRouter(prefix="/api/v1/projects/{project_id}/coding", tags=["text-coding"])


@router.get("/survey/{dataset_id}/options")
async def survey_options(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import survey_import

    return await survey_import.options(db, project_id, user, dataset_id)


@router.post("/survey/preview")
async def survey_preview(
    project_id: uuid.UUID,
    body: SurveyMapping,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import survey_import

    return await survey_import.preview(db, project_id, user, body)


@router.post("/survey/import")
async def survey_import(
    project_id: uuid.UUID,
    body: SurveyMapping,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import survey_import

    return await survey_import.apply(db, project_id, user, body)


@router.get("/survey/{dataset_id}/row/{row}")
async def survey_source_row(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    row: int,
    sha256: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import survey_import

    return await survey_import.source_row(db, project_id, user, dataset_id, row, sha256)


@router.post("/matrix")
async def matrix(
    project_id: uuid.UUID,
    body: MatrixRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_matrix

    return await coding_matrix.analyze(db, project_id, user, body)


@router.post("/matrix/save")
async def matrix_save(
    project_id: uuid.UUID,
    body: MatrixRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_matrix

    return await coding_matrix.save(db, project_id, user, body)


@router.get("/matrix/history")
async def matrix_history(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_matrix

    return await coding_matrix.history(db, project_id, user)


@router.get("/matrix/saved/{dataset_id}")
async def matrix_snapshot(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_matrix

    return await coding_matrix.snapshot(db, project_id, user, dataset_id)


@router.post("/matrix/export")
async def matrix_export(
    project_id: uuid.UUID,
    body: MatrixRequest,
    format: Literal["matrix", "evidence"],
    snapshot_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_matrix

    data = (
        await coding_matrix.snapshot(db, project_id, user, snapshot_id)
        if snapshot_id
        else await coding_matrix.analyze(db, project_id, user, body)
    )
    return {
        "csv": coding_matrix.export(data, format).decode("utf-8"),
        "filename": "证据矩阵.csv" if format == "matrix" else "矩阵证据明细.csv",
    }


@router.get("/agreement/choices")
async def agreement_choices(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_agreement

    return await coding_agreement.choices(db, project_id, user)


@router.post("/agreement")
async def agreement(
    project_id: uuid.UUID,
    body: AgreementRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_agreement

    return await coding_agreement.analyze(db, project_id, user, body)


@router.post("/agreement/export")
async def agreement_export(
    project_id: uuid.UUID,
    body: AgreementRequest,
    snapshot_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_agreement

    data = (
        await coding_agreement.snapshot(db, project_id, user, snapshot_id)
        if snapshot_id
        else await coding_agreement.analyze(db, project_id, user, body, full=True)
    )
    return coding_agreement.export_result(data)


@router.post("/agreement/save")
async def agreement_save(
    project_id: uuid.UUID,
    body: AgreementRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_agreement

    return await coding_agreement.save(db, project_id, user, body)


@router.get("/agreement/history")
async def agreement_history(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_agreement

    return await coding_agreement.history(db, project_id, user)


@router.get("/agreement/saved/{dataset_id}")
async def agreement_snapshot(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    page: int = 0,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_agreement

    return coding_agreement.paginated(
        await coding_agreement.snapshot(db, project_id, user, dataset_id), max(page, 0)
    )


@router.post("/relations")
async def relations(
    project_id: uuid.UUID,
    body: RelationsRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_relations

    return await coding_relations.analyze(db, project_id, user, body)


@router.post("/relations/export")
async def relations_export(
    project_id: uuid.UUID,
    body: RelationsRequest,
    format: Literal["matrix", "relations"],
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_relations

    return await coding_relations.analyze(db, project_id, user, body, format)


@router.get("/attributes/catalog")
async def attributes_catalog(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_attributes

    return await coding_attributes.catalog(db, project_id, user)


@router.post("/attributes/preview")
async def attributes_preview(
    project_id: uuid.UUID,
    body: AttributeBindingRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_attributes

    return await coding_attributes.preview_binding(db, project_id, user, body)


@router.get("/attributes/tables/{dataset_id}")
async def attribute_columns(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_attributes

    return await coding_attributes.table_columns(db, project_id, user, dataset_id)


@router.get("/scope")
async def coding_scope(
    project_id: uuid.UUID,
    revision: int,
    case_collection_id: uuid.UUID | None = None,
    case_sample_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import coding_attributes

    state = await text_coding.read(db, project_id, user)
    if state["revision"] != revision:
        raise text_coding.fail("标注已变化，请刷新后再查看。")
    case = await text_coding.selected_case(
        db, project_id, user, state, case_collection_id, case_sample_id
    )
    return coding_attributes.scoped(state, CodingDocument.model_validate(state["payload"]), case)


@router.get("")
async def read(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await text_coding.read(db, project_id, user)


@router.get("/summary/{dataset_id}/snapshot")
async def summary_snapshot(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await text_coding.summary_snapshot(db, project_id, user, dataset_id)


@router.post("/advice-context")
async def advice_context(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    body = await safe_body(request, CodingAdviceContextRequest)
    try:
        context = await load_coding_context(db, project_id, user, body)
    except ValueError:
        raise StatForgeError(
            422, "ADVICE_CONTEXT_INVALID", "请减少所选内容并重新选择原文。"
        ) from None
    return {"source_version": context.source_version, "context": context.document}


@router.post("/selection-summary")
async def selection_summary(
    project_id: uuid.UUID,
    body: coding_suggestions.ThemeSuggestionInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await coding_suggestions.suggest(db, project_id, user, body, request, "summary")


@router.post("/theme-suggestions")
async def theme_suggestions(
    project_id: uuid.UUID,
    body: coding_suggestions.ThemeSuggestionInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await coding_suggestions.suggest(db, project_id, user, body, request)


@router.post("")
async def apply(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await text_coding.apply(
        db, project_id, user, await safe_body(request, CodingChange, max_bytes=4 * 1024 * 1024)
    )


@router.get("/export")
async def export(
    project_id: uuid.UUID,
    format: Literal["csv", "json", "summary_csv", "qdc"] = "csv",
    case_collection_id: uuid.UUID | None = None,
    case_sample_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    if format == "qdc":
        from app.schemas.coding import CodingDocument
        from app.services import refi_codebook

        state = await text_coding.read(db, project_id, user)
        data = refi_codebook.export(CodingDocument.model_validate(state["payload"]).themes)
        mime = "application/xml"
    else:
        data, mime = await text_coding.export(
            db, project_id, user, format, case_collection_id, case_sample_id
        )
    return Response(
        data,
        media_type=mime,
        headers={
            ("Content-Disposition"): f'attachment; \
filename="coding.{format if format in {("json"), ("qdc")} else ("csv")}"'
        },
    )


class ReportRequest(BaseModel):
    revision: int = Field(ge=1)


class SummaryRequest(BaseModel):
    revision: int = Field(ge=1)
    task_id: uuid.UUID | None = None
    case_collection_id: uuid.UUID | None = None
    case_sample_id: str | None = Field(default=None, min_length=1, max_length=1000)


@router.post("/summary-dataset")
async def summary_dataset(
    project_id: uuid.UUID,
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await text_coding.summary_dataset(
        db,
        project_id,
        user,
        body.revision,
        body.task_id,
        body.case_collection_id,
        body.case_sample_id,
    )


@router.post("/segments/{segment_id}/report")
async def report(
    project_id: uuid.UUID,
    segment_id: uuid.UUID,
    body: ReportRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await text_coding.report(db, project_id, user, segment_id, body.revision)
