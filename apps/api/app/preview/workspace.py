"""CAS, current ACL/SHA checks and finite explicit domain plan execution."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.material import Material
from app.models.preview import PreviewTurn
from app.models.user import User
from app.models.workspace import WorkspaceExecution, WorkspaceRecord, WorkspaceRevision
from app.preview import vault as credentials
from app.preview.configuration import get_config
from app.preview.context import (
    canonical,
    digest,
    load_dataset,
    resolve_context,
    schema_snapshot,
    verify_storage_version,
)
from app.preview.security import contains_secret
from app.preview.write_fence import acquire_write_fence
from app.routers import ml as ml_routes
from app.routers import statistics as stat_routes
from app.schemas.analysis_jobs import AnalysisJob
from app.schemas.audio_spectrum import SpectrumRun
from app.schemas.coding import CodingDocument
from app.schemas.media_runs import MediaRun
from app.schemas.preview import PreviewScope
from app.schemas.textgrid import TextGridDocument
from app.schemas.voice_quality import VoiceQualityRun
from app.schemas.workspace import (
    RECORD_ADAPTER,
    AnalysisDraft,
    BindTemplate,
    DataWorkspaceState,
    ExecutePlan,
    FigureData,
    FigureSpec,
    HistoryCopyReceipt,
    HistoryLabel,
    ObjectRef,
    PlanBody,
    PlotCodeFile,
    PlotDraft,
    PlotRun,
    ReportDocument,
    ResearchInputState,
    ResearchTask,
    SaveRecord,
    SessionBody,
    SessionMessage,
    TemplateBody,
    WorkspaceState,
)
from app.services.identity_service import find_user
from app.services.workflow_service import check_dataset_access, check_project_access


def rejected(code: str = "WORKSPACE_REFERENCE_UNAVAILABLE", status: int = 409) -> StatForgeError:
    return StatForgeError(status, code, "Workspace input changed or is unavailable.")


async def credential_free(db: AsyncSession, user: User, value: Any) -> None:
    config = await get_config(db, user.id)
    if config and config.key_configured:
        secret = credentials.vault.get(config.vault_namespace, user.id)
        if secret and contains_secret(value, secret):
            raise rejected("PREVIEW_SECRET_INPUT", 422)
    # Model settings now uses the formal journal. Scan only this owner's known
    # credential namespaces; no OS enumeration and no secret leaves this call.
    from app.models.research_provider import ResearchProviderConfiguration
    from app.research.egress import credentials as has_credentials
    from app.services.research_gateway_service import get_gateway

    namespaces = (
        await db.scalars(
            select(ResearchProviderConfiguration.namespace).where(
                ResearchProviderConfiguration.owner_id == user.id,
                ResearchProviderConfiguration.key_state != "deleted",
            )
        )
    ).all()
    for namespace in namespaces:
        try:
            secret = get_gateway().vault.get(namespace, user.id)
        except Exception:
            raise rejected("WORKSPACE_CREDENTIAL_UNAVAILABLE", 503) from None
        if secret and has_credentials(value, (secret,)):
            raise rejected("PREVIEW_SECRET_INPUT", 422)


async def load_record(
    db: AsyncSession, record_id: uuid.UUID, user: User, project_id: uuid.UUID | None = None
) -> WorkspaceRecord:
    # Authorization scalars precede materializing any potentially sensitive payload.
    identity = (
        await db.execute(
            select(WorkspaceRecord.user_id, WorkspaceRecord.project_id).where(
                WorkspaceRecord.id == record_id
            )
        )
    ).one_or_none()
    if identity is None or identity.user_id != user.id:
        raise rejected("WORKSPACE_NOT_FOUND", 404)
    if project_id is not None and identity.project_id != project_id:
        raise rejected("WORKSPACE_NOT_FOUND", 404)
    await check_project_access(db, identity.project_id, user)
    row = await db.get(WorkspaceRecord, record_id, populate_existing=True)
    assert row is not None
    return row


async def validate_scope(db: AsyncSession, scope: PreviewScope, project: uuid.UUID, user: User):
    if scope.project_id != project:
        raise rejected()
    return await resolve_context(db, scope, user)


async def validate_object(db: AsyncSession, obj: ObjectRef, project: uuid.UUID, user: User):
    if obj.kind in {"analysis", "figure"}:
        row = await load_record(db, obj.id, user, project)
        payload = RECORD_ADAPTER.validate_json(row.payload_json)
        if payload.kind != obj.kind:
            raise rejected()
        await validate_payload(db, payload, project, user)
    elif obj.kind == "data":
        await check_dataset_access(db, project, obj.id, user)
    elif obj.kind == "material":
        identity = (
            await db.execute(
                select(Material.user_id, Material.project_id).where(Material.id == obj.id)
            )
        ).one_or_none()
        if identity is None or identity.user_id != user.id or identity.project_id != project:
            raise rejected()
    else:
        # Legacy Run/report links retain their existing ACL APIs.
        raise rejected()


async def validated_session_messages(
    db: AsyncSession,
    payload: SessionBody,
    project: uuid.UUID,
    user: User,
    *,
    reject_unfinished: bool,
) -> list[SessionMessage]:
    if payload.scope.session_id is not None or payload.scope.session_revision is not None:
        raise rejected("WORKSPACE_SESSION_RECURSION", 422)
    await validate_scope(db, payload.scope, project, user)
    finished = []
    for message in payload.messages:
        if message.scope.project_id != project:
            raise rejected()
        from app.preview.extensions import without_session

        await validate_scope(db, without_session(message.scope), project, user)
        turn = await db.scalar(
            select(PreviewTurn)
            .where(
                PreviewTurn.id == message.turn_id,
                PreviewTurn.user_id == user.id,
                PreviewTurn.project_id == project,
            )
            .execution_options(populate_existing=True)
        )
        if (
            turn is None
            or turn.request_id != message.request_id
            or canonical(message.scope.model_dump(mode="json")) != turn.scope_json
            or message.question != json.loads(turn.authorization_json).get("question")
        ):
            raise rejected()
        if turn.status != "finished":
            if reject_unfinished:
                raise rejected("WORKSPACE_HISTORY_TURN_UNFINISHED")
            continue
        finished.append(message)
    return finished


async def validate_payload(db: AsyncSession, payload, project: uuid.UUID, user: User) -> None:
    from app.schemas.textgrid import TextGridDocument

    if isinstance(payload, TextGridDocument):
        from app.services.textgrid import validate_binding

        await validate_binding(db, payload, project, user)
    elif isinstance(payload, PlotDraft):
        if payload.scope.project_id != project:
            raise rejected()
        await load_dataset(db, payload.scope, user)
    elif isinstance(payload, HistoryLabel):
        from app.services.project_research import source

        await source(db, project, user, payload.target_kind, payload.target_id)
    elif isinstance(payload, ReportDocument):
        from app.services.project_research import validate_report

        await validate_report(db, payload, project, user)
    elif isinstance(payload, ResearchTask):
        for dataset_id in payload.dataset_ids:
            await check_dataset_access(db, project, dataset_id, user)
        if payload.dataset_id:
            await check_dataset_access(db, project, payload.dataset_id, user)
            from app.models.dataset import Dataset, UploadStatus

            chosen = await db.get(Dataset, payload.dataset_id)
            if chosen is None or not (
                chosen.upload_status == UploadStatus.READY
                or (chosen.raw_file and chosen.upload_status == UploadStatus.UPLOADED)
            ):
                raise rejected("DATASET_NOT_PREPARED")
        for object_id, expected_kind in [
            *((item, "analysis") for item in payload.analysis_ids),
            *((item, "figure") for item in payload.figure_ids),
            *((item, "plot_run") for item in payload.plot_run_ids),
        ]:
            item = await load_record(db, object_id, user)
            if item.project_id != project or item.kind != expected_kind:
                raise rejected()
        if payload.archived_analysis_ids or payload.archived_figure_ids:
            from app.services.project_research import source

            for object_id, expected_kind in [
                *((item, "analysis") for item in payload.archived_analysis_ids),
                *((item, "figure") for item in payload.archived_figure_ids),
            ]:
                await source(db, project, user, expected_kind, object_id)
    elif isinstance(payload, ResearchInputState):
        from app.services import research_service as ledger
        from app.services.dataset_service import get_dataset_with_access

        if payload.workspace_id:
            scope = await ledger.workspace(db, payload.workspace_id, user.id)
            if scope.project_id != project:
                raise rejected()
        if payload.session_id:
            _, scope = await ledger.session(db, payload.session_id, user.id)
            if scope.id != payload.workspace_id or scope.project_id != project:
                raise rejected()
        if payload.dataset_id:
            await check_dataset_access(db, project, payload.dataset_id, user)
        for dataset_id in payload.columns:
            await get_dataset_with_access(db, dataset_id, user.id)
    elif isinstance(payload, DataWorkspaceState):
        await check_dataset_access(db, project, payload.dataset_id, user)
    elif isinstance(payload, FigureData):
        from app.preview.figure_data import validate_snapshot

        await validate_snapshot(db, payload, project, user)
    elif isinstance(payload, FigureSpec) and payload.schema_version == 2:
        from app.preview.figure_data import validate_figure

        await validate_figure(db, payload, project, user)
    elif isinstance(payload, WorkspaceState):
        for obj in payload.tabs + ([payload.selected] if payload.selected else []):
            await validate_object(db, obj, project, user)
    elif isinstance(payload, (AnalysisDraft, PlanBody, FigureSpec)):
        await validate_scope(db, payload.scope, project, user)
        if payload.scope.dataset_id is None:
            raise rejected()
        dataset = await load_dataset(db, payload.scope, user)
        if isinstance(payload, FigureSpec):
            await verify_storage_version(dataset)
        if isinstance(payload, AnalysisDraft):
            if payload.scope.statistical_run_id:
                await verify_storage_version(dataset)
            if not set(payload.measurement_scales) <= set(payload.scope.columns):
                raise rejected()
            if payload.ml_editor is not None:
                editor = payload.ml_editor
                used_editor = [
                    editor.target,
                    *editor.features,
                    *editor.categorical_features,
                    editor.group_variable,
                    editor.time_variable,
                ]
                names = {column["name"] for column in schema_snapshot(dataset.schema_json)}
                if not {value for value in used_editor if value} <= names:
                    raise rejected("ML_DRAFT_INPUT", 422)
            if payload.ml is not None:
                form = payload.ml
                used = [form.target, *form.features, *form.categorical_features]
                used += [value for value in (form.group_variable, form.time_variable) if value]
                valid = await ml_routes.ml_validate(body=form, current_user=user)
                if (
                    not set(used)
                    <= {column["name"] for column in schema_snapshot(dataset.schema_json)}
                    or not valid["valid"]
                ):
                    raise rejected("ML_DRAFT_INPUT", 422)
        if isinstance(payload, PlanBody):
            for step in payload.steps:
                scope = payload.scope.model_copy(
                    update={"statistical_config": step.statistics, "ml_config": step.ml}
                )
                await validate_scope(db, scope, project, user)
        if isinstance(payload, FigureSpec):
            if not payload.scope.statistical_run_id or payload.scope.ml_run_id:
                raise rejected()
            run = await stat_routes.get_statistical_run(payload.scope.statistical_run_id, db, user)
            result = run.result
            if (
                run.project_id != project
                or run.dataset_id != dataset.id
                or run.dataset_sha256 != dataset.sha256
                or run.method != "welch_t"
                or run.status.value != "completed"
                or result is None
                or result.status != "completed"
                or payload.group_order != list(result.descriptive_statistics)
                or payload.fields != (run.input_config or {}).get("variables")
                or ("difference_ci" in payload.layers and not result.confidence_interval)
            ):
                raise rejected()
    elif isinstance(payload, TemplateBody):
        PlanBody(scope=PreviewScope(project_id=project), steps=payload.steps, title=payload.title)
    elif isinstance(payload, SessionBody):
        await validated_session_messages(db, payload, project, user, reject_unfinished=True)


async def read_record(
    db: AsyncSession, record_id: uuid.UUID, user: User, revision: int | None = None
):
    row = await load_record(db, record_id, user)
    raw = row.payload_json
    selected_revision = row.revision
    if revision is not None:
        version = await db.scalar(
            select(WorkspaceRevision).where(
                WorkspaceRevision.record_id == row.id, WorkspaceRevision.revision == revision
            )
        )
        if version is None:
            raise rejected("WORKSPACE_REVISION_NOT_FOUND", 404)
        raw, selected_revision = version.payload_json, revision
    try:
        payload = RECORD_ADAPTER.validate_json(raw)
    except ValueError:
        raise rejected("WORKSPACE_SCHEMA_UNSUPPORTED") from None
    if isinstance(payload, WorkspaceState):
        valid_tabs = []
        for tab in payload.tabs:
            try:
                await validate_object(db, tab, row.project_id, user)
                valid_tabs.append(tab)
            except StatForgeError:
                pass
        selected = payload.selected
        if selected is not None:
            try:
                await validate_object(db, selected, row.project_id, user)
            except StatForgeError:
                selected = valid_tabs[-1] if valid_tabs else None
        payload = payload.model_copy(update={"tabs": valid_tabs, "selected": selected})
    elif isinstance(payload, SessionBody):
        # Old saved revisions remain immutable. Exclude unfinished turns from the
        # returned history while still checking every stored reference and source.
        messages = await validated_session_messages(
            db, payload, row.project_id, user, reject_unfinished=False
        )
        payload = payload.model_copy(update={"messages": messages})
    elif not isinstance(
        payload,
        (
            ReportDocument,
            PlotDraft,
            PlotRun,
            MediaRun,
            SpectrumRun,
            VoiceQualityRun,
            TextGridDocument,
        ),
    ):
        await validate_payload(db, payload, row.project_id, user)
    if isinstance(payload, ReportDocument):
        from app.models.project import Project
        from app.services import report_sources

        await credential_free(db, user, payload.model_dump(mode="json"))
        project = await db.get(Project, row.project_id)
        for block in payload.blocks:
            if (
                block.source
                and block.source_snapshot is not None
                and not report_sources.external(block.source_snapshot)
            ):
                # Cache an old inline frame without rewriting its frozen row.
                # The editor receives a reusable reference, including in undo.
                try:
                    block.source_snapshot = report_sources.store(
                        block.source_snapshot, block.source, project, user
                    )
                except (StatForgeError, OSError):
                    pass  # An unavailable source must not hide authored text.
    await credential_free(db, user, payload.model_dump(mode="json"))
    return {
        "id": str(row.id),
        "owner_id": str(row.user_id),
        "project_id": str(row.project_id),
        "revision": selected_revision,
        "payload": payload.model_dump(mode="json"),
    }


async def save_record(
    db: AsyncSession,
    user: User,
    body: SaveRecord,
    record_id: uuid.UUID | None = None,
    *,
    commit: bool = True,
    retained_sources: set[str] | None = None,
    coding_write: bool = False,
    textgrid_write: bool = False,
    journal_write: bool = False,
    memo_write: bool = False,
):
    from app.schemas.journal import JournalDocument, ProjectMemoDocument

    if isinstance(body.payload, JournalDocument) and not journal_write:
        raise rejected("JOURNAL_USE_COMMAND", 422)
    if isinstance(body.payload, ProjectMemoDocument) and not memo_write:
        raise rejected("MEMO_USE_COMMAND", 422)
    from app.schemas.textgrid import TextGridDocument

    if isinstance(body.payload, TextGridDocument) and not textgrid_write:
        raise rejected("TEXTGRID_USE_COMMAND", 422)
    if isinstance(body.payload, CodingDocument) and not coding_write:
        raise rejected("CODING_USE_COMMAND", 422)
    if isinstance(
        body.payload,
        (
            FigureData,
            HistoryCopyReceipt,
            PlotRun,
            PlotCodeFile,
            AnalysisJob,
            MediaRun,
            SpectrumRun,
            VoiceQualityRun,
        ),
    ):
        raise rejected("FIGURE_DATA_IMMUTABLE", 422)
    if isinstance(body.payload, ReportDocument) or (
        record_id is not None and isinstance(body.payload, (PlanBody, PlotDraft))
    ):
        # Held through validation, the active execution check and CAS commit.
        await acquire_write_fence(db, user.id)
    await check_project_access(db, body.project_id, user)
    await credential_free(db, user, body.model_dump(mode="json"))
    if isinstance(body.payload, ReportDocument):
        from app.services.project_research import validate_report

        allowed = set(retained_sources or ())
        retained_snapshots = {}
        if record_id:
            previous = await load_record(db, record_id, user, body.project_id)
            if previous.kind == "report":
                old_report = ReportDocument.model_validate_json(previous.payload_json)
                retained_snapshots = {
                    block.source.model_dump_json(): block.source_snapshot
                    for block in old_report.blocks
                    if block.source
                }
        await validate_report(db, body.payload, body.project_id, user, allowed, retained_snapshots)
    elif isinstance(body.payload, PlotDraft) and record_id:
        # Keep authored code saveable after a source disappears. A changed
        # binding and every execution still require current dataset access.
        previous = await load_record(db, record_id, user, body.project_id)
        old = RECORD_ADAPTER.validate_json(previous.payload_json)
        if not isinstance(old, PlotDraft) or old.scope != body.payload.scope:
            await validate_payload(db, body.payload, body.project_id, user)
    else:
        await validate_payload(db, body.payload, body.project_id, user)
    raw = canonical(body.payload.model_dump(mode="json"))
    if record_id is None:
        if body.expected_revision != 0:
            raise rejected("WORKSPACE_REVISION_CONFLICT")
        row = WorkspaceRecord(
            user_id=user.id,
            project_id=body.project_id,
            kind=body.payload.kind,
            payload_json=raw,
            **(
                {"updated_at": datetime.now(UTC)}
                if isinstance(body.payload, ResearchInputState)
                else {}
            ),
        )
        db.add(row)
        await db.flush()
        db.add(WorkspaceRevision(record_id=row.id, revision=1, payload_json=raw))
    else:
        row = await load_record(db, record_id, user, body.project_id)
        if row.kind != body.payload.kind:
            raise rejected()
        if row.kind == "plan":
            await require_plan_idle(db, row.id)
        values: dict[str, Any] = {"revision": body.expected_revision + 1, "payload_json": raw}
        if isinstance(body.payload, ResearchInputState):
            # SQLite's CURRENT_TIMESTAMP has second precision. Rapid discussion
            # switches must restore the last chosen draft, including older rows.
            values["updated_at"] = datetime.now(UTC)
        changed = await db.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == row.id, WorkspaceRecord.revision == body.expected_revision)
            .values(**values)
            .returning(WorkspaceRecord.id)
        )
        if changed.scalar_one_or_none() is None:
            raise rejected("WORKSPACE_REVISION_CONFLICT")
        db.add(
            WorkspaceRevision(
                record_id=row.id, revision=body.expected_revision + 1, payload_json=raw
            )
        )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return await read_record(db, row.id, user)


async def instantiate(db: AsyncSession, user: User, template_id: uuid.UUID, body: BindTemplate):
    row = await load_record(db, template_id, user, body.scope.project_id)
    if row.kind != "template" or row.revision != body.revision:
        raise rejected("WORKSPACE_REVISION_CONFLICT")
    template = TemplateBody.model_validate_json(row.payload_json)
    dataset = await load_dataset(db, body.scope, user)
    columns = {c["name"]: c["dtype"] for c in schema_snapshot(dataset.schema_json)}
    if any(columns.get(name) != dtype for name, dtype in template.required_columns.items()):
        raise rejected("WORKSPACE_TEMPLATE_BINDING_INVALID")
    plan = PlanBody(
        title=body.title,
        scope=body.scope,
        steps=template.steps,
        research_question=template.research_question,
    )
    return await save_record(db, user, SaveRecord(project_id=row.project_id, payload=plan))


async def execution_response(db: AsyncSession, row: WorkspaceExecution, user: User):
    if row.user_id != user.id:
        raise rejected("WORKSPACE_NOT_FOUND", 404)
    await check_project_access(db, row.project_id, user)
    saved_plan = await read_record(db, row.plan_id, user, row.plan_revision)
    plan = PlanBody.model_validate(saved_plan["payload"])
    refs = json.loads(row.results_json)
    for ref in refs:
        scope = plan.scope.model_copy(
            update={
                "statistical_run_id": uuid.UUID(ref["statistical_run_id"])
                if ref.get("statistical_run_id")
                else None,
                "ml_run_id": uuid.UUID(ref["ml_run_id"]) if ref.get("ml_run_id") else None,
                "statistical_config": None,
                "ml_config": None,
            }
        )
        await resolve_context(db, scope, user)
    return {
        "id": str(row.id),
        "request_id": row.request_id,
        "plan_id": str(row.plan_id),
        "plan_revision": row.plan_revision,
        "status": row.status,
        "results": refs,
        "error_code": row.error_code,
    }


async def execute_plan(db: AsyncSession, user: User, plan_id: uuid.UUID, body: ExecutePlan):
    user_id = user.id
    input_hash = digest({"plan_id": str(plan_id), **body.model_dump(mode="json")})
    previous = await db.scalar(
        select(WorkspaceExecution).where(
            WorkspaceExecution.user_id == user_id, WorkspaceExecution.request_id == body.request_id
        )
    )
    if previous:
        if previous.input_hash != input_hash:
            raise rejected("WORKSPACE_REQUEST_CONFLICT")
        return await execution_response(db, previous, user)
    await acquire_write_fence(db, user_id)
    # An exact request may have been persisted while this transaction waited.
    previous = await db.scalar(
        select(WorkspaceExecution).where(
            WorkspaceExecution.user_id == user_id, WorkspaceExecution.request_id == body.request_id
        )
    )
    if previous:
        if previous.input_hash != input_hash:
            raise rejected("WORKSPACE_REQUEST_CONFLICT")
        return await execution_response(db, previous, user)
    row = await load_record(db, plan_id, user)
    if row.kind != "plan" or row.revision != body.revision or not body.approve_local_execution:
        raise rejected("WORKSPACE_EXECUTION_APPROVAL_REQUIRED", 403)
    await require_plan_idle(db, plan_id)
    plan = PlanBody.model_validate_json(row.payload_json)
    await validate_payload(
        db, plan, row.project_id, user
    )  # all steps before any domain side effect
    dataset = await load_dataset(db, plan.scope, user)
    await verify_storage_version(dataset)
    execution = WorkspaceExecution(
        user_id=user_id,
        project_id=row.project_id,
        plan_id=row.id,
        plan_revision=row.revision,
        request_id=body.request_id,
        input_hash=input_hash,
    )
    db.add(execution)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise rejected("WORKSPACE_REQUEST_IN_PROGRESS") from None
    execution_id = execution.id
    results = []
    try:
        for step in plan.steps:
            await acquire_write_fence(db, user_id)
            current_user = await find_user(db, user_id=user_id)
            if current_user is None or not current_user.is_active:
                raise rejected()
            current = await load_record(db, plan_id, current_user)
            if current.revision != body.revision:
                raise rejected("WORKSPACE_REVISION_CONFLICT")
            await validate_payload(db, plan, current.project_id, current_user)
            await verify_storage_version(await load_dataset(db, plan.scope, current_user))
            assert plan.scope.dataset_id is not None
            if step.statistics is not None:
                run = await stat_routes.run_statistics(
                    plan.scope.dataset_id, step.statistics, db, current_user
                )
                ref = {
                    "step_id": step.id,
                    "statistical_run_id": str(run.id),
                    "status": run.status.value,
                }
            else:
                assert step.ml is not None
                experiment = await ml_routes.create_experiment(
                    plan.scope.dataset_id, step.ml, db, current_user
                )
                run_ml = await ml_routes.run_experiment(experiment.id, db, current_user)
                ref = {
                    "step_id": step.id,
                    "ml_run_id": str(run_ml.id),
                    "experiment_id": str(experiment.id),
                    "status": run_ml.status.value,
                }
            results.append(ref)
            execution.results_json = canonical(results)
            if ref["status"] != "completed":
                execution.status, execution.error_code = "failed", "WORKSPACE_DOMAIN_FAILED"
                await db.commit()
                break
            await db.commit()
        else:
            execution.status = "completed"
            await db.commit()
    except Exception as exc:
        await db.rollback()
        failed_execution = await db.get(WorkspaceExecution, execution_id)
        assert failed_execution is not None
        execution = failed_execution
        execution.status = "needs_review"
        execution.error_code = (
            exc.error_code if isinstance(exc, StatForgeError) else "WORKSPACE_INTERRUPTED"
        )
        await db.commit()
    identity = await find_user(db, user_id=user_id)
    if identity is None:
        raise rejected()
    saved_execution = await db.get(WorkspaceExecution, execution_id, populate_existing=True)
    assert saved_execution is not None
    return await execution_response(db, saved_execution, identity)


async def require_plan_idle(db: AsyncSession, plan_id: uuid.UUID) -> None:
    active = await db.scalar(
        select(WorkspaceExecution.id)
        .where(WorkspaceExecution.plan_id == plan_id, WorkspaceExecution.status == "running")
        .limit(1)
    )
    if active is not None:
        raise rejected("WORKSPACE_PLAN_RUNNING")


async def mark_workspace_interrupted(db: AsyncSession) -> int:
    rows = await db.execute(
        update(WorkspaceExecution)
        .where(WorkspaceExecution.status == "running")
        .values(status="interrupted", error_code="WORKSPACE_RESTART_INTERRUPTED")
        .returning(WorkspaceExecution.id)
    )
    count = len(rows.all())
    await db.commit()
    return count
