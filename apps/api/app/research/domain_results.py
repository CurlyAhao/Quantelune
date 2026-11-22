"""Read saved domain results with fresh ACL, version and byte verification. Never compute."""

import enum
import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.exceptions import StatForgeError
from app.models.dataset import Dataset
from app.models.ml import MLExperiment, MLRun
from app.models.quality import QualityFinding, QualityRun
from app.models.research import ResearchArtifact
from app.models.statistics import StatFinding, StatisticalRun, StatResult
from app.models.workflow import Workflow, WorkflowRun
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document


@dataclass(frozen=True)
class SavedOutput:
    object_key: str
    kind: str
    media_type: str
    byte_size: int
    sha256: str

    def semantic(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "media_type": self.media_type,
            "bytes": self.byte_size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DomainSnapshot:
    kind: str
    source_id: uuid.UUID
    project_id: uuid.UUID
    state: State
    datasets: tuple[dict[str, str], ...]
    document: dict[str, Any]
    outputs: tuple[SavedOutput, ...]

    @property
    def logical_hash(self) -> str:
        return logical_hash(self.document)


def invalid() -> StatForgeError:
    return StatForgeError(
        409, "RESEARCH_DOMAIN_INVALID", "Saved domain result is incomplete or changed."
    )


_ENVELOPE = {
    "id",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "created_by",
    "project_id",
    "dataset_id",
    "profile_id",
    "quality_run_id",
    "statistical_run_id",
    "experiment_id",
    "workflow_id",
    "artifact_key",
}


def _record(item: Any) -> dict[str, Any]:
    """Only known mapped columns; no recursive key deletion from user parameters."""
    result = {}
    for column in inspect(type(item)).columns:
        name = column.key
        if name in _ENVELOPE:
            continue
        value = getattr(item, name)
        if name.endswith("_json") and value is not None:
            value = parse_document(value)
        if isinstance(value, enum.Enum):
            value = value.value
        result[name.removesuffix("_json")] = value
    canonical_bytes(result)
    return result


async def _dataset(
    db: AsyncSession,
    owner: uuid.UUID,
    dataset_id: uuid.UUID,
    project_id: uuid.UUID,
    sha: str,
) -> dict[str, str]:
    from app.services import research_service as ledger

    await ledger._project(db, project_id, owner)
    actual, current_sha, _ = await ledger._target(db, "dataset_version", dataset_id, owner, sha)
    if actual != project_id or current_sha != sha or len(sha) != 64:
        raise invalid()
    return {"id": str(dataset_id), "sha256": sha}


async def _quality(db: AsyncSession, owner: uuid.UUID, source_id: uuid.UUID) -> DomainSnapshot:
    metadata = (
        await db.execute(
            select(
                QualityRun.dataset_id,
                QualityRun.dataset_sha256,
                Dataset.project_id,
            )
            .join(Dataset, QualityRun.dataset_id == Dataset.id)
            .where(QualityRun.id == source_id)
        )
    ).one_or_none()
    if metadata is None:
        raise invalid()
    binding = await _dataset(
        db, owner, metadata.dataset_id, metadata.project_id, metadata.dataset_sha256
    )
    item = await db.scalar(
        select(QualityRun).options(noload("*")).where(QualityRun.id == source_id),
        execution_options={"populate_existing": True},
    )
    if item is None or item.status.value not in {"completed", "completed_with_errors", "failed"}:
        raise invalid()
    findings = (
        await db.scalars(
            select(QualityFinding).where(
                QualityFinding.quality_run_id == source_id,
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    record = _record(item)
    values = [_record(finding) for finding in findings]
    values.sort(key=logical_hash)
    state = State.COMPLETED if item.status.value == "completed" else State.FAILED
    if state is State.COMPLETED:
        counts = {
            "passed": item.passed_count,
            "warning": item.warning_count,
            "failed": item.failed_count,
            "error": item.error_count,
            "skipped": item.skipped_count,
        }
        observed = Counter(value["status"] for value in values)
        if (
            not values
            or item.total_rules <= 0
            or min(counts.values()) < 0
            or observed != Counter(counts)
            or item.error_count != 0
            or not record["score"]
            or record["score"]["total_score"] != item.overall_score
            or not item.ruleset_version
        ):
            raise invalid()
    document = {"domain_kind": "quality", "run": record, "findings": values}
    return DomainSnapshot(
        "quality", source_id, metadata.project_id, state, (binding,), document, ()
    )


async def _statistics(db: AsyncSession, owner: uuid.UUID, source_id: uuid.UUID) -> DomainSnapshot:
    metadata = (
        await db.execute(
            select(
                StatisticalRun.project_id,
                StatisticalRun.dataset_id,
                StatisticalRun.dataset_sha256,
            ).where(StatisticalRun.id == source_id)
        )
    ).one_or_none()
    if metadata is None:
        raise invalid()
    binding = await _dataset(
        db, owner, metadata.dataset_id, metadata.project_id, metadata.dataset_sha256
    )
    item = await db.scalar(
        select(StatisticalRun).options(noload("*")).where(StatisticalRun.id == source_id),
        execution_options={"populate_existing": True},
    )
    if item is None or item.status.value not in {"completed", "not_applicable", "failed"}:
        raise invalid()
    results = (
        await db.scalars(
            select(StatResult)
            .options(noload("*"))
            .where(
                StatResult.statistical_run_id == source_id,
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    findings = (
        await db.scalars(
            select(StatFinding)
            .options(noload("*"))
            .where(
                StatFinding.statistical_run_id == source_id,
            ),
            execution_options={"populate_existing": True},
        )
    ).all()
    values = [_record(result) for result in results]
    values.sort(key=logical_hash)
    state = State.COMPLETED if item.status.value == "completed" else State.FAILED
    if state is State.COMPLETED:
        if not values or not item.methodset_version or item.error_code:
            raise invalid()
        for value in values:
            saved = value["result"]
            if (
                not isinstance(saved, dict)
                or saved.get("status") != "completed"
                or saved.get("method") != item.method
                or not value["software_versions"]
            ):
                raise invalid()
    document = {
        "domain_kind": "statistics",
        "run": _record(item),
        "results": values,
        "findings": sorted([_record(finding) for finding in findings], key=logical_hash),
    }
    return DomainSnapshot(
        "statistics", source_id, metadata.project_id, state, (binding,), document, ()
    )


async def _ml(db: AsyncSession, owner: uuid.UUID, source_id: uuid.UUID) -> DomainSnapshot:
    from app.services import research_service as ledger

    metadata = (
        await db.execute(
            select(
                MLExperiment.project_id,
                MLExperiment.dataset_id,
                MLExperiment.dataset_sha256,
                MLExperiment.id.label("experiment_id"),
            )
            .join(MLRun, MLRun.experiment_id == MLExperiment.id)
            .where(MLRun.id == source_id)
        )
    ).one_or_none()
    if metadata is None:
        raise invalid()
    binding = await _dataset(
        db, owner, metadata.dataset_id, metadata.project_id, metadata.dataset_sha256
    )
    item = await db.scalar(
        select(MLRun).options(noload("*")).where(MLRun.id == source_id),
        execution_options={"populate_existing": True},
    )
    experiment = await db.scalar(
        select(MLExperiment)
        .options(noload("*"))
        .where(
            MLExperiment.id == metadata.experiment_id,
        ),
        execution_options={"populate_existing": True},
    )
    if item is None or experiment is None or item.status.value not in {"completed", "failed"}:
        raise invalid()
    record = _record(item)
    state = State.COMPLETED if item.status.value == "completed" else State.FAILED
    outputs: tuple[SavedOutput, ...] = ()
    if state is State.COMPLETED:
        models, split, meta = record["metrics"], record["split"], record["artifact_meta"]
        successful = [model for model in models if model.get("status") == "completed"]
        if (
            not successful
            or not record["comparison"]
            or not record["software_versions"]
            or not isinstance(meta, dict)
            or not split.get("evaluation_protocol")
            or item.error_code
        ):
            raise invalid()
        expected = f"ml-artifacts/{metadata.project_id}/{experiment.id}/{source_id}.joblib"
        if item.artifact_key != expected or set(meta["models"]) != {
            m["model_id"] for m in successful
        }:
            raise invalid()
        ledger.verify_local_bytes(expected, meta["bytes"], meta["sha256"])
        outputs = (
            SavedOutput(
                expected, "model", "application/octet-stream", meta["bytes"], meta["sha256"]
            ),
        )
    document = {
        "domain_kind": "ml",
        "experiment": _record(experiment),
        "run": record,
        "outputs": [output.semantic() for output in outputs],
    }
    return DomainSnapshot(
        "ml", source_id, metadata.project_id, state, (binding,), document, outputs
    )


def _graph_semantic(graph: dict, results: dict, order: list) -> dict[str, Any]:
    """Normalize only generated graph/reference identities, retaining every parameter."""
    names = {node["id"]: f"node-{index}" for index, node in enumerate(graph["nodes"])}
    source_shas = {
        source["dataset_id"]: source["dataset_sha256"] for source in graph["_provenance"]["sources"]
    }

    def source_semantic(source: dict) -> dict:
        result = {
            key: value
            for key, value in source.items()
            if key
            not in {"dataset_id", "parent_dataset_id", "workflow_id", "workflow_run_id", "node_id"}
        }
        if source.get("parent_dataset_id"):
            result["parent_dataset_sha256"] = source_shas[source["parent_dataset_id"]]
        return result

    nodes = []
    for node in graph["nodes"]:
        data = node.get("data", {})
        if node["type"] in {"dataset", "data.source"} and data.get("dataset_id"):
            data = {key: value for key, value in data.items() if key != "dataset_id"}
            data["dataset_sha256"] = source_shas[node["data"]["dataset_id"]]
        nodes.append({**node, "id": names[node["id"]], "data": data})
    edges = [
        {
            key: names[value] if key in {"source", "target"} else value
            for key, value in edge.items()
            if key != "id"
        }
        for edge in graph["edges"]
    ]
    normalized = {}
    for identifier, result in results.items():
        output = result.get("output")
        if isinstance(output, dict) and output.get("dataset_id"):
            output = source_semantic(output)
        if isinstance(output, dict) and isinstance(output.get("source"), dict):
            output = {**output, "source": source_semantic(output["source"])}
        if isinstance(output, dict) and isinstance(output.get("artifacts"), list):
            output = {
                **output,
                "artifacts": [
                    {
                        key: value
                        for key, value in reference.items()
                        if key
                        not in {"id", "object_key", "created_at", "download_url", "provenance"}
                    }
                    for reference in output["artifacts"]
                ],
            }
        normalized[names[identifier]] = {**result, "output": output}
    provenance = graph["_provenance"]
    return {
        "schema_version": graph.get("schema_version", 1),
        "nodes": nodes,
        "edges": edges,
        "order": [names[name] for name in order],
        "node_results": normalized,
        "workflow_version": provenance["workflow_version"],
        "workflow_engine": provenance["workflow_engine"],
        "sources": [source_semantic(source) for source in provenance["sources"]],
    }


async def _workflow(db: AsyncSession, owner: uuid.UUID, source_id: uuid.UUID) -> DomainSnapshot:
    from app.services import research_service as ledger

    metadata = (
        await db.execute(
            select(Workflow.project_id, WorkflowRun.dataset_id)
            .join(
                WorkflowRun,
                WorkflowRun.workflow_id == Workflow.id,
            )
            .where(WorkflowRun.id == source_id)
        )
    ).one_or_none()
    if metadata is None:
        raise invalid()
    await ledger._project(db, metadata.project_id, owner)
    item = await db.scalar(
        select(WorkflowRun).options(noload("*")).where(WorkflowRun.id == source_id),
        execution_options={"populate_existing": True},
    )
    if item is None or item.status.value not in {"completed", "failed"}:
        raise invalid()
    graph = parse_document(item.graph_snapshot_json or "null")
    results = parse_document(item.node_results_json or "null")
    order = parse_document(item.order_json or "null")
    state = State.COMPLETED if item.status.value == "completed" else State.FAILED
    if not isinstance(graph, dict) or not isinstance(results, dict) or not isinstance(order, list):
        raise invalid()
    sources = graph["_provenance"]["sources"]
    bindings = [
        await _dataset(
            db,
            owner,
            uuid.UUID(source["dataset_id"]),
            metadata.project_id,
            source["dataset_sha256"],
        )
        for source in sources
    ]
    if metadata.dataset_id is not None and not any(
        b["id"] == str(metadata.dataset_id) for b in bindings
    ):
        raise invalid()
    outputs = []
    for result in results.values():
        for reference in (result.get("output") or {}).get("artifacts", []):
            identifier = reference["id"]
            prefix = f"workflow-artifacts/{metadata.project_id}/{source_id}/{identifier}/"
            key = reference["object_key"]
            if (
                len(identifier) != 32
                or any(c not in "0123456789abcdef" for c in identifier)
                or not key.startswith(prefix)
                or ".." in key
            ):
                raise invalid()
            ledger.verify_local_bytes(key, reference["bytes"], reference["sha256"])
            outputs.append(
                SavedOutput(
                    key,
                    reference.get("kind", "output"),
                    reference["media_type"],
                    reference["bytes"],
                    reference["sha256"],
                )
            )
    if state is State.COMPLETED:
        if (
            not bindings
            or not results
            or len(order) != len(results)
            or set(order) != set(results)
            or len(set(order)) != len(order)
            or any(result.get("status") != "completed" for result in results.values())
        ):
            raise invalid()
    document = {
        "domain_kind": "workflow",
        "status": item.status.value,
        "saved_graph": _graph_semantic(graph, results, order),
        "error_message": item.error_message,
    }
    return DomainSnapshot(
        "workflow", source_id, metadata.project_id, state, tuple(bindings), document, tuple(outputs)
    )


async def load_snapshot(
    db: AsyncSession,
    owner: uuid.UUID,
    kind: str,
    source_id: uuid.UUID,
) -> DomainSnapshot:
    loaders = {"quality": _quality, "statistics": _statistics, "ml": _ml, "workflow": _workflow}
    if kind not in loaders:
        raise invalid()
    try:
        snapshot = await loaders[kind](db, owner, source_id)
        canonical_bytes(snapshot.document)
        return snapshot
    except (ValueError, TypeError, KeyError, AttributeError):
        # Never replace corrupted legacy JSON with an empty, successful result.
        raise invalid() from None


def verify_artifact(
    item: ResearchArtifact,
    snapshot: DomainSnapshot,
    owner: uuid.UUID,
) -> None:
    from app.services import research_service as ledger

    try:
        provenance = parse_document(item.provenance_json)
        if (
            item.project_id != snapshot.project_id
            or provenance["domain_kind"] != snapshot.kind
            or provenance["domain_run_id"] != str(snapshot.source_id)
            or provenance["snapshot_hash"] != snapshot.logical_hash
            or provenance["datasets"] != list(snapshot.datasets)
        ):
            raise invalid()
        if item.kind == "domain_result":
            expected = canonical_bytes(snapshot.document)
            digest = hashlib.sha256(expected).hexdigest()
            key = f"research-artifacts/{owner}/{item.id}/{digest}.json"
            if (
                item.object_key != key
                or item.sha256 != digest
                or item.byte_size != len(expected)
                or item.logical_hash != snapshot.logical_hash
                or item.media_type != "application/json"
            ):
                raise invalid()
        else:
            matches = [
                output for output in snapshot.outputs if output.object_key == item.object_key
            ]
            if len(matches) != 1:
                raise invalid()
            output = matches[0]
            if (
                item.kind != output.kind
                or item.byte_size != output.byte_size
                or item.sha256 != output.sha256
                or item.media_type != output.media_type
                or item.logical_hash != logical_hash(output.semantic())
            ):
                raise invalid()
        ledger.verify_local_bytes(item.object_key, item.byte_size, item.sha256)
    except (ValueError, TypeError, KeyError):
        raise invalid() from None
