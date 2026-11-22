"""Async domain execution: real datasets, bounded MLRun and owned output files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Protocol, TypeVar, cast

import anyio
import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.config import settings
from app.exceptions import DatasetAccessDeniedError, StatForgeError
from app.ml.bounded_writer import BoundedWriter
from app.ml.config import MLConfig
from app.ml.resources import MAX_FILE_BYTES, MAX_ROWS, validate_config_resources
from app.ml.types import MODEL_METADATA, MLModelId, MLTaskType, SplitStrategy
from app.ml.worker_control import MLTaskScope
from app.models.dataset import Dataset, UploadStatus
from app.models.ml import MLRunStatus
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun
from app.services import ml_service, ml_storage, storage_service
from app.services.file_validation_service import validate_file
from app.workflow.artifacts import (
    RenderedArtifact,
    aggregate_result,
    json_bytes,
    render_chart,
    render_export,
    render_notebook,
    render_report,
    render_table,
)
from app.workflow.catalog import get_spec
from app.workflow.error_codes import node_error_code
from app.workflow.executor import STAT_NODE_METHODS, _apply_cleaning, _run_stat_node
from app.workflow.graph import validate_graph
from app.workflow.node_parameters import normalize_node_data
from app.workflow.publication import pending_object
from app.workflow.storage import delete_output, verified_output


@dataclass
class FrameValue:
    dataset: Dataset
    frame: pl.DataFrame | None = None
    lineage: list[dict] = field(default_factory=list)
    split: dict | None = None
    partition: dict | None = None
    preprocessing: dict = field(default_factory=dict)
    selection_k: int | None = None
    dirty: bool = False
    source_refs: list[dict] = field(default_factory=list)


@dataclass
class ResultValue:
    result: dict


@dataclass
class ModelValue(ResultValue):
    pass


@dataclass
class ArtifactValue:
    files: list[RenderedArtifact]
    references: list[dict]


def resolve_ml_config(value: FrameValue, node_type: str, raw: dict, data: dict) -> MLConfig:
    merged = dict(value.split or {})
    if value.split:
        # An explicit Split is an actual partition. It cannot be silently replaced.
        for key in (
            "target",
            "task_type",
            "test_size",
            "val_size",
            "seed",
            "n_splits",
            "group_variable",
            "time_variable",
        ):
            if key in raw and raw[key] is not None and data[key] != value.split.get(key):
                raise ValueError("WORKFLOW_SPLIT_OVERRIDE")
        if "split_strategy" in raw and data["split_strategy"] != value.split.get("strategy"):
            raise ValueError("WORKFLOW_SPLIT_OVERRIDE")
    for key, item in data.items():
        if item is not None and (key not in merged or key in raw):
            merged[key] = item
    task = MLTaskType(merged.get("task_type", "regression"))
    model = MLModelId(
        raw.get("model_id") or ("ridge" if task == MLTaskType.REGRESSION else "logistic_regression")
    )
    if task.value not in MODEL_METADATA[model]["tasks"]:
        raise ValueError("WORKFLOW_MODEL_TASK_MISMATCH")
    target = merged.get("target")
    features = merged.get("features", [])
    categorical = merged.get("categorical", [])
    if not target or not features or target in {*features, *categorical}:
        raise ValueError("WORKFLOW_ML_COLUMNS_REQUIRED")
    preprocessing = {
        "standardize": True,
        "impute_numeric": "median",
        "impute_categorical": "most_frequent",
        "handle_outliers": False,
        **value.preprocessing,
    }
    for key in preprocessing:
        if raw.get(key) is not None:
            preprocessing[key] = data[key]
    config = MLConfig(
        task_type=task,
        target=target,
        features=tuple(features),
        categorical_features=tuple(categorical),
        group_variable=merged.get("group_variable"),
        time_variable=merged.get("time_variable"),
        split_strategy=SplitStrategy(
            value.split["strategy"]
            if value.split
            else merged.get(
                "split_strategy", "kfold" if node_type == "cross_validation" else "train_test"
            )
        ),
        test_size=merged.get("test_size", 0.2),
        val_size=merged.get("val_size", 0.15),
        n_splits=merged.get("n_splits", 5),
        random_seed=merged.get("seed", 42),
        model_ids=(model,),
        feature_selection_k=value.selection_k,
        model_params=merged.get("model_params", {}),
        **preprocessing,
    )
    if (
        config.split_strategy == SplitStrategy.TRAIN_VAL_TEST
        and config.test_size + config.val_size >= 1
    ):
        raise ValueError("WORKFLOW_SPLIT_SIZE_INVALID")
    if node_type == "cross_validation" and config.split_strategy in {
        SplitStrategy.TRAIN_TEST,
        SplitStrategy.TRAIN_VAL_TEST,
    }:
        raise ValueError("WORKFLOW_CV_REQUIRES_CROSS_VALIDATION_SPLIT")
    validate_config_resources(config)
    return config


_T = TypeVar("_T")


class WorkflowTask(Protocol):
    async def check(self) -> None: ...

    async def wait(self, awaitable: Awaitable[_T]) -> _T: ...


class WorkflowRuntime:
    def __init__(
        self, db: AsyncSession, workflow: Workflow, run: WorkflowRun, user: User, task: MLTaskScope
    ):
        self.db, self.workflow, self.run, self.user = db, workflow, run, user
        self.task: WorkflowTask = task
        self._service_task = task
        self.sources: dict[str, dict] = {}

    def node_transaction(self):
        return self.db.begin_nested()

    async def partition(self, dataset: Dataset, config: MLConfig) -> dict:
        return await ml_service.partition_dataset(dataset, config, task=self._service_task)

    async def source(self, data: dict) -> FrameValue:
        from app.services.workflow_service import check_dataset_access

        dataset_id = (
            uuid.UUID(data["dataset_id"]) if data.get("dataset_id") else self.workflow.dataset_id
        )
        if dataset_id is None:
            raise ValueError("WORKFLOW_DATASET_REQUIRED")
        await self.task.wait(
            check_dataset_access(self.db, self.workflow.project_id, dataset_id, self.user)
        )
        dataset = (
            await self.task.wait(
                self.db.scalars(
                    select(Dataset).options(noload("*")).where(Dataset.id == dataset_id)
                )
            )
        ).one()
        if dataset.upload_status != UploadStatus.READY or (dataset.row_count or 0) > MAX_ROWS:
            raise ValueError("WORKFLOW_INPUT_LIMIT_OR_NOT_READY")
        lineage: list[dict] = []
        ancestors: list[dict] = []
        if dataset.object_key.startswith("workflow-derived-data/"):
            prefix = f"workflow-derived-data/{self.workflow.project_id}/{dataset.id}/"
            if not dataset.object_key.startswith(prefix) or not dataset.object_key.endswith(
                "/input.parquet"
            ):
                raise ValueError("WORKFLOW_DATA_VERSION_UNVERIFIABLE")
            metadata_sha = dataset.object_key[len(prefix) :].split("/")[0]
            if len(metadata_sha) != 64 or any(
                char not in "0123456789abcdef" for char in metadata_sha
            ):
                raise ValueError("WORKFLOW_DATA_VERSION_UNVERIFIABLE")
            metadata_key = f"{prefix}{metadata_sha}/provenance.json"
            metadata = json.loads(
                await self.task.wait(
                    verified_output(metadata_key, None, metadata_sha, limit=16 * 1024 * 1024)
                )
            )
            if (
                metadata.get("version") != 1
                or metadata.get("dataset_id") != str(dataset.id)
                or metadata.get("dataset_sha256") != dataset.sha256
                or not isinstance(metadata.get("lineage"), list)
            ):
                raise ValueError("WORKFLOW_DATA_VERSION_UNVERIFIABLE")
            ancestors = metadata.get("ancestors", [])
            if (
                not isinstance(ancestors, list)
                or len(ancestors) > 100
                or len(metadata["lineage"]) > 100
            ):
                raise ValueError("WORKFLOW_DATA_VERSION_LIMIT")
            for ancestor in ancestors:
                if not isinstance(ancestor, dict) or not isinstance(
                    ancestor.get("dataset_sha256"), str
                ):
                    raise ValueError("WORKFLOW_DATA_VERSION_UNVERIFIABLE")
                await self.task.wait(
                    check_dataset_access(
                        self.db,
                        self.workflow.project_id,
                        uuid.UUID(ancestor["dataset_id"]),
                        self.user,
                    )
                )
                self.sources[ancestor["dataset_id"]] = ancestor
            for step in metadata["lineage"]:
                if not isinstance(step, dict) or not isinstance(step.get("parameters"), dict):
                    raise ValueError("WORKFLOW_DATA_VERSION_UNVERIFIABLE")
                lineage.append(
                    {
                        "node_type": step["node_type"],
                        "parameters": normalize_node_data(step["node_type"], step["parameters"]),
                    }
                )
        self.sources[str(dataset.id)] = {
            "dataset_id": str(dataset.id),
            "dataset_sha256": dataset.sha256,
            "file_format": dataset.file_format,
            "lineage": lineage,
        }
        ref = {
            "dataset_id": str(dataset.id),
            "dataset_sha256": dataset.sha256,
            "file_format": dataset.file_format,
        }
        return FrameValue(dataset, lineage=lineage, source_refs=[*ancestors, ref])

    async def frame(self, value: FrameValue) -> pl.DataFrame:
        if value.frame is None:
            value.frame = await ml_service.bounded_workflow_frame(
                value.dataset, task=self._service_task
            )
        if value.frame.height > MAX_ROWS or value.frame.estimated_size() > 128 * 1024 * 1024:
            raise ValueError("WORKFLOW_FRAME_LIMIT")
        return value.frame

    async def materialize(self, value: FrameValue) -> Dataset:
        if not value.dirty:
            return value.dataset
        frame = await self.frame(value)
        fd, name = tempfile.mkstemp(suffix=".parquet", prefix="sf_wf_derived_")
        os.close(fd)
        Path(name).unlink()
        try:
            with BoundedWriter(Path(name), MAX_FILE_BYTES) as sink:
                frame.write_parquet(cast(IO[bytes], sink))
            digest = hashlib.sha256(Path(name).read_bytes()).hexdigest()
            # Every transformation yields its own immutable dataset version.
            # Never annotate or overwrite a user's existing same-byte upload.
            validated = validate_file(name, "parquet", preview_rows=0)
            dataset_id = uuid.uuid4()
            filename = f"workflow-{self.run.id}.parquet"
            metadata = {
                "version": 1,
                "dataset_id": str(dataset_id),
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
            metadata_bytes = json_bytes(metadata)
            metadata_sha = hashlib.sha256(metadata_bytes).hexdigest()
            prefix = f"workflow-derived-data/{self.workflow.project_id}/{dataset_id}/{metadata_sha}"
            key, metadata_key = prefix + "/input.parquet", prefix + "/provenance.json"
            dataset = Dataset(
                id=dataset_id,
                project_id=self.workflow.project_id,
                original_filename=filename,
                safe_filename=filename,
                file_format="parquet",
                mime_type="application/vnd.apache.parquet",
                file_size=Path(name).stat().st_size,
                sha256=digest,
                object_key=key,
                bucket=settings.minio_bucket,
                upload_status=UploadStatus.READY,
                name=filename,
                file_path=key,
                file_type="parquet",
                content_hash=digest,
                row_count=validated.row_count,
                column_count=validated.column_count,
                schema_json=validated.schema_json(),
                preview_json=validated.preview_json(),
                profile_status="profiled",
            )
            self.pending_output(key)
            self.pending_output(metadata_key)
            self.db.add(dataset)
            await self.task.wait(self.db.flush())
            await self.upload(metadata_key, metadata_bytes, "application/json")
            await self.upload(key, Path(name).read_bytes(), dataset.mime_type)
            self.sources[str(dataset.id)] = {
                "dataset_id": str(dataset.id),
                "dataset_sha256": dataset.sha256,
                "file_format": dataset.file_format,
                "parent_dataset_id": str(value.dataset.id),
                "lineage": value.lineage,
            }
            value.source_refs = [
                *value.source_refs,
                {
                    "dataset_id": str(dataset.id),
                    "dataset_sha256": dataset.sha256,
                    "file_format": dataset.file_format,
                },
            ]
            value.dataset, value.dirty = dataset, False
            return dataset
        finally:
            Path(name).unlink(missing_ok=True)

    def provenance(self, nid: str, data: dict, source: dict | None = None) -> dict:
        return {
            "contract_version": 2,
            "workflow_id": str(self.workflow.id),
            "workflow_version": self.workflow.version,
            "workflow_run_id": str(self.run.id),
            "node_id": nid,
            "parameters": data,
            "sources": list(self.sources.values()),
            "source": source or {},
            "software": {"workflow_engine": "2.0.0"},
        }

    def pending_output(self, key: str) -> None:
        from app.workflow import output_recovery

        delete = output_recovery.delete_registered if output_recovery.enabled() else delete_output
        pending_object(self.db, key, delete)

    async def upload(self, key: str, content: bytes, media_type: str) -> None:
        await self.task.check()
        if settings.storage_backend == "local":
            from app.workflow import output_recovery

            if output_recovery.enabled():
                output_recovery.write_local_output(
                    key,
                    content,
                    project_id=self.workflow.project_id,
                    workflow_id=self.workflow.id,
                    run_id=self.run.id,
                )
                await self.task.check()
                return
            # Bounded local copy has no abandoned thread that could resurrect a
            # compensated file after cancellation. No await crosses this write.
            path = storage_service.get_local_object_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with BoundedWriter(path, 128 * 1024 * 1024) as sink:
                sink.write(content)
            await self.task.check()
        else:
            await self.task.wait(
                ml_storage.upload_artifact(key, content, self._service_task, media_type=media_type)
            )

    async def save_file(self, nid: str, file: RenderedArtifact, provenance: dict) -> dict:
        from app.services.workflow_service import check_dataset_access

        await self.task.wait(
            check_dataset_access(self.db, self.workflow.project_id, self.run.dataset_id, self.user)
        )
        artifact_id = uuid.uuid4().hex
        prefix = f"workflow-artifacts/{self.workflow.project_id}/{self.run.id}/{artifact_id}"
        key = f"{prefix}/{file.filename}"
        self.pending_output(key)
        await self.upload(key, file.content, file.media_type)
        return {
            "id": artifact_id,
            "object_key": key,
            "filename": file.filename,
            "media_type": file.media_type,
            "bytes": len(file.content),
            "sha256": hashlib.sha256(file.content).hexdigest(),
            "includes_raw_rows": file.includes_raw_rows,
            "created_at": datetime.now(UTC).isoformat(),
            "provenance": provenance,
            "download_url": f"/api/v1/workflow/runs/{self.run.id}/artifacts/{artifact_id}",
        }

    async def fit_model(self, nid: str, node_type: str, dataset: Dataset, config: MLConfig) -> dict:
        from app.workflow import output_recovery

        await ml_service.preflight_experiment(dataset, config, task=self._service_task)
        experiment = await self.task.wait(
            ml_service.create_experiment(
                self.db, dataset, self.user, config, f"Workflow {self.run.id}: {nid}"
            )
        )
        owner_kwargs = {}
        if output_recovery.enabled():
            owner_kwargs["workflow_output_owner"] = output_recovery.ModelOutputOwner(
                self.workflow.project_id, self.workflow.id, self.run.id, dataset.id, dataset.sha256
            )
        run = await ml_service.run_experiment(
            self.db, experiment, self.user, task=self._service_task, **owner_kwargs
        )
        if run.status != MLRunStatus.COMPLETED or not run.artifact_key:
            raise ValueError(run.error_code or "WORKFLOW_ML_NOT_COMPLETED")
        split = json.loads(run.split_json or "{}")
        source = {
            "dataset_id": str(dataset.id),
            "dataset_sha256": dataset.sha256,
            "file_format": dataset.file_format,
            "ml_run_id": str(run.id),
            "ml_experiment_id": str(experiment.id),
            "workflow_id": str(self.workflow.id),
            "workflow_run_id": str(self.run.id),
            "workflow_version": self.workflow.version,
            "node_id": nid,
            "software_versions": json.loads(run.software_versions_json or "{}"),
            "node_type": node_type,
            "artifact": json.loads(run.artifact_meta_json or "{}"),
            "model_download_url": f"/api/v1/ml/runs/{run.id}/artifacts",
        }
        result = {
            "status": "completed",
            "task_type": config.task_type.value,
            "models": json.loads(run.metrics_json or "[]"),
            "split": split,
            "comparison": json.loads(run.comparison_json or "{}"),
            "leakage_checks": json.loads(run.leakage_checks_json or "[]"),
            "software_versions": json.loads(run.software_versions_json or "{}"),
            "input_config": config.to_dict(),
            "source": source,
        }
        return result

    async def node(
        self, nid: str, node_type: str, raw: dict, inputs: list[Any], legacy: bool
    ) -> tuple[Any, dict | None, str]:
        frame_values = [
            replace(
                value,
                lineage=list(value.lineage),
                preprocessing=dict(value.preprocessing),
                source_refs=list(value.source_refs),
            )
            for value in inputs
            if isinstance(value, FrameValue)
        ]
        results = [value for value in inputs if isinstance(value, ResultValue)]
        artifacts = [value for value in inputs if isinstance(value, ArtifactValue)]
        if len(frame_values) > 1:
            raise ValueError("WORKFLOW_AMBIGUOUS_FRAME_INPUT")
        value = frame_values[0] if frame_values else None
        data = normalize_node_data(node_type, raw, legacy=legacy)
        if node_type == "dataset":
            output = await self.source(data)
            return output, self.sources[str(output.dataset.id)], "Source version recorded"
        if node_type in {
            "column_select",
            "filter",
            "sample",
            "missing_value",
            "type_conversion",
            "duplicate_handling",
            "outlier_strategy",
        }:
            if value is None or value.split or value.preprocessing or value.selection_k:
                raise ValueError("WORKFLOW_CLEAN_BEFORE_ML_PLAN")
            frame = await self.frame(value)
            data = normalize_node_data(
                node_type, raw, legacy=legacy, known_columns=set(frame.columns)
            )
            changed = await self.task.wait(
                anyio.to_thread.run_sync(_apply_cleaning, node_type, frame, data)
            )
            output = replace(
                value,
                frame=changed,
                lineage=[*value.lineage, {"node_type": node_type, "parameters": data}],
                dirty=True,
            )
            return (
                output,
                {"rows": changed.height, "columns": changed.columns, "lineage": output.lineage},
                "Derived data; source retained",
            )
        if node_type in {"split", "preprocessing", "feature_selection"}:
            if value is None:
                raise ValueError("WORKFLOW_FRAME_REQUIRED")
            if node_type == "split":
                if value.split:
                    raise ValueError("WORKFLOW_DUPLICATE_SPLIT")
                dataset = await self.materialize(value)
                config = MLConfig(
                    task_type=MLTaskType(data["task_type"]),
                    target=data["target"],
                    split_strategy=SplitStrategy(data["strategy"]),
                    test_size=data["test_size"],
                    val_size=data["val_size"],
                    n_splits=data["n_splits"],
                    random_seed=data["seed"],
                    group_variable=data.get("group_variable"),
                    time_variable=data.get("time_variable"),
                )
                partition = await self.partition(dataset, config)
                file = RenderedArtifact(
                    "partitions.json", "application/json", json_bytes(partition)
                )
                ref = await self.save_file(nid, file, self.provenance(nid, data))
                output = replace(value, split=data, partition=partition)
                return (
                    output,
                    {"partition": partition["protocol"]["split"], "artifacts": [ref]},
                    "Actual partition membership saved",
                )
            if node_type == "preprocessing":
                output = replace(value, preprocessing={**value.preprocessing, **data})
            else:
                output = replace(value, selection_k=data["k"])
            return (
                output,
                {"configuration": data, "fit_scope": "training_fold_only"},
                "Training configuration recorded; learned inside model folds",
            )
        if node_type in {"model", "cross_validation"}:
            if value is None:
                raise ValueError("WORKFLOW_FRAME_REQUIRED")
            if any(
                step["node_type"] == "outlier_strategy"
                or (
                    step["node_type"] == "missing_value"
                    and step["parameters"].get("strategy") == "mean"
                )
                for step in value.lineage
            ):
                raise ValueError("WORKFLOW_GLOBAL_LEARNING_LEAKAGE_USE_PREPROCESSING")
            dataset = await self.materialize(value)
            config = resolve_ml_config(value, node_type, raw, data)
            result = await self.fit_model(nid, node_type, dataset, config)
            if (
                value.partition
                and value.partition["protocol"]["split"]
                != result["split"]["evaluation_protocol"]["split"]
            ):
                raise ValueError("WORKFLOW_PARTITION_MISMATCH")
            return (
                (ModelValue(result) if node_type == "model" else ResultValue(result)),
                result,
                "Bounded ML result and fitted model verified",
            )
        if node_type in STAT_NODE_METHODS or node_type == "regression":
            if value is None or value.split or value.preprocessing or value.selection_k:
                raise ValueError("WORKFLOW_STATISTICS_NEEDS_DATA_NOT_ML_PLAN")
            dataset = await self.materialize(value)
            frame = await self.frame(value)
            data = normalize_node_data(
                node_type, raw, legacy=legacy, known_columns=set(frame.columns)
            )
            result = await self.task.wait(
                anyio.to_thread.run_sync(_run_stat_node, node_type, frame, data)
            )
            if result.get("status") != "completed":
                raise ValueError(result.get("error_code") or "WORKFLOW_DOMAIN_NOT_COMPLETED")
            result["source"] = {
                "dataset_id": str(dataset.id),
                "dataset_sha256": dataset.sha256,
                "file_format": dataset.file_format,
                "node_type": node_type,
                "node_parameters": data,
                "workflow_id": str(self.workflow.id),
                "workflow_run_id": str(self.run.id),
                "workflow_version": self.workflow.version,
                "node_id": nid,
                "software_versions": result.get("software_versions", {}),
            }
            return ResultValue(result), result, "Deterministic statistics completed"
        if node_type in {"evaluation", "interpretation"}:
            models = [item for item in results if isinstance(item, ModelValue)]
            if len(models) != 1:
                raise ValueError("WORKFLOW_MODEL_REQUIRED")
            model_result = models[0].result
            if value is not None:
                dataset = await self.materialize(value)
                if (
                    str(dataset.id) != model_result["source"]["dataset_id"]
                    or dataset.sha256 != model_result["source"]["dataset_sha256"]
                ):
                    raise ValueError("WORKFLOW_EVALUATION_INPUT_MISMATCH")
                if (
                    value.partition
                    and value.partition["protocol"]["split"]
                    != model_result["split"]["evaluation_protocol"]["split"]
                ):
                    raise ValueError("WORKFLOW_EVALUATION_PARTITION_MISMATCH")
            candidate = model_result["models"][0]
            if node_type == "evaluation":
                partition = data["partition"]
                metrics = candidate.get(
                    "validation_metrics" if partition == "validation" else "test_metrics"
                )
                if not metrics:
                    raise ValueError("WORKFLOW_EVALUATION_PARTITION_UNAVAILABLE")
                result = {
                    **model_result,
                    "evaluation": {
                        "partition": partition,
                        "metrics": metrics,
                        "evaluation_protocol_id": candidate["evaluation_protocol_id"],
                    },
                }
            else:
                interpretation = candidate.get("interpretation")
                if not interpretation or not any(
                    interpretation.get(key)
                    for key in ("features", "permutation", "original_features")
                ):
                    raise ValueError("WORKFLOW_INTERPRETATION_UNAVAILABLE")
                result = {**model_result, "interpretation": interpretation}
            return ResultValue(result), result, "Projection of actual saved model evaluation"
        if node_type in {"table", "chart", "report", "notebook", "export"}:
            if node_type != "export" and len(results) + len(frame_values) != 1:
                raise ValueError("WORKFLOW_OUTPUT_NEEDS_SINGLE_INPUT")
            output_result = aggregate_result(results[0].result) if results else None
            provenance = self.provenance(
                nid, data, output_result.get("source") if output_result else None
            )
            if node_type == "table":
                if value and (value.split or value.preprocessing or value.selection_k):
                    raise ValueError("WORKFLOW_TABLE_NEEDS_DATA_NOT_ML_PLAN")
                if value:
                    await self.materialize(value)
                    provenance = self.provenance(nid, data)
                if value is None and output_result is None:
                    raise ValueError("WORKFLOW_RESULT_REQUIRED")
                table_input = await self.frame(value) if value else output_result
                assert table_input is not None
                file = render_table(table_input, data)
            elif node_type == "export":
                file = render_export(
                    [file for item in artifacts for file in item.files], data, provenance
                )
            elif output_result is None:
                raise ValueError("WORKFLOW_RESULT_REQUIRED")
            elif node_type == "chart":
                file = render_chart(output_result, data, provenance)
            elif node_type == "report":
                file = render_report(output_result, data, provenance)
            else:
                file = render_notebook(output_result, data, provenance)
            ref = await self.save_file(nid, file, provenance)
            return (
                ArtifactValue([file], [ref]),
                {"artifacts": [ref]},
                f"Saved {file.filename} ({len(file.content)} bytes)",
            )
        raise ValueError("WORKFLOW_CAPABILITY_UNAVAILABLE")


async def execute_domain_graph(graph: dict, runtime: WorkflowRuntime) -> dict:
    validation = validate_graph(graph)
    if not validation["valid"]:
        return {
            "status": "failed",
            "issues": validation["issues"],
            "node_results": {},
            "order": validation["topo_order"],
        }
    nodes = {node["id"]: node for node in graph["nodes"]}
    values: dict[str, Any] = {}
    results: dict[str, dict[str, Any]] = {}
    started = time.monotonic()
    for nid in validation["topo_order"]:
        await runtime.task.check()
        node, node_started = nodes[nid], time.monotonic()
        predecessors = [edge["source"] for edge in graph["edges"] if edge["target"] == nid]
        if any(results[source]["status"] != "completed" for source in predecessors):
            results[nid] = {
                "status": "skipped",
                "node_type": node["type"],
                "summary": "Upstream did not complete",
                "reason_code": "WORKFLOW_UPSTREAM_INCOMPLETE",
                "seconds": 0,
            }
            continue
        sources_before = dict(runtime.sources)
        try:
            async with runtime.node_transaction():
                value, output, summary = await runtime.node(
                    nid,
                    node["type"],
                    node.get("data", {}),
                    [values[source] for source in predecessors],
                    graph.get("schema_version", 1) == 1,
                )
            values[nid] = value
            spec = get_spec(node["type"])
            results[nid] = {
                "status": "completed",
                "node_type": node["type"],
                "output_kind": spec.outputs[0] if spec else None,
                "summary": summary,
                "seconds": round(time.monotonic() - node_started, 3),
                "output": output,
            }
        except Exception as exc:
            runtime.sources = sources_before
            if isinstance(exc, (DatasetAccessDeniedError, StatForgeError)) and getattr(
                exc, "status_code", 422
            ) in {403, 404}:
                raise
            if getattr(exc, "code", getattr(exc, "error_code", None)) in {
                "ML_TIMEOUT",
                "ML_CANCELLED",
                "ML_BUSY",
            }:
                raise
            results[nid] = {
                "status": "failed",
                "node_type": node["type"],
                "summary": "",
                "error_code": node_error_code(exc),
                "error": str(getattr(exc, "error_code", None) or str(exc) or type(exc).__name__)[
                    :1000
                ],
                "seconds": round(time.monotonic() - node_started, 3),
            }
    return {
        "status": "completed"
        if all(result["status"] == "completed" for result in results.values())
        else "failed",
        "node_results": results,
        "order": validation["topo_order"],
        "total_seconds": round(time.monotonic() - started, 3),
        "sources": list(runtime.sources.values()),
    }
