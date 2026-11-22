"""Project history and pinned report sources. Read saved results, never execute them."""

import base64
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, String, case, cast, func, literal, or_, select, union_all

from app.exceptions import StatForgeError
from app.models.dataset import Dataset
from app.models.material import Citation, Material
from app.models.ml import MLExperiment, MLRun
from app.models.project import Project
from app.models.quality import QualityRun
from app.models.research import ResearchRun, ResearchSession, ResearchWorkspace
from app.models.statistics import StatisticalRun, StatResult
from app.models.workflow import Workflow, WorkflowRun
from app.models.workspace import WorkspaceRecord
from app.research.contracts import logical_hash
from app.schemas.workspace import ReportDocument
from app.services.workflow_service import check_project_access

RECORD_KINDS = {
    "media_run",
    "analysis_run",
    "plot",
    "plot_run",
    "analysis",
    "figure",
    "report",
    "research_input",
    "plan",
    "template",
    "session",
}
REPORTABLE = {"plot_run", "statistics", "ml", "quality", "workflow", "figure", "citation"}


def failure(code="SOURCE_UNAVAILABLE", status=409):
    return StatForgeError(status, "PROJECT_RESEARCH_" + code, "记录或来源暂时无法读取。")


def document(value, default=None):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def identifier(value):
    return str(value) if value is not None else None


def timestamp(value):
    if value is None:
        return ""
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return (
        parsed.replace(tzinfo=UTC).isoformat()
        if parsed.tzinfo is None
        else parsed.astimezone(UTC).isoformat()
    )


def archives(project):
    """Imported history remains a read-only snapshot, never a native Run row."""
    root = document(project.snapshot_json, {}).get("imported_project")
    tables, mapping = {}, {}
    for _ in range(8):
        if not isinstance(root, dict):
            break
        for key, value in root.get("references", {}).items():
            mapping.setdefault(key, value)
        records = root.get("records", {})
        for name, rows in records.items():
            target = tables.setdefault(name, {})
            for row in rows:
                if row.get("id"):
                    target.setdefault(row["id"], row)
        parents = records.get("projects", [])
        nested = document(parents[0].get("snapshot_json"), {}) if parents else {}
        root = nested.get("imported_project")
    return tables, mapping


def mapped(value, mapping):
    value = identifier(value)
    seen = set()
    while value in mapping and value not in seen:
        seen.add(value)
        value = mapping[value]
    return value


async def labels(db, project_id, user):
    rows = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "history_label",
            )
            .order_by(WorkspaceRecord.updated_at.desc(), WorkspaceRecord.id.desc())
        )
    ).all()
    result = {}
    for row in rows:
        value = document(row.payload_json, {})
        result.setdefault(
            f"{value.get('target_kind')}:{value.get('target_id')}",
            {
                "title": value.get("title", ""),
                "revision": row.revision,
                "record_id": str(row.id),
            },
        )
    return result


def query_history(db, project_id, owner):
    def columns(
        kind, identity, created, status, title, dataset, revision=literal(1), detail=literal(None)
    ):
        return [
            literal(kind) if isinstance(kind, str) else kind,
            cast(identity, String),
            created,
            func.lower(cast(status, String)),
            title,
            cast(dataset, String),
            revision,
            cast(detail, String),
        ]

    def json_field(path):
        return (
            func.json_extract(WorkspaceRecord.payload_json, "$" + path)
            if db.bind.dialect.name == "sqlite"
            else cast(WorkspaceRecord.payload_json, JSON)[path.lstrip(".")].as_string()
        )

    statements = [
        select(
            *columns(
                "statistics",
                StatisticalRun.id,
                StatisticalRun.created_at,
                StatisticalRun.status,
                StatisticalRun.method,
                StatisticalRun.dataset_id,
            )
        ).where(StatisticalRun.project_id == project_id),
        select(
            *columns(
                "ml",
                MLRun.id,
                MLRun.created_at,
                MLRun.status,
                MLExperiment.name,
                MLExperiment.dataset_id,
                detail=MLExperiment.id,
            )
        )
        .join(MLExperiment, MLRun.experiment_id == MLExperiment.id)
        .where(MLExperiment.project_id == project_id),
        select(
            *columns(
                "quality",
                QualityRun.id,
                QualityRun.created_at,
                QualityRun.status,
                literal("数据质量"),
                QualityRun.dataset_id,
            )
        )
        .join(Dataset, QualityRun.dataset_id == Dataset.id)
        .where(Dataset.project_id == project_id),
        select(
            *columns(
                "workflow",
                WorkflowRun.id,
                WorkflowRun.created_at,
                WorkflowRun.status,
                Workflow.name,
                WorkflowRun.dataset_id,
                detail=Workflow.id,
            )
        )
        .join(Workflow, WorkflowRun.workflow_id == Workflow.id)
        .where(Workflow.project_id == project_id),
        select(
            *columns(
                "research",
                ResearchRun.id,
                ResearchRun.created_at,
                ResearchRun.state,
                ResearchSession.title,
                literal(None),
                ResearchRun.revision,
                ResearchSession.id,
            )
        )
        .join(ResearchSession, ResearchRun.session_id == ResearchSession.id)
        .join(ResearchWorkspace, ResearchSession.workspace_id == ResearchWorkspace.id)
        .where(ResearchWorkspace.project_id == project_id, ResearchWorkspace.owner_id == owner),
        select(
            *columns(
                WorkspaceRecord.kind,
                WorkspaceRecord.id,
                WorkspaceRecord.updated_at,
                case(
                    (
                        WorkspaceRecord.kind.in_(["plot_run", "analysis_run", "media_run"]),
                        json_field(".status"),
                    ),
                    else_=literal("saved"),
                ),
                json_field(".title"),
                case(
                    (
                        WorkspaceRecord.kind.in_(["analysis_run", "media_run"]),
                        json_field(".dataset_id"),
                    ),
                    else_=json_field(".scope.dataset_id"),
                ),
                WorkspaceRecord.revision,
            )
        ).where(
            WorkspaceRecord.project_id == project_id,
            WorkspaceRecord.user_id == owner,
            WorkspaceRecord.kind.in_(RECORD_KINDS),
            or_(WorkspaceRecord.kind != "media_run", json_field(".case_archive").is_(None)),
        ),
        select(
            *columns(
                "citation",
                Citation.id,
                Citation.updated_at,
                literal("reviewed"),
                Material.filename,
                literal(None),
                Citation.revision,
                Material.id,
            )
        )
        .join(Material, Citation.material_id == Material.id)
        .where(
            Citation.project_id == project_id,
            Citation.user_id == owner,
            Citation.reviewed.is_(True),
        ),
    ]
    return union_all(*statements).subquery("project_history")


async def history(
    db,
    project_id,
    user,
    q="",
    kind=None,
    offset=0,
    limit=40,
    reportable=False,
    runs_only=False,
    run_filter="all",
):
    await check_project_access(db, project_id, user)
    project = await db.get(Project, project_id)
    annotations = await labels(db, project_id, user)
    stored, mapping = archives(project)
    for row in stored.get("workspace_preview_records", {}).values():
        if row.get("kind") != "history_label":
            continue
        value = document(row.get("payload_json"), {})
        target, target_kind = value.get("target_id"), value.get("target_kind")
        if target and value.get("title"):
            annotation = {"title": value["title"], "revision": 0}
            annotations.setdefault(f"{target_kind}:{target}", annotation)
            annotations.setdefault(f"{target_kind}:{mapped(target, mapping)}", annotation)
    timeline = query_history(db, project_id, user.id)
    names = ["kind", "id", "created_at", "status", "title", "dataset_id", "revision", "detail_id"]
    timeline = timeline.select().subquery().alias("history_rows")
    cols = list(timeline.c)
    query = select(
        *[column.label(name) for column, name in zip(cols, names, strict=True)],
        Dataset.original_filename.label("filename"),
    ).outerjoin(
        Dataset, func.replace(cast(Dataset.id, String), "-", "") == func.replace(cols[5], "-", "")
    )
    conditions = []
    run_kinds = {
        "media_run",
        "analysis_run",
        "plot_run",
        "statistics",
        "ml",
        "quality",
        "workflow",
        "research",
    }
    if runs_only:
        from app.services import analysis_jobs, multimodal_cases, plots

        live_ids = {
            "media_run": [value.hex for value in multimodal_cases.JOBS],
            "analysis_run": [value.hex for value in analysis_jobs.JOBS],
            "plot_run": [value.hex for value in plots.JOBS],
        }
        active_states = {"queued", "running", "cancel_requested", "cancel_pending"}
        running = or_(
            (cols[0] == "research") & cols[3].in_(active_states),
            *[
                (cols[0] == name)
                & func.replace(cols[1], "-", "").in_(values)
                & cols[3].in_(active_states)
                for name, values in live_ids.items()
            ],
        )
        ended_states = {"completed", "completed_with_errors", "cancelled", "not_applicable"}
        conditions.append(cols[0].in_(run_kinds))
        for native_kind in ("statistics", "ml"):
            field = (
                func.json_extract(WorkspaceRecord.payload_json, "$.native_id")
                if db.bind.dialect.name == "sqlite"
                else cast(WorkspaceRecord.payload_json, JSON)["native_id"].as_string()
            )
            operation = (
                func.json_extract(WorkspaceRecord.payload_json, "$.operation")
                if db.bind.dialect.name == "sqlite"
                else cast(WorkspaceRecord.payload_json, JSON)["operation"].as_string()
            )
            linked = select(func.replace(field, "-", "")).where(
                WorkspaceRecord.project_id == project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "analysis_run",
                operation == native_kind,
                field.is_not(None),
            )
            conditions.append(
                ~((cols[0] == native_kind) & func.replace(cols[1], "-", "").in_(linked))
            )
    if reportable:
        conditions.append(cols[0].in_(REPORTABLE))
        conditions.append(
            or_(
                cols[0] == "figure",
                (cols[0] == "citation") & (cols[3] == "reviewed"),
                cols[3].in_(["completed", "completed_with_errors"]),
            )
        )
    if kind:
        conditions.append(cols[0] == kind)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        aliases = [
            key.split(":", 1)
            for key, label in annotations.items()
            if q.casefold() in label["title"].casefold()
        ]
        conditions.append(
            or_(
                cols[4].ilike(pattern, escape="\\"),
                Dataset.original_filename.ilike(pattern, escape="\\"),
                *[
                    (cols[0] == name)
                    & (func.replace(cols[1], "-", "") == identity.replace("-", ""))
                    for name, identity in aliases
                ],
            )
        )
    if conditions:
        query = query.where(*conditions)
    active_total = (
        await db.scalar(select(func.count()).select_from(query.where(running).subquery()))
        if runs_only
        else None
    )
    if runs_only and run_filter == "active":
        query = query.where(running)
    elif runs_only and run_filter == "attention":
        query = query.where(~running, ~cols[3].in_(ended_states))
    elif runs_only and run_filter == "finished":
        query = query.where(cols[3].in_(ended_states))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        (await db.execute(query.order_by(cols[2].desc(), cols[1].desc()).limit(offset + limit)))
        .mappings()
        .all()
    )
    items = [
        {**dict(row), "created_at": timestamp(row["created_at"]), "archived": False} for row in rows
    ]
    for item in items:
        for field in ("id", "dataset_id", "detail_id"):
            if item.get(field):
                item[field] = str(uuid.UUID(item[field]))
    archived = []
    archived_native = {operation: set() for operation in ("statistics", "ml")}
    if runs_only:
        for record in stored.get("workspace_preview_records", {}).values():
            if record.get("kind") == "analysis_run":
                job = document(record.get("payload_json"), {})
                if job.get("native_id") and job.get("operation") in archived_native:
                    archived_native[job["operation"]].add(str(job["native_id"]).replace("-", ""))
    for table, source_kind in [
        ("statistical_runs", "statistics"),
        ("ml_runs", "ml"),
        ("quality_runs", "quality"),
        ("workflow_runs", "workflow"),
        ("material_citations", "citation"),
        ("workspace_preview_records", None),
    ]:
        for row in stored.get(table, {}).values():
            row_kind = source_kind or row.get("kind")
            if row_kind not in REPORTABLE | RECORD_KINDS or (kind and row_kind != kind):
                continue
            if runs_only and (
                row_kind not in run_kinds
                or str(row["id"]).replace("-", "") in archived_native.get(row_kind, set())
            ):
                continue
            payload = document(row.get("payload_json"), {})
            experiment = stored.get("ml_experiments", {}).get(row.get("experiment_id"), {})
            dataset_id = (
                row.get("dataset_id")
                or experiment.get("dataset_id")
                or payload.get("dataset_id")
                or payload.get("scope", {}).get("dataset_id")
            )
            data = stored.get("datasets", {}).get(dataset_id, {})
            material = (
                stored.get("materials", {}).get(row.get("material_id"), {})
                if row_kind == "citation"
                else {}
            )
            filename = material.get("filename") or data.get("original_filename")
            title = (
                payload.get("title")
                or material.get("filename")
                or row.get("method")
                or experiment.get("name")
                or row_kind
            )
            item = {
                "kind": row_kind,
                "id": row["id"],
                "title": title,
                "created_at": timestamp(row.get("updated_at") or row.get("created_at")),
                "status": str(row.get("status", "saved")).lower(),
                "dataset_id": mapped(dataset_id, mapping),
                "filename": filename,
                "revision": row.get("revision", 1),
                "detail_id": row.get("experiment_id") or row.get("workflow_id"),
                "archived": True,
            }
            if row_kind in {"plot_run", "analysis_run", "media_run"}:
                item["status"] = payload.get("status", "interrupted")
            if row_kind == "citation":
                item["status"] = "reviewed" if row.get("reviewed") else "unreviewed"
            if runs_only and (
                run_filter in {"active", "attention"}
                or (run_filter == "finished" and item["status"] not in ended_states)
            ):
                continue
            if reportable and (
                row_kind not in REPORTABLE
                or (
                    row_kind != "figure"
                    and item["status"] not in {"completed", "completed_with_errors", "reviewed"}
                )
            ):
                continue
            alias = annotations.get(f"{row_kind}:{row['id']}", {}).get("title", "")
            if q and q.casefold() not in f"{title} {alias} {item['filename']}".casefold():
                continue
            archived.append(item)
    items.extend(archived)
    items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    for item in items:
        label = annotations.get(f"{item['kind']}:{item['id']}")
        item["label_revision"] = label["revision"] if label else 0
        item["original_title"] = item["title"]
        if label:
            item["title"] = label["title"]
    return {
        "items": items[offset : offset + limit],
        "total": (total or 0) + len(archived),
        "offset": offset,
        "limit": limit,
        **({"active_total": active_total or 0} if runs_only else {}),
    }


def stat_payload(row, results):
    result = next(
        (
            document(item.get("result_json"))
            for item in results
            if identifier(item.get("statistical_run_id")) == identifier(row["id"])
        ),
        None,
    )
    return {
        "id": identifier(row["id"]),
        "project_id": identifier(row["project_id"]),
        "dataset_id": identifier(row["dataset_id"]),
        "dataset_sha256": row["dataset_sha256"],
        "method": row["method"],
        "status": str(getattr(row["status"], "value", row["status"])).lower(),
        "input_config": document(row.get("input_config_json"), {}),
        "result": result,
    }


async def source(db, project_id, user, kind, source_id, revision=None):
    await check_project_access(db, project_id, user)
    project = await db.get(Project, project_id)
    stored, mapping = archives(project)
    archived = False
    payload, dataset_id, data_sha, latest = {}, None, None, 1
    source_id = uuid.UUID(str(source_id))
    if kind == "statistics":
        row = (
            (
                await db.execute(
                    select(StatisticalRun.__table__).where(
                        StatisticalRun.id == source_id, StatisticalRun.project_id == project_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row:
            results = (
                (
                    await db.execute(
                        select(StatResult.__table__).where(
                            StatResult.statistical_run_id == source_id
                        )
                    )
                )
                .mappings()
                .all()
            )
        else:
            row = stored.get("statistical_runs", {}).get(str(source_id))
            results = list(stored.get("stat_results", {}).values())
            archived = True
        if not row:
            raise failure()
        payload = stat_payload(row, results)
        dataset_id, data_sha = row["dataset_id"], row["dataset_sha256"]
    elif kind == "plot_run":
        from app.services import plots

        record = await plots.get_run(db, project_id, source_id, user)
        latest = record["revision"]
        if revision and revision != latest:
            raise failure("PLOT_VERSION_UNAVAILABLE")
        body = record["payload"]
        dataset_id, data_sha = body["scope"].get("dataset_id"), body["scope"].get("dataset_sha256")
        archived = record.get("archived", False)
        payload = {"record": record, "status": body["status"]}
        if body["status"] == "completed":
            content, _mime = await plots.artifact(db, project_id, source_id, "png", user)
            payload["figure_png"] = base64.b64encode(content).decode("ascii")
    elif kind in {
        "media_run",
        "analysis_run",
        "plot",
        "analysis",
        "figure",
        "report",
        "research_input",
        "plan",
        "template",
        "session",
    }:
        from app.preview.workspace import read_record

        own = await db.scalar(
            select(WorkspaceRecord).where(
                WorkspaceRecord.id == source_id,
                WorkspaceRecord.project_id == project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == kind,
            )
        )
        if own:
            latest = own.revision
            record = await read_record(db, source_id, user, revision)
        else:
            row = stored.get("workspace_preview_records", {}).get(str(source_id))
            if not row or row.get("kind") != kind:
                raise failure()
            raw = row["payload_json"]
            wanted = revision or row["revision"]
            if wanted != row["revision"]:
                version = next(
                    (
                        value
                        for value in stored.get("workspace_preview_revisions", {}).values()
                        if value.get("record_id") == str(source_id)
                        and value.get("revision") == wanted
                    ),
                    None,
                )
                if not version:
                    raise failure()
                raw = version["payload_json"]
            record = {
                "id": str(source_id),
                "project_id": row["project_id"],
                "owner_id": row["user_id"],
                "revision": wanted,
                "payload": document(raw),
            }
            latest, archived = row["revision"], True
        body = record["payload"]
        dataset_id, data_sha = (
            body.get("scope", {}).get("dataset_id"),
            body.get("scope", {}).get("dataset_sha256"),
        )
        payload = {"record": record}
        if kind == "analysis_run":
            dataset_id, data_sha = body["dataset_id"], body["dataset_sha256"]
            payload["status"] = body["status"]
        if kind == "media_run":
            dataset_id = body["dataset_id"]
            payload["status"] = body["status"]
        if kind == "figure":
            run = await source(
                db, project_id, user, "statistics", body["scope"]["statistical_run_id"]
            )
            payload["run"] = run["payload"]
            snapshot_id = body.get("snapshot_id")
            if snapshot_id:
                if not archived:
                    payload["snapshot"] = await read_record(db, uuid.UUID(snapshot_id), user)
                else:
                    snap = stored.get("workspace_preview_records", {}).get(snapshot_id)
                    if not snap:
                        raise failure()
                    payload["snapshot"] = {
                        "id": snap["id"],
                        "owner_id": snap["user_id"],
                        "project_id": snap["project_id"],
                        "revision": snap["revision"],
                        "payload": document(snap["payload_json"]),
                    }
            else:
                payload["snapshot"] = None
    elif kind == "ml":
        row = (
            (
                await db.execute(
                    select(MLRun.__table__)
                    .join(MLExperiment, MLRun.experiment_id == MLExperiment.id)
                    .where(MLRun.id == source_id, MLExperiment.project_id == project_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row:
            exp = (
                (
                    await db.execute(
                        select(MLExperiment.__table__).where(
                            MLExperiment.id == row["experiment_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
        else:
            row = stored.get("ml_runs", {}).get(str(source_id))
            exp = stored.get("ml_experiments", {}).get(row.get("experiment_id")) if row else None
            archived = True
        if not row or not exp:
            raise failure()
        dataset_id, data_sha = exp["dataset_id"], exp["dataset_sha256"]
        payload = {
            "id": str(source_id),
            "experiment_id": identifier(exp["id"]),
            "name": exp["name"],
            "status": str(getattr(row["status"], "value", row["status"])).lower(),
            "task_type": exp["task_type"],
            "input_config": document(exp.get("config_json"), {}),
            "target": exp["target_variable"],
            "features": document(exp.get("feature_variables_json"), []),
            "models": document(row.get("metrics_json"), []),
            "comparison": document(row.get("comparison_json")),
            "validation_strategy": row.get("validation_strategy"),
            "random_seed": row.get("random_seed"),
            "software_versions": document(row.get("software_versions_json"), {}),
        }
    elif kind in {"quality", "workflow"}:
        model = QualityRun if kind == "quality" else WorkflowRun
        query = select(model.__table__)
        if kind == "quality":
            query = query.join(Dataset, model.dataset_id == Dataset.id).where(
                Dataset.project_id == project_id
            )
        else:
            query = query.join(Workflow, model.workflow_id == Workflow.id).where(
                Workflow.project_id == project_id
            )
        row = (await db.execute(query.where(model.id == source_id))).mappings().one_or_none()
        if not row:
            row = stored.get(model.__tablename__, {}).get(str(source_id))
            archived = True
        if not row:
            raise failure()
        dataset_id, data_sha = row.get("dataset_id"), row.get("dataset_sha256")
        keys = (
            (
                "ruleset_version",
                "total_rules",
                "passed_count",
                "warning_count",
                "failed_count",
                "error_count",
                "skipped_count",
                "overall_score",
                "sampled",
            )
            if kind == "quality"
            else ("total_seconds",)
        )
        payload = {key: row.get(key) for key in keys}
        payload.update(
            id=str(source_id),
            status=str(getattr(row["status"], "value", row["status"])).lower(),
            input_config=document(
                row.get("threshold_config_json")
                if kind == "quality"
                else row.get("graph_snapshot_json"),
                {},
            ),
        )
    elif kind == "citation":
        row = (
            (
                await db.execute(
                    select(Citation.__table__).where(
                        Citation.id == source_id,
                        Citation.project_id == project_id,
                        Citation.user_id == user.id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            row = stored.get("material_citations", {}).get(str(source_id))
            archived = True
        if not row:
            raise failure()
        latest = row["revision"]
        if revision and revision != latest:
            raise failure("CITATION_VERSION_UNAVAILABLE")
        payload = {
            "id": str(source_id),
            "material_id": identifier(row["material_id"]),
            "material_sha256": row["material_sha256"],
            "material_version": row["material_version"],
            "revision": row["revision"],
            "reviewed": row["reviewed"],
            "locator": document(row["locator_json"]),
            "status": "reviewed" if row["reviewed"] else "unreviewed",
        }
        data_sha = row["material_sha256"]
    elif kind == "research":
        row = await db.scalar(
            select(ResearchRun)
            .join(ResearchSession, ResearchRun.session_id == ResearchSession.id)
            .join(ResearchWorkspace, ResearchSession.workspace_id == ResearchWorkspace.id)
            .where(
                ResearchRun.id == source_id,
                ResearchWorkspace.project_id == project_id,
                ResearchWorkspace.owner_id == user.id,
            )
        )
        if not row:
            raise failure()
        payload = {
            "id": str(source_id),
            "session_id": str(row.session_id),
            "status": row.state,
            "input": document(row.input_json),
            "revision": row.revision,
        }
        latest = row.revision
    else:
        raise failure("SOURCE_KIND", 422)
    from app.preview.workspace import credential_free

    await credential_free(db, user, payload)
    file = await db.get(Dataset, uuid.UUID(mapped(dataset_id, mapping))) if dataset_id else None
    reference = {
        "kind": kind,
        "id": str(source_id),
        "revision": payload.get("record", {}).get("revision", latest),
        "sha256": fingerprint(kind, payload),
    }
    issue = source_issue(kind, payload)
    location = await material_location(db, project, user, kind, payload)
    return {
        "reference": reference,
        "payload": payload,
        "archived": archived,
        "latest_revision": latest,
        "material": location,
        "issue_code": issue,
        "dataset_id": mapped(dataset_id, mapping),
        "dataset_sha256": data_sha,
        "filename": file.original_filename
        if file and file.project_id == project_id
        else (location["filename"] if location else None),
        "source_version": file.source_version if file and file.project_id == project_id else None,
    }


async def material_location(db, project, user, kind, payload):
    """Map a frozen citation to an owned, byte-identical current material."""
    if kind != "citation" or not payload.get("material_id"):
        return None
    original_id = payload["material_id"]
    identity = mapped(original_id, archives(project)[1])
    try:
        material = await db.get(Material, uuid.UUID(identity))
    except (ValueError, TypeError):
        return None
    if (
        not material
        or material.project_id != project.id
        or material.user_id != user.id
        or material.sha256 != payload.get("material_sha256")
        or (identity == original_id and material.version != payload.get("material_version"))
    ):
        return None
    return {
        "id": str(material.id),
        "sha256": material.sha256,
        "version": material.version,
        "filename": material.filename,
    }


def source_issue(kind, payload):
    result = payload.get("result") or {}
    if (
        kind == "statistics"
        and result.get("method") == "descriptive"
        and result.get("method_version") == "1.0.0"
    ):
        values = result.get("descriptive_statistics", {}).get("variables", {}).values()
        if any(
            value.get("count") == 1 and (value.get("std") == 0 or value.get("variance") == 0)
            for value in values
        ):
            return "LEGACY_SINGLE_ROW_DISPERSION"
    return None


def require_reportable(kind, payload):
    issue = source_issue(kind, payload)
    if issue:
        raise failure(issue)
    status = payload.get("status")
    if status and status not in {"completed", "completed_with_errors", "reviewed"}:
        raise failure("SOURCE_NOT_READY")


def fingerprint(kind, payload):
    try:
        if kind == "plot_run":
            body = payload["record"]["payload"]
            return logical_hash(
                {
                    "code": body["code"],
                    "dpi": body["dpi"],
                    "status": body["status"],
                    "data": {
                        key: body["scope"].get(key)
                        for key in ("dataset_sha256", "schema_sha256", "columns")
                    },
                    "metadata": body["metadata"],
                    "artifacts": {
                        ext: {key: value[key] for key in ("sha256", "bytes")}
                        for ext, value in body["artifacts"].items()
                    },
                    "figure_png": payload.get("figure_png"),
                }
            )
        stable = (
            payload
            if kind != "figure"
            else {
                "record": {key: payload["record"][key] for key in ("id", "revision", "payload")},
                "run": payload["run"],
                "snapshot": {key: payload["snapshot"][key] for key in ("id", "revision", "payload")}
                if payload.get("snapshot")
                else None,
            }
        )
        return logical_hash(stable)
    except (ValueError, TypeError, KeyError):
        raise failure("SOURCE_SNAPSHOT_INVALID", 422) from None


async def validate_report(
    db, report: ReportDocument, project_id, user, allowed=(), retained_snapshots=None
):
    from app.preview.workspace import credential_free
    from app.services import report_sources as frames

    retained_snapshots = retained_snapshots or {}
    project = await db.get(Project, project_id)
    for block in report.blocks:
        if not block.source:
            continue
        ref = block.source
        signature = ref.model_dump_json()
        if signature in retained_snapshots and retained_snapshots[signature] is not None:
            if not frames.external(retained_snapshots[signature]):
                await credential_free(db, user, retained_snapshots[signature])
            block.source_snapshot = frames.retained(
                retained_snapshots[signature], ref, project, user
            )
            continue
        if frames.external(block.source_snapshot):
            # A server-owned immutable frame can be reused across documents,
            # including restored historical results with no live native Run.
            payload = frames.read(block.source_snapshot, ref, project, user)
            await credential_free(db, user, payload)
            if signature not in allowed:
                require_reportable(ref.kind, payload)
            block.source_snapshot = frames.store(payload, ref, project, user)
            continue
        try:
            value = await source(db, project_id, user, ref.kind, ref.id, ref.revision)
            if value["reference"] != ref.model_dump(mode="json"):
                raise failure("SOURCE_CHANGED")
            require_reportable(ref.kind, value["payload"])
            # The server selects the saved bytes. JSON round trips through JS
            # can change 91.0 to 91; client frames are never result authority.
            payload = value["payload"]
        except StatForgeError:
            signature = ref.model_dump_json()
            if signature in retained_snapshots:
                block.source_snapshot = frames.retained(
                    retained_snapshots[signature], ref, project, user
                )
                continue
            if frames.external(block.source_snapshot):
                payload = frames.read(block.source_snapshot, ref, project, user)
            elif (
                signature in allowed
                and block.source_snapshot is not None
                and fingerprint(ref.kind, block.source_snapshot) == ref.sha256
            ):
                payload = block.source_snapshot
                await credential_free(db, user, payload)
            else:
                raise
        block.source_snapshot = frames.store(payload, ref, project, user)


async def report_sources(db, report: ReportDocument, project_id, user):
    from app.preview.workspace import credential_free
    from app.services import report_sources as frames

    result = {}
    project = await db.get(Project, project_id)
    for block in report.blocks:
        if not block.source:
            continue
        pinned = None
        try:
            candidate = (
                frames.read(block.source_snapshot, block.source, project, user)
                if frames.external(block.source_snapshot)
                else block.source_snapshot
            )
            if (
                candidate is not None
                and fingerprint(block.source.kind, candidate) == block.source.sha256
            ):
                await credential_free(db, user, candidate)
                pinned = candidate
        except StatForgeError:
            pass
        try:
            item = await source(
                db, project_id, user, block.source.kind, block.source.id, block.source.revision
            )
            if item["reference"] != block.source.model_dump(mode="json"):
                raise failure("SOURCE_CHANGED")
            if pinned is not None:
                item["payload"] = pinned
                item["material"] = await material_location(
                    db, project, user, block.source.kind, pinned
                )
            result[str(block.id)] = {"available": True, **item}
        except StatForgeError as exc:
            try:
                payload = (
                    pinned
                    if pinned is not None
                    else (
                        frames.read(block.source_snapshot, block.source, project, user)
                        if frames.external(block.source_snapshot)
                        else block.source_snapshot
                    )
                )
                if (
                    payload is None
                    or fingerprint(block.source.kind, payload) != block.source.sha256
                ):
                    raise frames.fail()
                await credential_free(db, user, payload)
                location = await material_location(db, project, user, block.source.kind, payload)
                result[str(block.id)] = {
                    "available": True,
                    "retained": True,
                    "archived": bool(archives(project)[0]),
                    "reference": block.source.model_dump(mode="json"),
                    "payload": payload,
                    "material": location,
                    "latest_revision": block.source.revision,
                    "dataset_id": None,
                    "dataset_sha256": payload.get("material_sha256"),
                    "filename": location["filename"] if location else None,
                }
            except StatForgeError:
                result[str(block.id)] = {"available": False, "error_code": exc.error_code}
    return result
