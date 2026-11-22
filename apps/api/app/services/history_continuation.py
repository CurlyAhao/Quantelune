"""Copy frozen workflow input into a new owned draft, without running it."""

import uuid
from copy import deepcopy

from pydantic import ValidationError

from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.project import Project
from app.preview.context import prepare_context, schema_snapshot, verify_storage_version
from app.schemas.ml_drafts import MLEditorState
from app.schemas.preview import MLForm, PreviewScope
from app.schemas.workflow import WorkflowGraphRequest
from app.schemas.workspace import AnalysisDraft
from app.services import project_research as research
from app.services.workflow_service import create_workflow


def unavailable():
    return StatForgeError(
        409,
        "WORKFLOW_COPY_SOURCE_CHANGED",
        "原数据版本无法核对，请在工作流中重新选择数据。",
    )


async def ml_copy(db, project_id, user, resolved):
    payload = resolved["payload"]
    draft = payload.get("record", {}).get("payload", {})
    try:
        editor = (
            MLEditorState.model_validate(draft["ml_editor"]) if draft.get("ml_editor") else None
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise research.failure("ML_CONFIGURATION_UNAVAILABLE", 422) from exc
    configuration = deepcopy(draft.get("ml") or payload.get("input_config"))
    if (configuration is not None and not isinstance(configuration, dict)) or (
        configuration is None and editor is None
    ):
        raise research.failure("ML_CONFIGURATION_UNAVAILABLE", 422)
    # Keep engine parameters and unfinished editor input in their own forms.
    title = (draft.get("title") or payload.get("name") or "模型实验")[:150] + "（副本）"
    if configuration is not None:
        configuration["name"] = title
    if editor is not None:
        editor.name = title
    try:
        form = MLForm.model_validate(configuration) if configuration is not None else None
        identity = uuid.UUID(resolved["dataset_id"])
    except (ValidationError, ValueError, TypeError) as exc:
        raise research.failure("ML_CONFIGURATION_UNAVAILABLE", 422) from exc
    dataset = await db.get(Dataset, identity)
    if (
        not dataset
        or dataset.project_id != project_id
        or dataset.upload_status != UploadStatus.READY
        or dataset.sha256 != resolved["dataset_sha256"]
    ):
        raise research.failure("SOURCE_CHANGED")
    try:
        await verify_storage_version(dataset)
    except Exception as exc:
        raise research.failure("SOURCE_CHANGED") from exc
    inputs = form or editor
    fields = list(
        dict.fromkeys(
            [
                inputs.target,
                *inputs.features,
                *inputs.categorical_features,
                *[value for value in (inputs.group_variable, inputs.time_variable) if value],
            ]
        )
    )
    columns = [value for value in fields if value][:50]
    if not columns:
        columns = [schema_snapshot(dataset.schema_json)[0]["name"]]
    context = await prepare_context(
        db,
        PreviewScope(
            project_id=project_id,
            dataset_id=identity,
            dataset_sha256=dataset.sha256,
            columns=columns,
        ),
        user,
    )
    return AnalysisDraft(
        title=title,
        scope=context["scope"],
        ml=form,
        ml_editor=editor,
        research_question=draft.get("research_question", ""),
    )


async def workflow_copy(db, project_id, user, resolved):
    snapshot = deepcopy(resolved["payload"]["input_config"])
    if not isinstance(snapshot, dict):
        raise unavailable()
    provenance = snapshot.pop("_provenance", {})
    if not isinstance(provenance, dict) or not isinstance(provenance.get("sources", []), list):
        raise unavailable()
    if any(not isinstance(source, dict) for source in provenance.get("sources", [])):
        raise unavailable()
    project = await db.get(Project, project_id)
    _stored, mapping = research.archives(project)
    expected = {}
    for source in provenance.get("sources", []):
        identity = research.mapped(source.get("dataset_id"), mapping)
        sha = source.get("dataset_sha256")
        if not identity or not isinstance(sha, str) or len(sha) != 64:
            raise unavailable()
        if identity in expected and expected[identity] != sha:
            raise unavailable()
        expected[identity] = sha
    primary = resolved["dataset_id"]
    identities = {primary} if primary else set()
    try:
        graph = WorkflowGraphRequest.model_validate(snapshot)
        for node in graph.nodes:
            if node.type == "dataset" and node.data.get("dataset_id"):
                identity = research.mapped(node.data["dataset_id"], mapping)
                node.data["dataset_id"] = identity
                identities.add(identity)
        # Check every actual selected input, not intermediate output artifacts.
        # The archived graph grants no right to access the original project.
        for identity in identities:
            dataset = await db.get(Dataset, uuid.UUID(identity))
            if (
                not dataset
                or dataset.project_id != project_id
                or dataset.upload_status != UploadStatus.READY
                or dataset.sha256 != expected.get(identity)
            ):
                raise unavailable()
            try:
                await verify_storage_version(dataset)
            except Exception as exc:
                # A missing, changed or unreachable source cannot seed this
                # draft. Cancellation still propagates as BaseException.
                raise unavailable() from exc
    except (ValidationError, ValueError, TypeError, OSError) as exc:
        raise unavailable() from exc
    return await create_workflow(
        db,
        project_id,
        user,
        "工作流（副本）",
        uuid.UUID(primary) if primary else None,
        graph.model_dump(by_alias=True),
    )
