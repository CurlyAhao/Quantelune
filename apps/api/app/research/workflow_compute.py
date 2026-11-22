"""Fixed Workflow catalog inside one already OS-bounded native worker.

No Session, provider, user code, network or additional process admission exists
here. Existing node semantics and scientific kernels are reused through narrow
storage/model adapters. These are produced objects, not committed DB records.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TypeVar, cast

import polars as pl

from app.ml.bounded_writer import MAX_ARTIFACT_BYTES, BoundedWriter
from app.ml.config import MLConfig
from app.ml.resources import MAX_FILE_BYTES, MAX_ROWS
from app.models.dataset import Dataset, UploadStatus
from app.models.workflow import Workflow, WorkflowRun
from app.research.contracts import logical_hash
from app.research.output_handles import directories, long_path
from app.research.produced import read_bytes, sync_file
from app.services.file_validation_service import validate_file
from app.services.profiling_service import load_dataframe
from app.workflow.artifacts import RenderedArtifact, json_bytes
from app.workflow.runtime import FrameValue, WorkflowRuntime, execute_domain_graph

_T = TypeVar("_T")


class NativeTask:
    """The parent owns the native deadline, cancellation and Job/process group."""

    async def check(self) -> None:
        return None

    async def wait(self, awaitable: Awaitable[_T]) -> _T:
        return await awaitable


class DetachedRuntime(WorkflowRuntime):
    def __init__(self, owned: Path, request: dict):
        self.owned, self.request = owned, request
        self.context = request["config"]
        self.workflow = Workflow(
            id=uuid.UUID(self.context["workflow_id"]),
            project_id=uuid.UUID(self.context["project_id"]),
            dataset_id=uuid.UUID(self.context["dataset_id"]),
            version=self.context["workflow_version"],
        )
        self.run = WorkflowRun(id=uuid.UUID(self.context["workflow_run_id"]))
        self.task = NativeTask()
        self.sources: dict[str, dict] = {}
        self.datasets: dict[str, Dataset] = {}
        self.paths: dict[str, Path] = {}
        self.objects: list[dict] = []
        self.dataset_records: list[dict] = []
        self.model_records: list[dict] = []
        self.nid = ""
        self.total_bytes = 0

    @asynccontextmanager
    async def node_transaction(self):
        # Match the original node savepoint's semantic rollback. Actual private
        # bytes remain registered and retained as failure evidence; they do not
        # become derived Dataset/ML records or enter downstream source lineage.
        dataset_count, model_count = len(self.dataset_records), len(self.model_records)
        datasets, paths = dict(self.datasets), dict(self.paths)
        try:
            yield
        except BaseException:
            del self.dataset_records[dataset_count:]
            del self.model_records[model_count:]
            self.datasets, self.paths = datasets, paths
            raise

    async def node(self, nid, node_type, raw, inputs, legacy):
        self.nid = nid
        return await super().node(nid, node_type, raw, inputs, legacy)

    def save(self, name: str, content: bytes, role: str) -> dict:
        with BoundedWriter(self.owned / name, MAX_ARTIFACT_BYTES, guarded=True) as sink:
            sink.write(content)
            sink.flush()
        sync_file(self.owned / name)
        item = {"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        self.objects.append(item)
        self.total_bytes += len(content)
        return {**item, "role": role}

    async def source(self, data: dict) -> FrameValue:
        identity = data.get("dataset_id") or str(self.workflow.dataset_id)
        item = self.context["sources"].get(identity)
        if item is None or identity not in self.datasets:
            raise ValueError("WORKFLOW_SOURCE_UNREGISTERED")
        for ancestor in item["ancestors"]:
            self.sources[ancestor["dataset_id"]] = ancestor
        ref = {
            "dataset_id": identity,
            "dataset_sha256": item["sha256"],
            "file_format": item["format"],
        }
        self.sources[identity] = {**ref, "lineage": item["lineage"]}
        return FrameValue(
            self.datasets[identity],
            lineage=list(item["lineage"]),
            source_refs=[*item["ancestors"], ref],
        )

    async def frame(self, value: FrameValue) -> pl.DataFrame:
        if value.frame is None:
            value.frame = load_dataframe(
                str(self.paths[str(value.dataset.id)]),
                value.dataset.file_format,
                value.dataset.sheet_name,
                value.dataset.csv_encoding,
            )
        if value.frame.height > MAX_ROWS or value.frame.estimated_size() > MAX_ARTIFACT_BYTES:
            raise ValueError("WORKFLOW_FRAME_LIMIT")
        return value.frame

    async def materialize(self, value: FrameValue) -> Dataset:
        if not value.dirty:
            return value.dataset
        frame = await self.frame(value)
        identity = self.context["identities"][self.nid]["dataset"]
        filename = f"wf-{uuid.UUID(identity).hex}-input.parquet"
        path = self.owned / filename
        with BoundedWriter(path, MAX_FILE_BYTES, guarded=True) as sink:
            frame.write_parquet(cast(IO[bytes], sink))
            sink.flush()
        sync_file(path)
        content = read_bytes(path, MAX_FILE_BYTES)
        digest = hashlib.sha256(content).hexdigest()
        self.objects.append({"name": filename, "bytes": len(content), "sha256": digest})
        self.total_bytes += len(content)
        metadata = {
            "version": 1,
            "dataset_id": identity,
            "dataset_sha256": digest,
            "parent_dataset_id": str(value.dataset.id),
            "parent_sha256": value.dataset.sha256,
            "lineage": value.lineage,
            "ancestors": value.source_refs,
            "workflow_id": str(self.workflow.id),
            "workflow_run_id": str(self.run.id),
            "workflow_version": self.workflow.version,
            "created_at": datetime.now(UTC).isoformat(),
        }
        meta = self.save(
            f"wf-{uuid.UUID(identity).hex}-provenance.json", json_bytes(metadata), "provenance"
        )
        prefix = f"workflow-derived-data/{self.workflow.project_id}/{identity}/{meta['sha256']}"
        validated = validate_file(str(path), "parquet", preview_rows=0)
        key = prefix + "/input.parquet"
        dataset = Dataset(
            id=uuid.UUID(identity),
            project_id=self.workflow.project_id,
            file_format="parquet",
            sha256=digest,
            file_size=len(content),
            object_key=key,
            upload_status=UploadStatus.READY,
            row_count=validated.row_count,
            column_count=validated.column_count,
            schema_json=validated.schema_json(),
            preview_json=validated.preview_json(),
        )
        self.dataset_records.append(
            {
                "id": identity,
                "project_id": str(self.workflow.project_id),
                "sha256": digest,
                "bytes": len(content),
                "object_key": key,
                "metadata_key": prefix + "/provenance.json",
                "staged_name": filename,
                "metadata_name": meta["name"],
                "row_count": validated.row_count,
                "column_count": validated.column_count,
                "schema_json": validated.schema_json(),
                "preview_json": validated.preview_json(),
            }
        )
        self.datasets[identity], self.paths[identity] = dataset, path
        self.sources[identity] = {
            "dataset_id": identity,
            "dataset_sha256": digest,
            "file_format": "parquet",
            "parent_dataset_id": str(value.dataset.id),
            "lineage": value.lineage,
        }
        value.source_refs = [
            *value.source_refs,
            {"dataset_id": identity, "dataset_sha256": digest, "file_format": "parquet"},
        ]
        value.dataset, value.dirty = dataset, False
        return dataset

    def domain_operation(self, dataset: Dataset, config: MLConfig, operation: str) -> dict:
        # Reuse the exact ML snapshot/preflight/allocation/fit serializer in the
        # same native Job. No nested worker or second global slot is requested.
        from app.ml.worker import _execute

        suffix = uuid.UUID(self.context["identities"][self.nid]["ml_run"]).hex
        scratch = self.owned / ("node-" + suffix + "-" + operation)
        scratch.mkdir()
        with directories(scratch):
            output = _execute(
                scratch,
                {
                    "operation": operation,
                    "source_path": str(self.paths[str(dataset.id)]),
                    "file_format": dataset.file_format,
                    "config": config.to_dict(),
                    "dataset_sha256": dataset.sha256,
                    "intent_hash": self.request["intent_hash"],
                    "artifact_target": None,
                },
            )
            if operation == "execute" and output["result"].get("status") == "completed":
                model = self.save(
                    "wf-" + suffix + "-model.joblib",
                    read_bytes(scratch / "model.joblib", MAX_ARTIFACT_BYTES),
                    "model",
                )
                if any(model[k] != output["artifact_meta"][k] for k in ("bytes", "sha256")):
                    raise ValueError("WORKFLOW_ML_ARTIFACT_MISMATCH")
                output["staged_model"] = model["name"]
        return output

    async def partition(self, dataset: Dataset, config: MLConfig) -> dict:
        return self.domain_operation(dataset, config, "partition")["partition"]

    async def fit_model(self, nid: str, node_type: str, dataset: Dataset, config: MLConfig) -> dict:
        output = self.domain_operation(dataset, config, "execute")
        result = output["result"]
        if result.get("status") != "completed" or not output.get("staged_model"):
            raise ValueError(result.get("error_code") or "WORKFLOW_ML_NOT_COMPLETED")
        ids = self.context["identities"][nid]
        key = (
            f"ml-artifacts/{self.workflow.project_id}/{ids['ml_experiment']}/{ids['ml_run']}.joblib"
        )
        self.model_records.append(
            {
                "id": ids["ml_run"],
                "experiment_id": ids["ml_experiment"],
                "dataset_id": str(dataset.id),
                "dataset_sha256": dataset.sha256,
                "config": config.to_dict(),
                "result": result,
                "artifact_meta": output["artifact_meta"],
                "artifact_key": key,
                "staged_name": output["staged_model"],
                "node_id": nid,
            }
        )
        source = {
            "dataset_id": str(dataset.id),
            "dataset_sha256": dataset.sha256,
            "file_format": dataset.file_format,
            "ml_run_id": ids["ml_run"],
            "ml_experiment_id": ids["ml_experiment"],
            "workflow_id": str(self.workflow.id),
            "workflow_run_id": str(self.run.id),
            "workflow_version": self.workflow.version,
            "node_id": nid,
            "software_versions": result.get("software_versions", {}),
            "node_type": node_type,
            "artifact": output["artifact_meta"],
            "model_download_url": f"/api/v1/ml/runs/{ids['ml_run']}/artifacts",
        }
        return {
            "status": "completed",
            "task_type": config.task_type.value,
            "models": result.get("models", []),
            "split": result.get("split", {}),
            "comparison": result.get("comparison", {}),
            "leakage_checks": result.get("leakage_checks", []),
            "software_versions": result.get("software_versions", {}),
            "input_config": config.to_dict(),
            "source": source,
        }

    async def save_file(self, nid: str, file: RenderedArtifact, provenance: dict) -> dict:
        identity = uuid.UUID(self.context["identities"][nid]["artifact"]).hex
        # Generated filenames never determine the local private path.
        item = self.save("wf-" + identity + "-output.bin", file.content, "output")
        prefix = f"workflow-artifacts/{self.workflow.project_id}/{self.run.id}/{identity}"
        return {
            "id": identity,
            "object_key": f"{prefix}/{file.filename}",
            "filename": file.filename,
            "media_type": file.media_type,
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "staged_name": item["name"],
            "includes_raw_rows": file.includes_raw_rows,
            "created_at": datetime.now(UTC).isoformat(),
            "provenance": provenance,
            "download_url": f"/api/v1/workflow/runs/{self.run.id}/artifacts/{identity}",
        }


def compute(owned: Path, request: dict) -> dict:
    owned = Path(long_path(owned))
    context = request["config"]
    if context["graph_hash"] != logical_hash(context["graph"]):
        raise ValueError("WORKFLOW_GRAPH_CHANGED")
    if set(request["workflow_sources"]) != set(context["sources"]):
        raise ValueError("WORKFLOW_SOURCE_CHANGED")
    runtime = DetachedRuntime(owned, request)
    from app.ml.worker import _snapshot

    for identity, source in context["sources"].items():
        scratch = owned / ("source-" + uuid.UUID(identity).hex)
        scratch.mkdir()
        with directories(scratch):
            path = _snapshot(
                scratch,
                {
                    "operation": "research_workflow",
                    "source_path": request["workflow_sources"][identity],
                    "file_format": source["format"],
                },
            )
            content = read_bytes(path, MAX_FILE_BYTES)
            if (
                len(content) != source["bytes"]
                or hashlib.sha256(content).hexdigest() != source["sha256"]
            ):
                raise ValueError("WORKFLOW_SOURCE_CHANGED")
        runtime.paths[identity] = path
        runtime.datasets[identity] = Dataset(
            id=uuid.UUID(identity),
            project_id=uuid.UUID(source["project_id"]),
            sha256=source["sha256"],
            file_size=source["bytes"],
            file_format=source["format"],
            object_key=source["object_key"],
            preview_json=json.dumps(
                {"sheet_name": source.get("sheet_name"), "csv_encoding": source.get("csv_encoding")}
            ),
            upload_status=UploadStatus.READY,
        )
    result = asyncio.run(execute_domain_graph(context["graph"], runtime))
    return {
        **result,
        "workflow_objects": runtime.objects,
        "dataset_records": runtime.dataset_records,
        "model_records": runtime.model_records,
    }
