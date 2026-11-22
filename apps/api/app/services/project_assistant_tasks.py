"""Bind conversational work to an existing research task, retaining every result."""

import uuid

from app.database import async_session_factory
from app.models.user import User
from app.preview.context import digest, schema_snapshot
from app.preview.workspace import load_record, rejected, save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.preview import MLForm, PreviewScope, StatisticalForm
from app.schemas.workspace import AnalysisDraft, ResearchTask, SaveRecord


async def context(db, project, user, key):
    if key == "project":
        return None
    try:
        identity = uuid.UUID(key)
    except ValueError:
        raise rejected("ASSISTANT_TASK_NOT_FOUND", 404) from None
    row = await load_record(db, identity, user, project)
    if row.kind != "research_task":
        raise rejected("ASSISTANT_TASK_NOT_FOUND", 404)
    task = ResearchTask.model_validate_json(row.payload_json)
    return {"id": str(row.id), "revision": row.revision, **task.model_dump(mode="json")}


def scope(dataset, columns):
    return PreviewScope(
        project_id=dataset.project_id,
        dataset_id=dataset.id,
        dataset_sha256=dataset.sha256,
        schema_sha256=digest(schema_snapshot(dataset.schema_json)),
        columns=list(dict.fromkeys(value for value in columns if value)),
    )


async def append(db, user, live, field, identity, *, source_dataset=None):
    captured = live.value.get("task_context")
    if not captured:
        return None
    # Read after the actor fence, merge only the new reference, then CAS. Never
    # rewrite the user's question, adopted result, editor state or active view.
    await acquire_write_fence(db, user.id)
    row = await load_record(db, uuid.UUID(captured["id"]), user, live.project)
    task = ResearchTask.model_validate_json(row.payload_json)
    values = list(getattr(task, field))
    for item in ([source_dataset] if source_dataset else []) + [identity]:
        if item not in values:
            values.append(item)
    changes = {field: values}
    if field == "dataset_ids" and task.dataset_id == source_dataset:
        changes["dataset_id"] = identity
    updated = ResearchTask.model_validate({**task.model_dump(), **changes})
    if updated == task:
        return None
    return await save_record(
        db,
        user,
        SaveRecord(project_id=live.project, expected_revision=row.revision, payload=updated),
        row.id,
        commit=False,
    )


async def attach_record(live, field, identity, *, source_dataset=None):
    async with async_session_factory() as db:
        user = await db.get(User, live.owner)
        value = await append(
            db, user, live, field, uuid.UUID(str(identity)), source_dataset=source_dataset
        )
        await db.commit()
        return value


async def analysis(db, live, user, dataset, kind, identity, config, title):
    if not live.value.get("task_context"):
        return None
    await acquire_write_fence(db, user.id)
    task_row = await load_record(
        db, uuid.UUID(live.value["task_context"]["id"]), user, live.project
    )
    task = ResearchTask.model_validate_json(task_row.payload_json)
    # Re-reading a saved Run must not create another analysis version.
    for record_id in task.analysis_ids:
        row = await load_record(db, record_id, user, live.project)
        previous = AnalysisDraft.model_validate_json(row.payload_json)
        if getattr(
            previous.scope, "statistical_run_id" if kind == "statistics" else "ml_run_id"
        ) == uuid.UUID(str(identity)):
            return str(row.id)
    if kind == "statistics":
        config = dict(config)
        if "missing_policy" in config:
            config["missing_value_policy"] = config.pop("missing_policy")
        form = StatisticalForm.model_validate(config)
        used = [
            *form.variables,
            *form.categorical_variables,
            *(form.reference_categories or {}),
            form.group_variable,
            form.target,
        ]
        if form.evaluation:
            used.extend([form.evaluation.actual.name, form.evaluation.predicted.name])
        bound = scope(dataset, used)
        bound.statistical_run_id = uuid.UUID(str(identity))
        bound.statistical_config = form
        draft = AnalysisDraft(
            title=title[:160], scope=bound, statistics=form, research_question=task.question[:1000]
        )
    else:
        form = MLForm.model_validate(config)
        bound = scope(
            dataset,
            [
                form.target,
                *form.features,
                *form.categorical_features,
                form.group_variable,
                form.time_variable,
            ],
        )
        bound.ml_run_id = uuid.UUID(str(identity))
        draft = AnalysisDraft(
            title=title[:160], scope=bound, ml=form, research_question=task.question[:1000]
        )
    saved = await save_record(
        db, user, SaveRecord(project_id=live.project, payload=draft), commit=False
    )
    await append(db, user, live, "analysis_ids", uuid.UUID(saved["id"]))
    await db.commit()
    return saved["id"]
