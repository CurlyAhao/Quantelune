"""Persist already computed fixed-domain values in the caller's short transaction."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import noload

from app.config import settings
from app.models.dataset import Dataset, UploadStatus
from app.models.ml import MLExperiment, MLRun, MLRunStatus
from app.models.quality import QualityRun, QualityRunStatus
from app.models.statistics import StatisticalRun
from app.models.workflow import WorkflowRun, WorkflowRunStatus
from app.research.contracts import canonical_bytes
from app.services import research_service as ledger


def encoded(value):
    return canonical_bytes(value).decode()


async def _absent(db, model, identity):
    if await db.scalar(select(model.id).where(model.id == identity)) is not None:
        raise ledger.error("PUBLICATION_RECORD_COLLISION")


async def _ml(
    db,
    owner,
    project,
    dataset,
    config,
    result,
    identity,
    experiment_id,
    artifact_key,
    artifact_meta,
    when,
    *,
    reuse=False,
):
    from app.services.ml_service import config_from_dict

    actual = config_from_dict(config)
    experiment = await db.scalar(
        select(MLExperiment).options(noload("*")).where(MLExperiment.id == experiment_id)
    )
    if experiment is not None and not reuse:
        raise ledger.error("PUBLICATION_RECORD_COLLISION")
    if experiment is None:
        experiment = MLExperiment(
            id=experiment_id,
            project_id=project,
            dataset_id=dataset,
            name=f"Research {identity}",
            task_type=actual.task_type.value,
            target_variable=actual.target,
            feature_variables_json=encoded(list(actual.features)),
            categorical_variables_json=encoded(list(actual.categorical_features)),
            config_json=encoded(config),
            dataset_sha256=result["_dataset_sha256"],
            created_by=owner.email,
        )
        db.add(experiment)
        await db.flush()
    elif (
        experiment.project_id != project
        or experiment.dataset_id != dataset
        or experiment.dataset_sha256 != result["_dataset_sha256"]
    ):
        raise ledger.error("BINDING_CHANGED")
    await _absent(db, MLRun, identity)
    run = MLRun(
        id=identity,
        experiment_id=experiment_id,
        status=MLRunStatus.COMPLETED if result["status"] == "completed" else MLRunStatus.FAILED,
        random_seed=actual.random_seed,
        validation_strategy=actual.split_strategy.value,
        split_json=encoded(result.get("split", {})),
        metrics_json=encoded(result.get("models", [])),
        comparison_json=encoded(result.get("comparison", {})),
        leakage_checks_json=encoded(result.get("leakage_checks", [])),
        n_observations=result.get("n_observations"),
        software_versions_json=encoded(result.get("software_versions", {})),
        artifact_key=artifact_key,
        artifact_meta_json=encoded(artifact_meta) if artifact_meta else None,
        started_at=when,
        completed_at=when,
        error_code=result.get("error_code"),
        error_message=result.get("error_message"),
    )
    db.add(run)
    return run


async def persist(db, document, response, plan, owner):
    """No numerical work or storage I/O; caller already validated/copied bytes."""
    kind, result, config = document["domain_kind"], response["result"], document["configuration"]
    identity = uuid.UUID(document["domain_id"])
    project = uuid.UUID(document["source"]["project_id"])
    dataset_id = uuid.UUID(document["source"]["dataset_id"])
    sha = document["source"]["sha256"]
    when = datetime.fromisoformat(plan["recorded_at"])
    dataset = await db.scalar(
        select(Dataset).options(noload("*")).where(Dataset.id == dataset_id),
        execution_options={"populate_existing": True},
    )
    if dataset is None or dataset.project_id != project or dataset.sha256 != sha:
        raise ledger.error("BINDING_CHANGED")
    if kind == "quality":
        from app.quality.registry import RULESET_VERSION
        from app.quality.rules import OHLCV_RULESET_VERSION
        from app.quality.thresholds import ThresholdConfig
        from app.services.quality_service import _ensure_profile, _persist_findings

        await _absent(db, QualityRun, identity)
        report = result["report"]
        profile = await _ensure_profile(db, dataset, result["profile"])
        counts = report["counts"]
        versions = [RULESET_VERSION] + ([OHLCV_RULESET_VERSION] if config["enable_ohlcv"] else [])
        run = QualityRun(
            id=identity,
            dataset_id=dataset_id,
            dataset_sha256=sha,
            profile_id=profile.id,
            ruleset_version="+".join(versions),
            threshold_config_json=encoded(
                ThresholdConfig().with_overrides(config["thresholds"]).to_dict()
            ),
            status=QualityRunStatus.COMPLETED_WITH_ERRORS
            if counts.get("error", 0) > 0
            else QualityRunStatus.COMPLETED,
            started_at=when,
            completed_at=when,
            created_by=owner.email,
            ohlcv_enabled=config["enable_ohlcv"],
            sampled=bool(report.get("sampled", False)),
            total_rules=report["total_rules"],
            passed_count=counts.get("passed", 0),
            warning_count=counts.get("warning", 0),
            failed_count=counts.get("failed", 0),
            error_count=counts.get("error", 0),
            skipped_count=counts.get("skipped", 0),
            overall_score=report["score"]["total_score"],
            score_json=encoded(report["score"]),
        )
        db.add(run)
        await db.flush()
        _persist_findings(db, run, report["findings"])
    elif kind == "statistics":
        from app.schemas.statistics import StatisticalRunRequest
        from app.services.statistics_service import (
            _STATUS_MAP,
            _config_from_request,
            _persist_result_and_findings,
        )
        from app.statistics.registry import METHODSET_VERSION

        await _absent(db, StatisticalRun, identity)
        request = StatisticalRunRequest.model_validate(config)
        run = StatisticalRun(
            id=identity,
            project_id=project,
            dataset_id=dataset_id,
            dataset_sha256=sha,
            method=request.method_id.value,
            methodset_version=METHODSET_VERSION,
            status=_STATUS_MAP[result["status"]],
            input_config_json=encoded(_config_from_request(request).to_dict()),
            missing_value_policy=request.missing_value_policy.value,
            sample_size=(result.get("sample") or {}).get("final_n"),
            started_at=when,
            completed_at=when,
            created_by=owner.email,
            error_code=result.get("error_code"),
            error_message=result.get("error_message"),
        )
        db.add(run)
        await db.flush()
        _persist_result_and_findings(db, run, result)
    elif kind == "ml":
        origin = document["configuration_source"]
        experiment = uuid.UUID(origin["id"]) if origin else uuid.uuid5(identity, "ml_experiment")
        key = (
            f"ml-artifacts/{project}/{experiment}/{identity}.joblib"
            if result["status"] == "completed"
            else None
        )
        await _ml(
            db,
            owner,
            project,
            dataset_id,
            config,
            {**result, "_dataset_sha256": sha},
            identity,
            experiment,
            key,
            response.get("artifact_meta"),
            when,
            reuse=origin is not None,
        )
    elif kind == "workflow":
        for record in result["dataset_records"]:
            own_id = uuid.UUID(record["id"])
            await _absent(db, Dataset, own_id)
            filename = f"workflow-{identity}.parquet"
            db.add(
                Dataset(
                    id=own_id,
                    project_id=project,
                    original_filename=filename,
                    safe_filename=filename,
                    file_format="parquet",
                    mime_type="application/vnd.apache.parquet",
                    file_size=record["bytes"],
                    sha256=record["sha256"],
                    object_key=record["object_key"],
                    bucket=settings.minio_bucket,
                    upload_status=UploadStatus.READY,
                    name=filename,
                    file_path=record["object_key"],
                    file_type="parquet",
                    content_hash=record["sha256"],
                    row_count=record["row_count"],
                    column_count=record["column_count"],
                    schema_json=record["schema_json"],
                    preview_json=record["preview_json"],
                    profile_status="profiled",
                )
            )
        await db.flush()
        for record in result["model_records"]:
            await _ml(
                db,
                owner,
                project,
                uuid.UUID(record["dataset_id"]),
                record["config"],
                {**record["result"], "_dataset_sha256": record["dataset_sha256"]},
                uuid.UUID(record["id"]),
                uuid.UUID(record["experiment_id"]),
                record["artifact_key"],
                record["artifact_meta"],
                when,
            )
        context = document["configuration"]
        await _absent(db, WorkflowRun, identity)
        # Private filenames are publication implementation data, never public
        # snapshot fields or semantic scientific result identifiers.
        node_results = {
            nid: {
                **node,
                "output": {
                    **node["output"],
                    "artifacts": [
                        {key: value for key, value in reference.items() if key != "staged_name"}
                        for reference in node["output"]["artifacts"]
                    ],
                },
            }
            if isinstance(node.get("output"), dict) and "artifacts" in node["output"]
            else node
            for nid, node in result["node_results"].items()
        }
        db.add(
            WorkflowRun(
                id=identity,
                workflow_id=uuid.UUID(context["workflow_id"]),
                dataset_id=dataset_id,
                status=WorkflowRunStatus.COMPLETED
                if result["status"] == "completed"
                else WorkflowRunStatus.FAILED,
                graph_snapshot_json=encoded(
                    {
                        **context["graph"],
                        "_provenance": {
                            "workflow_version": context["workflow_version"],
                            "workflow_engine": "2.0.0",
                            "sources": result["sources"],
                        },
                    }
                ),
                node_results_json=encoded(node_results),
                order_json=encoded(result["order"]),
                total_seconds=result["total_seconds"],
                started_at=when,
                completed_at=when,
                created_by=owner.email,
                error_message="One or more Workflow nodes failed"
                if result["status"] != "completed"
                else None,
            )
        )
    else:
        raise ledger.error("EXECUTION_UNSUPPORTED")
    await db.flush()
