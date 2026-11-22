"""Saved project history, source selection and report documents."""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.dataset import Dataset
from app.models.user import User
from app.models.workspace import WorkspaceRecord, WorkspaceRevision
from app.preview.context import prepare_context
from app.preview.workspace import save_record
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import canonical_bytes, logical_hash
from app.schemas.preview import PreviewScope
from app.schemas.statistics import StatisticalRunRequest
from app.schemas.workspace import (
    AnalysisDraft,
    HistoryCopyReceipt,
    HistoryKind,
    HistoryLabel,
    ReportDocument,
    SaveRecord,
)
from app.services import project_research as research
from app.services.workflow_service import check_project_access

router = APIRouter(prefix="/api/v1/projects/{project_id}/research", tags=["project-research"])


@router.get("/history")
async def history(
    project_id: uuid.UUID,
    q: str = Query(default="", max_length=200),
    kind: HistoryKind | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await research.history(db, project_id, user, q, kind, offset, limit)


@router.get("/source/{kind}/{source_id}")
async def source(
    project_id: uuid.UUID,
    kind: HistoryKind,
    source_id: uuid.UUID,
    revision: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await research.source(db, project_id, user, kind, source_id, revision)


@router.get("/assets")
async def assets(
    project_id: uuid.UUID,
    q: str = Query(default="", max_length=200),
    kind: HistoryKind | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    listing = await research.history(db, project_id, user, q, kind, offset, limit, reportable=True)
    output = []
    for item in listing["items"]:
        if item["kind"] not in research.REPORTABLE:
            continue
        if item["kind"] not in {"figure", "citation"} and item["status"] not in {
            "completed",
            "completed_with_errors",
        }:
            continue
        try:
            resolved = await research.source(db, project_id, user, item["kind"], item["id"])
            output.append(
                {
                    **item,
                    "reference": resolved["reference"],
                    "available": not bool(resolved.get("issue_code")),
                    "issue_code": resolved.get("issue_code"),
                }
            )
        except research.StatForgeError:
            output.append({**item, "available": False})
    return {**listing, "items": output}


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)


@router.put("/history/{kind}/{source_id}/title")
async def rename(
    project_id: uuid.UUID,
    kind: HistoryKind,
    source_id: uuid.UUID,
    body: Rename,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await acquire_write_fence(db, user.id)
    await check_project_access(db, project_id, user)
    if not body.title.strip():
        raise research.failure("TITLE_EMPTY", 422)
    current = (await research.labels(db, project_id, user)).get(f"{kind}:{source_id}")
    if (current["revision"] if current else 0) != body.expected_revision:
        raise research.failure("TITLE_CHANGED")
    return await save_record(
        db,
        user,
        SaveRecord(
            project_id=project_id,
            expected_revision=body.expected_revision,
            payload=HistoryLabel(title=body.title.strip(), target_kind=kind, target_id=source_id),
        ),
        uuid.UUID(current["record_id"]) if current else None,
    )


@router.get("/reports/{report_id}/sources")
async def report_sources(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    revision: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await research.source(db, project_id, user, "report", report_id, revision)
    record = source["payload"]["record"]
    if record["payload"]["kind"] != "report":
        raise research.failure("REPORT_NOT_FOUND", 404)
    return await research.report_sources(
        db, ReportDocument.model_validate(record["payload"]), project_id, user
    )


@router.get("/reports/{report_id}/source/{block_id}")
async def report_block_source(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    block_id: uuid.UUID,
    revision: int = Query(ge=1),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await research.source(db, project_id, user, "report", report_id, revision)
    record = source["payload"]["record"]
    if record["payload"]["kind"] != "report":
        raise research.failure("REPORT_NOT_FOUND", 404)
    report = ReportDocument.model_validate(record["payload"])
    block = next((item for item in report.blocks if item.id == block_id and item.source), None)
    if block is None:
        raise research.failure("REPORT_SOURCE_NOT_FOUND", 404)
    resolved = await research.report_sources(
        db, report.model_copy(update={"blocks": [block]}), project_id, user
    )
    item = resolved.get(str(block_id))
    if not item or not item.get("available"):
        raise research.failure()
    return item


class Continue(BaseModel):
    request_id: uuid.UUID
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=1)


def continuation_location(project_id, kind, identity, output_kind="workspace", dataset_id=None):
    if output_kind == "ml_draft":
        return f"/projects/{project_id}/machine-learning?draftId={identity}&datasetId={dataset_id}"
    if output_kind == "workflow":
        return f"/projects/{project_id}/workflow?workflow={identity}"
    query = f"view=plot&plotId={identity}" if kind in {"plot", "plot_run"} else f"object={identity}"
    return f"/projects/{project_id}/workspace?{query}"


@router.post("/history/{kind}/{source_id}/continue")
async def continue_analysis(
    project_id: uuid.UUID,
    kind: HistoryKind,
    source_id: uuid.UUID,
    body: Continue,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    await acquire_write_fence(db, user.id)
    await check_project_access(db, project_id, user)
    input_hash = logical_hash({"kind": kind, "id": str(source_id), **body.model_dump(mode="json")})
    receipt_id = uuid.uuid5(user.id, f"history-copy:{project_id}:{body.request_id}")
    receipt = await db.get(WorkspaceRecord, receipt_id)
    if receipt:
        if (
            receipt.user_id != user.id
            or receipt.project_id != project_id
            or receipt.kind != "history_copy"
        ):
            raise research.failure("COPY_UNAVAILABLE")
        prior = HistoryCopyReceipt.model_validate_json(receipt.payload_json)
        if prior.input_sha256 != input_hash:
            raise research.failure("COPY_CHANGED")
        return {
            "href": continuation_location(
                project_id, kind, prior.output_id, prior.output_kind, prior.dataset_id
            ),
            "record_id": str(prior.output_id),
        }
    resolved = await research.source(db, project_id, user, kind, source_id, body.revision)
    if resolved["reference"]["sha256"] != body.source_sha256:
        raise research.failure("SOURCE_CHANGED")
    payload = resolved["payload"]
    output_kind = "workspace"
    if kind == "ml" or (
        kind == "analysis"
        and any(
            payload.get("record", {}).get("payload", {}).get(key) for key in ("ml", "ml_editor")
        )
    ):
        from app.services.history_continuation import ml_copy

        copy = await ml_copy(db, project_id, user, resolved)
        output_kind = "ml_draft"
    elif kind == "workflow":
        from app.services.history_continuation import workflow_copy

        workflow = await workflow_copy(db, project_id, user, resolved)
        saved = {"id": str(workflow.id)}
        output_kind = "workflow"
    elif kind in {"plot", "plot_run"}:
        from app.schemas.workspace import PlotDraft

        original = payload["record"]["payload"]
        context = await prepare_context(
            db,
            PreviewScope(
                project_id=project_id,
                dataset_id=resolved["dataset_id"],
                dataset_sha256=resolved["dataset_sha256"],
                columns=original["scope"]["columns"],
            ),
            user,
        )
        copy = PlotDraft(
            title=original["title"][:150] + "（副本）",
            scope=context["scope"],
            code=original["code"],
            dpi=original["dpi"],
        )
    elif kind in {"analysis", "figure"} and not resolved["archived"]:
        from app.schemas.workspace import RECORD_ADAPTER

        copy = RECORD_ADAPTER.validate_python(
            {
                **payload["record"]["payload"],
                "title": payload["record"]["payload"]["title"][:150] + "（副本）",
            }
        )
    elif kind in {"statistics", "figure", "analysis"}:
        run = payload["run"] if kind == "figure" else payload
        if kind == "analysis":
            draft = payload["record"]["payload"]
            configuration, title = draft.get("statistics"), draft["title"]
        else:
            configuration, title = run.get("input_config"), run.get("method", "分析")
        if not configuration or str(configuration.get("method_id", "")).startswith("evaluate_"):
            raise research.failure("CONTINUE_IN_RESEARCH_PLAN", 422)
        configuration = dict(configuration)
        if "missing_policy" in configuration:
            configuration["missing_value_policy"] = configuration.pop("missing_policy")
        request = StatisticalRunRequest.model_validate(configuration)
        dataset = (
            await db.get(Dataset, uuid.UUID(resolved["dataset_id"]))
            if resolved["dataset_id"]
            else None
        )
        if (
            not dataset
            or dataset.project_id != project_id
            or dataset.sha256 != resolved["dataset_sha256"]
        ):
            raise research.failure("SOURCE_CHANGED")
        fields = list(
            dict.fromkeys(
                [
                    *request.variables,
                    *request.categorical_variables,
                    *([request.group_variable] if request.group_variable else []),
                    *([request.target] if request.target else []),
                ]
            )
        )
        context = await prepare_context(
            db,
            PreviewScope(
                project_id=project_id,
                dataset_id=dataset.id,
                dataset_sha256=dataset.sha256,
                columns=fields,
            ),
            user,
        )
        copy = AnalysisDraft(
            title=title[:150] + "（副本）", scope=context["scope"], statistics=request.model_dump()
        )
    else:
        raise research.failure("OPEN_ORIGINAL_RECORD", 422)
    if output_kind != "workflow":
        saved = await save_record(
            db, user, SaveRecord(project_id=project_id, payload=copy), commit=False
        )
    proof = HistoryCopyReceipt(
        title="继续分析",
        input_sha256=input_hash,
        output_id=uuid.UUID(saved["id"]),
        output_kind=output_kind,
        dataset_id=resolved["dataset_id"] if output_kind == "ml_draft" else None,
    )
    raw = canonical_bytes(proof.model_dump(mode="json")).decode()
    db.add(
        WorkspaceRecord(
            id=receipt_id,
            user_id=user.id,
            project_id=project_id,
            kind="history_copy",
            payload_json=raw,
        )
    )
    await db.flush()
    db.add(WorkspaceRevision(record_id=receipt_id, revision=1, payload_json=raw))
    await db.flush()
    return {
        "href": continuation_location(project_id, kind, saved["id"], output_kind, proof.dataset_id),
        "record_id": saved["id"],
    }
