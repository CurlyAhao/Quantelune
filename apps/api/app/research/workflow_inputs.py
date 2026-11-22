"""Capture every authorized Workflow source before a native dispatch."""

import uuid

from sqlalchemy import select

from app.ml.resources import MAX_ROWS
from app.models.dataset import Dataset
from app.research.contracts import logical_hash, parse_document
from app.research.dataset_metadata import preview_field
from app.services import research_service as ledger
from app.workflow.node_parameters import normalize_node_data
from app.workflow.storage import verified_output


async def capture(db, workflow, graph, owner, attached, domain_id) -> dict:
    sources = {}
    for node in graph["nodes"]:
        if node["type"] != "dataset":
            continue
        data = normalize_node_data(
            "dataset", node.get("data", {}), legacy=graph.get("schema_version", 1) == 1
        )
        identity = uuid.UUID(data["dataset_id"]) if data.get("dataset_id") else workflow.dataset_id
        source = (
            await db.execute(
                select(
                    Dataset.id,
                    Dataset.project_id,
                    Dataset.sha256,
                    Dataset.file_size,
                    Dataset.row_count,
                    Dataset.file_format,
                    Dataset.object_key,
                ).where(Dataset.id == identity)
            )
        ).one_or_none()
        if source is None or source.project_id != workflow.project_id:
            raise ledger.error("ACCESS_DENIED", 404)
        actual_project, _sha, _projects = await ledger._target(
            db, "dataset_version", source.id, owner, source.sha256
        )
        if actual_project != workflow.project_id:
            raise ledger.error("ACCESS_DENIED", 404)
        if (str(source.id), source.sha256) not in attached:
            raise ledger.error("SOURCE_NOT_ATTACHED")
        source_metadata = (
            await db.execute(
                select(preview_field(db, "csv_encoding"), preview_field(db, "sheet_name")).where(
                    Dataset.id == source.id, Dataset.project_id == workflow.project_id
                )
            )
        ).one()
        if (source.row_count or 0) > MAX_ROWS or source.file_format not in {
            "csv",
            "xlsx",
            "parquet",
        }:
            raise ledger.error("EXECUTION_INPUT_LIMIT", 422)
        lineage, ancestors = [], []
        if source.object_key.startswith("workflow-derived-data/"):
            prefix = f"workflow-derived-data/{workflow.project_id}/{source.id}/"
            tail = source.object_key.removeprefix(prefix)
            if not source.object_key.startswith(prefix) or len(tail.split("/")) != 2:
                raise ledger.error("SOURCE_UNPROVEN")
            digest, name = tail.split("/")
            if name != "input.parquet" or len(digest) != 64:
                raise ledger.error("SOURCE_UNPROVEN")
            metadata = parse_document(
                (await verified_output(prefix + digest + "/provenance.json", None, digest)).decode()
            )
            if (
                metadata.get("version") != 1
                or metadata.get("dataset_id") != str(source.id)
                or metadata.get("dataset_sha256") != source.sha256
                or not isinstance(metadata.get("lineage"), list)
                or not isinstance(metadata.get("ancestors"), list)
                or len(metadata["lineage"]) > 100
                or len(metadata["ancestors"]) > 100
            ):
                raise ledger.error("SOURCE_UNPROVEN")
            for ancestor in metadata["ancestors"]:
                ancestor_project, _sha, _projects = await ledger._target(
                    db,
                    "dataset_version",
                    uuid.UUID(ancestor["dataset_id"]),
                    owner,
                    ancestor["dataset_sha256"],
                )
                if ancestor_project != workflow.project_id:
                    raise ledger.error("ACCESS_DENIED", 404)
                if (ancestor["dataset_id"], ancestor["dataset_sha256"]) not in attached:
                    raise ledger.error("SOURCE_NOT_ATTACHED")
            ancestors = metadata["ancestors"]
            lineage = [
                {
                    "node_type": step["node_type"],
                    "parameters": normalize_node_data(step["node_type"], step["parameters"]),
                }
                for step in metadata["lineage"]
            ]
        sources[str(source.id)] = {
            "dataset_id": str(source.id),
            "project_id": str(source.project_id),
            "sha256": source.sha256,
            "bytes": source.file_size,
            "format": source.file_format,
            "object_key": source.object_key,
            "lineage": lineage,
            "ancestors": ancestors,
        }
        if source_metadata.csv_encoding is not None:
            sources[str(source.id)]["csv_encoding"] = source_metadata.csv_encoding
        if source_metadata.sheet_name is not None:
            sources[str(source.id)]["sheet_name"] = source_metadata.sheet_name
    # IDs are server allocated once in the immutable intent, never after fitting.
    identities = {
        node["id"]: {
            kind: str(uuid.uuid5(domain_id, kind + ":" + node["id"]))
            for kind in ("dataset", "ml_experiment", "ml_run", "artifact")
        }
        for node in graph["nodes"]
    }
    return {
        "graph": graph,
        "sources": sources,
        "identities": identities,
        "workflow_id": str(workflow.id),
        "workflow_version": workflow.version,
        "workflow_run_id": str(domain_id),
        "owner_id": str(owner),
        "project_id": str(workflow.project_id),
        "dataset_id": str(workflow.dataset_id),
        "graph_hash": logical_hash(graph),
    }
