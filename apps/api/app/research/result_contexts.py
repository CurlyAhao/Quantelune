"""Explicit attached results, each with its own proven source and historical citation."""

import uuid
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.models.research import ResearchArtifact, ResearchArtifactEdge, ResearchAttempt
from app.models.research_execution import ResearchPublicationReceipt
from app.preview.context import digest, schema_snapshot
from app.research.contracts import State, parse_document
from app.research.domain_results import verify_artifact
from app.research.egress import DisclosureSelection, failure
from app.schemas.preview import PreviewScope
from app.schemas.preview_extensions import ExplanationRequest
from app.services import research_context_service as contexts
from app.services import research_service as ledger


class ResultExplanationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection: DisclosureSelection
    request: ExplanationRequest


async def prove_result(db, owner, artifact_id):
    selected = await ledger.artifact(db, owner, artifact_id)
    root = selected
    if selected.kind != "domain_result":
        parents = (
            await db.scalars(
                select(ResearchArtifact)
                .join(ResearchArtifactEdge, ResearchArtifactEdge.parent_id == ResearchArtifact.id)
                .where(
                    ResearchArtifactEdge.child_id == selected.id,
                    ResearchArtifactEdge.relation == "saved_output",
                    ResearchArtifact.kind == "domain_result",
                )
                .limit(2)
            )
        ).all()
        if len(parents) != 1:
            raise failure("TOOL_RESULT_REQUIRED")
        root = parents[0]
    run, _ = await ledger.run(db, root.run_id, owner)
    number = parse_document(root.provenance_json).get("attempt_number", 1)
    if type(number) is not int or number < 1 or selected.run_id != run.id:
        raise failure("TOOL_RESULT_CHANGED")
    attempt = await db.scalar(
        select(ResearchAttempt)
        .where(ResearchAttempt.run_id == run.id, ResearchAttempt.number == number)
        .execution_options(populate_existing=True)
    )
    if (
        attempt is None
        or attempt.state != State.COMPLETED.value
        or attempt.domain_kind not in {"statistics", "ml"}
        or attempt.domain_kind != run.kind
        or attempt.domain_run_id is None
        or attempt.completion_hash is None
    ):
        raise failure("TOOL_RESULT_REQUIRED")
    snapshot = await ledger._snapshot_for_attempt(db, owner, run, attempt)
    view = SimpleNamespace(
        id=run.id,
        session_id=run.session_id,
        kind=run.kind,
        input_json=run.input_json,
        input_hash=run.input_hash,
        attempt_count=attempt.number,
        domain_kind=attempt.domain_kind,
        domain_run_id=attempt.domain_run_id,
    )
    if (
        snapshot.state != State.COMPLETED
        or snapshot.logical_hash != attempt.completion_hash
        or await ledger._prove_completion(db, owner, view, current_context=False)
        != attempt.completion_hash
    ):
        raise failure("TOOL_RESULT_CHANGED")
    if parse_document(run.input_json)["parameters"].get("operation") == "execute":
        receipt = await db.get(ResearchPublicationReceipt, attempt.id, populate_existing=True)
        if receipt is None or receipt.root_id != root.id:
            raise failure("TOOL_RESULT_CHANGED")
    children = (
        await db.scalars(
            select(ResearchArtifact)
            .join(ResearchArtifactEdge, ResearchArtifactEdge.child_id == ResearchArtifact.id)
            .where(
                ResearchArtifactEdge.parent_id == root.id,
                ResearchArtifactEdge.relation == "saved_output",
            )
            .limit(len(snapshot.outputs) + 1)
            .execution_options(populate_existing=True)
        )
    ).all()
    if len({item.object_key for item in children}) != len(children) or {
        item.object_key for item in children
    } != {item.object_key for item in snapshot.outputs}:
        raise failure("TOOL_RESULT_CHANGED")
    artifacts = [root, *children]
    if selected.id not in {item.id for item in artifacts}:
        raise failure("TOOL_RESULT_CHANGED")
    for item in artifacts:
        provenance = parse_document(item.provenance_json)
        if item.run_id != run.id or provenance.get("attempt_number", 1) != number:
            raise failure("TOOL_RESULT_CHANGED")
        verify_artifact(item, snapshot, owner)
    citation = dict(
        artifact_id=str(selected.id),
        artifact_sha256=selected.sha256,
        artifact_logical_hash=selected.logical_hash,
        root_artifact_id=str(root.id),
        root_sha256=root.sha256,
        root_logical_hash=root.logical_hash,
        research_run_id=str(run.id),
        attempt_id=str(attempt.id),
        attempt_number=attempt.number,
        completion_hash=attempt.completion_hash,
        input_hash=run.input_hash,
        domain_kind=attempt.domain_kind,
        domain_run_id=str(attempt.domain_run_id),
    )
    return snapshot, citation


def saved_columns(snapshot):
    """Only fields already used by this proved saved result, never live-table expansion."""
    if snapshot.kind == "statistics":
        config = snapshot.document["run"].get("input_config") or {}
        if config.get("method_id") in {
            "evaluate_predictions",
            "evaluate_binary_predictions",
        } or snapshot.document["run"].get("method") in {
            "evaluate_predictions",
            "evaluate_binary_predictions",
        }:
            from app.ml.resources import MLResourceError
            from app.research.domain_compute import normalize

            try:
                if (
                    config.get("method_id")
                    not in {"evaluate_predictions", "evaluate_binary_predictions"}
                    or "missing_value_policy" in config
                ):
                    raise ValueError("Unexpected saved configuration shape.")
                parameters = dict(config)
                parameters["missing_value_policy"] = parameters.pop("missing_policy")
                evaluation = normalize("research_statistics", parameters)["evaluation"]
                actual, predicted = evaluation["actual"], evaluation["predicted"]
                source = snapshot.datasets[0]
                if (
                    len(snapshot.datasets) != 1
                    or snapshot.document["run"]["method"] != config["method_id"]
                    or actual["dataset_id"] != source["id"]
                    or actual["dataset_sha256"] != source["sha256"]
                    or actual["dataset_sha256"] != snapshot.document["run"]["dataset_sha256"]
                ):
                    raise ValueError("Saved prediction source differs.")
            except (MLResourceError, ValueError, TypeError, KeyError, IndexError):
                raise failure("TOOL_SCOPE_CHANGED") from None
            return [actual["name"], predicted["name"]]
        names = [*config.get("variables", []), *config.get("categorical_variables", [])]
        names += list(config.get("reference_categories") or {})
        names += [config.get("group_variable"), config.get("target")]
    elif snapshot.kind == "ml":
        config = snapshot.document["experiment"].get("config") or {}
        names = [*config.get("features", []), *config.get("categorical_features", [])]
        names += [config.get("target"), config.get("group_variable"), config.get("time_variable")]
    else:
        raise failure("TOOL_RESULT_REQUIRED")
    names = [item for item in names if item is not None]
    if not names or any(not isinstance(item, str) or not 1 <= len(item) <= 255 for item in names):
        raise failure("TOOL_SCOPE_CHANGED")
    names = list(dict.fromkeys(names))
    if len(names) > 50:
        raise failure("TOOL_SCOPE_CHANGED")
    return names


async def read(db, owner, session_id, selection, source_version, *, artifact_only=False):
    """Local facts only; no model send, fit, or implicit source expansion."""
    from app.models.dataset import Dataset
    from app.models.user import User
    from app.preview.context import ml_facts, statistical_facts

    selection = DisclosureSelection.model_validate(selection)
    if artifact_only and (
        selection.datasets
        or not selection.artifact_ids
        or selection.purpose != "review"
        or selection.scope != "metadata_only"
        or selection.rows != 0
        or selection.acknowledge_raw
    ):
        raise failure("TOOL_SCOPE_CHANGED")
    context = await contexts.snapshot(db, owner, session_id, expected_source_version=source_version)
    attached = {str(item.artifact_id): item for item in context.artifacts}
    datasets = {str(item.dataset_id): item for item in context.datasets}
    columns = {item.dataset_id: item.columns for item in selection.datasets}
    actor = await db.get(User, owner, populate_existing=True)
    values = {}
    for identifier in selection.artifact_ids:
        if identifier not in attached:
            raise failure("TOOL_RESULT_NOT_SELECTED")
        snapshot, citation = await prove_result(db, owner, uuid.UUID(identifier))
        if len(snapshot.datasets) != 1:
            raise failure("TOOL_SCOPE_CHANGED")
        source = snapshot.datasets[0]
        dataset = datasets.get(source["id"])
        chosen = columns.get(source["id"])
        if artifact_only:
            _, sha, _ = await ledger._target(
                db, "dataset_version", uuid.UUID(source["id"]), owner, source["sha256"]
            )
            raw_dataset = await db.get(Dataset, uuid.UUID(source["id"]), populate_existing=True)
            if (
                raw_dataset is None
                or sha != source["sha256"]
                or raw_dataset.project_id != snapshot.project_id
            ):
                raise failure("TOOL_SCOPE_CHANGED")
            chosen = saved_columns(snapshot)
            if not set(chosen) <= {
                item["name"] for item in schema_snapshot(raw_dataset.schema_json)
            }:
                raise failure("TOOL_SCOPE_CHANGED")
            dataset = SimpleNamespace(
                dataset_id=raw_dataset.id,
                project_id=raw_dataset.project_id,
                sha256=raw_dataset.sha256,
                fields=[SimpleNamespace(reference=SimpleNamespace(name=name)) for name in chosen],
            )
        if (
            dataset is None
            or dataset.sha256 != source["sha256"]
            or dataset.project_id != snapshot.project_id
            or not chosen
            or not set(chosen) <= {item.reference.name for item in dataset.fields}
        ):
            raise failure("TOOL_SCOPE_CHANGED")
        raw_dataset = await db.get(Dataset, dataset.dataset_id, populate_existing=True)
        if raw_dataset is None:
            raise failure("TOOL_SCOPE_CHANGED")
        scope = PreviewScope(
            project_id=dataset.project_id,
            dataset_id=dataset.dataset_id,
            dataset_sha256=dataset.sha256,
            schema_sha256=digest(schema_snapshot(raw_dataset.schema_json)),
            columns=chosen,
            statistical_run_id=snapshot.source_id if snapshot.kind == "statistics" else None,
            ml_run_id=snapshot.source_id if snapshot.kind == "ml" else None,
        )
        facts = await (statistical_facts if snapshot.kind == "statistics" else ml_facts)(
            db, scope, actor, snapshot.source_id
        )
        citation.update(
            dataset_id=str(dataset.dataset_id),
            dataset_sha256=dataset.sha256,
            schema_sha256=scope.schema_sha256,
            source_version=context.source_version,
        )
        key = (snapshot.kind, snapshot.source_id)
        # Several selected outputs of one result keep every explicit citation.
        if key in values:
            values[key]["citations"].append(citation)
        else:
            values[key] = dict(scope=scope, facts=facts, citations=[citation])
    return values


async def explain(db, actor, request, values):
    from app.preview.extensions import explain_fields
    from app.schemas.preview_extensions import ExplanationRequest

    rendered = []
    sources = {}
    for reference in request.fields:
        key = (reference.kind, reference.run_id)
        item = values.get(key)
        if item is None:
            raise failure("TOOL_RESULT_NOT_SELECTED")
        single = ExplanationRequest(fields=[reference], interpretation=request.interpretation)
        actual = await explain_fields(db, item["scope"], actor, single, {reference.run_id}, False)
        field = actual["fields"][0]
        field["citations"] = [
            dict(citation, field_path=field["field_path"]) for citation in item["citations"]
        ]
        rendered.append(field)
        sources[str(item["scope"].dataset_id)] = actual["source"]
    return dict(
        kind="validated_explanation",
        state="validated",
        request=request.model_dump(mode="json"),
        sources=list(sources.values()),
        fields=rendered,
        interpretation=request.interpretation,
        computed=False,
        limitations=["解释仅引用已选择工件的保存字段；没有重新拟合或推断因果关系。"],
    )
