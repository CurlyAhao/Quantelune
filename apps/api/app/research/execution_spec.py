"""Read/normalize fixed trusted execution inputs without preparing a native side effect."""

import uuid
from typing import Any

from app.ml.resources import MAX_ROWS
from app.models.dataset import Dataset
from app.models.ml import MLExperiment
from app.models.workflow import Workflow
from app.research.contracts import logical_hash, parse_document
from app.services import research_service as ledger


async def describe(db, owner, run):
    captured = parse_document(run.input_json)
    parameters = captured["parameters"]
    allowed = {"operation", "dataset_id", "configuration", "experiment_id", "workflow_id"}
    if "p4_protocol" in parameters:
        from app.research.plan_input_proof import prove

        await prove(db, owner, run)
        allowed.add("p4_protocol")
    if set(parameters) - allowed:
        raise ledger.error("EXECUTION_CONFIGURATION_INVALID", 422)
    config = parameters.get("configuration", {})
    source = None
    configuration_source: dict[str, Any] | None = None
    workflow = None
    if run.kind == "ml" and parameters.get("experiment_id"):
        experiment = await db.get(MLExperiment, uuid.UUID(parameters["experiment_id"]))
        if experiment is None or not experiment.config_json:
            raise ledger.error("ACCESS_DENIED", 404)
        await ledger._project(db, experiment.project_id, owner)
        source = await db.get(Dataset, experiment.dataset_id)
        config = parse_document(experiment.config_json)
        if source is None or source.sha256 != experiment.dataset_sha256:
            raise ledger.error("BINDING_CHANGED")
        configuration_source = {
            "kind": "ml_experiment",
            "id": str(experiment.id),
            "hash": logical_hash(config),
        }
    elif run.kind == "workflow":
        workflow = await db.get(
            Workflow, uuid.UUID(parameters["workflow_id"]), populate_existing=True
        )
        if workflow is None or not workflow.graph_json or workflow.dataset_id is None:
            raise ledger.error("ACCESS_DENIED", 404)
        await ledger._project(db, workflow.project_id, owner)
        source = await db.get(Dataset, workflow.dataset_id, populate_existing=True)
        config = parse_document(workflow.graph_json)
        configuration_source = {
            "kind": "workflow",
            "id": str(workflow.id),
            "version": workflow.version,
            "hash": logical_hash(config),
        }
    else:
        source = await db.get(Dataset, uuid.UUID(parameters["dataset_id"]))
    if source is None:
        raise ledger.error("ACCESS_DENIED", 404)
    await ledger._target(db, "dataset_version", source.id, owner, source.sha256)
    attached = {
        (b["id"], b["sha256"]) for b in captured["attachments"] if b["kind"] == "dataset_version"
    }
    if (str(source.id), source.sha256) not in attached:
        raise ledger.error("SOURCE_NOT_ATTACHED")
    if (source.row_count or 0) > MAX_ROWS or source.file_format not in {"csv", "xlsx", "parquet"}:
        raise ledger.error("EXECUTION_INPUT_LIMIT", 422)
    operation = {
        "quality": "research_quality",
        "statistics": "research_statistics",
        "ml": "execute",
        "workflow": "research_workflow",
    }[run.kind]
    if run.kind in {"quality", "statistics"}:
        from app.research.domain_compute import normalize

        config = normalize(operation, config)
    elif run.kind == "ml":
        from app.ml.resources import validate_config_resources
        from app.services.ml_service import config_from_dict

        actual = config_from_dict(config)
        validate_config_resources(actual)
        config = actual.to_dict()
    else:
        from app.workflow.graph import validate_graph

        if not validate_graph(config)["valid"]:
            raise ledger.error("EXECUTION_CONFIGURATION_INVALID", 422)
    return source, config, configuration_source, operation, attached, workflow


async def signature(db, owner, run):
    from app.research.contracts import logical_hash

    source, config, origin, operation, attached, workflow = await describe(db, owner, run)
    if workflow is not None:
        from app.research.workflow_inputs import capture

        config = await capture(db, workflow, config, owner, attached, uuid.UUID(int=0))
    return logical_hash(
        {
            "version": 1,
            "input_hash": run.input_hash,
            "operation": operation,
            "source": {
                "id": str(source.id),
                "project": str(source.project_id),
                "sha256": source.sha256,
                "bytes": source.file_size,
                "format": source.file_format,
                "object_key": source.object_key,
            },
            "configuration": config,
            "configuration_source": origin,
        }
    )
