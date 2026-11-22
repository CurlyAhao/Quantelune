"""ML experiment orchestration: create experiments, run them, persist artifacts.

Heavy work runs in a bounded OS worker. The fitted pipelines are serialized with
joblib and stored in object storage; the API only ever *serves* these artifacts
(presigned download) and never deserializes externally-supplied model files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import anyio
import polars as pl
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import DatasetNotFoundError, StatForgeError
from app.ml.config import MLConfig
from app.ml.preflight import (
    TrustedTargetStats,
    preflight_config_columns,
)
from app.ml.resources import (
    MAX_ROWS,
    MLResourceError,
    WorkerLimits,
    validate_config_resources,
)
from app.ml.types import MLModelId, MLTaskType, SplitStrategy
from app.ml.worker_control import MLTaskScope, run_ml_worker
from app.models.dataset import Dataset
from app.models.ml import MLExperiment, MLRun, MLRunStatus
from app.models.user import User
from app.services import audit_service, dataset_service, ml_storage, storage_service
from app.services.profiling_service import load_dataframe

logger = logging.getLogger("statforge.ml")

if TYPE_CHECKING:
    from app.workflow.output_recovery import ModelOutputOwner


def config_from_dict(data: dict[str, Any]) -> MLConfig:
    """Rebuild an MLConfig from its serialized dict (enums coerced back)."""
    return MLConfig(
        task_type=MLTaskType(data["task_type"]),
        target=data["target"],
        features=tuple(data.get("features", [])),
        categorical_features=tuple(data.get("categorical_features", [])),
        group_variable=data.get("group_variable"),
        time_variable=data.get("time_variable"),
        split_strategy=SplitStrategy(data.get("split_strategy", "stratified_kfold")),
        test_size=float(data.get("test_size", 0.2)),
        val_size=float(data.get("val_size", 0.15)),
        n_splits=int(data.get("n_splits", 5)),
        random_seed=int(data.get("random_seed", 42)),
        model_ids=tuple(MLModelId(m) for m in data.get("model_ids", [])),
        standardize=bool(data.get("standardize", True)),
        impute_numeric=data.get("impute_numeric", "median"),
        impute_categorical=data.get("impute_categorical", "most_frequent"),
        handle_outliers=bool(data.get("handle_outliers", False)),
        feature_selection_k=data.get("feature_selection_k"),
        model_params=data.get("model_params", {}),
    )


def _load_dataset_frame_blocking(
    object_key: str,
    file_format: str,
    suffix: str,
    sheet_name: str | None = None,
    csv_encoding: str | None = None,
) -> pl.DataFrame:
    """Download dataset to a private temp file and load the full frame."""
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sf_ml_pf_")
    os.close(fd)
    try:
        storage_service.download_to_path(object_key, tmp_path)
        return load_dataframe(tmp_path, file_format, sheet_name, csv_encoding)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def load_dataset_frame(dataset: Dataset) -> pl.DataFrame:
    """Load the full training dataframe for preflight/stats (no sampling)."""
    suffix = f".{dataset.file_format}" if dataset.file_format else ".tmp"
    return await anyio.to_thread.run_sync(
        _load_dataset_frame_blocking,
        dataset.object_key,
        dataset.file_format,
        suffix,
        dataset.sheet_name,
        dataset.csv_encoding,
    )


async def preflight_experiment(
    dataset: Dataset,
    config: MLConfig,
    column_names: set[str] | None = None,
    *,
    wall_seconds: float = 300,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    task: MLTaskScope | None = None,
) -> TrustedTargetStats:
    """Run dataset-aware preflight; raise StatForgeError 422 when infeasible.

    Must be called before persisting any experiment or run for the request.
    """
    if column_names is not None:
        preflight_config_columns(config, column_names)
    output = await _bounded_dataset_operation(
        dataset,
        config,
        "preflight",
        wall_seconds=wall_seconds,
        cancel_check=cancel_check,
        task=task,
    )
    return TrustedTargetStats(**output["stats"])


def _pending_artifact(db: AsyncSession, key: str) -> None:
    """Compensate only this session's newly staged ML object on rollback."""
    session = db.sync_session
    namespace = "_statforge_ml_artifacts"
    if namespace not in session.info:
        session.info[namespace] = []

        def current(s):
            return s.get_nested_transaction() or s.get_transaction()

        def committed(s):
            transaction = current(s)
            pending = s.info.get(namespace, [])
            if transaction is not None and transaction.parent is not None:
                s.info[namespace] = [
                    (transaction.parent if owner is transaction else owner, object_key)
                    for owner, object_key in pending
                ]
            else:
                uncertain = set(s.info.get(namespace + "_uncertain", []))
                uncertain.difference_update(object_key for _, object_key in pending)
                s.info[namespace + "_uncertain"] = sorted(uncertain)
                s.info[namespace] = []

        def ended(s, previous):
            # Flush uses internal subtransactions. Only a real savepoint/root
            # ending can revoke its staged files. Commit already promoted or
            # cleared ownership before this public event fires.
            if previous.parent is not None and not previous.nested:
                return
            keep = []
            cleanup = set(s.info.pop(namespace + "_failed", []))
            uncertain = set(s.info.get(namespace + "_uncertain", []))
            for owner, object_key in s.info.get(namespace, []):
                cursor = owner
                belongs = False
                while cursor is not None:
                    if cursor is previous:
                        belongs = True
                        break
                    cursor = cursor.parent
                if belongs:
                    if object_key in uncertain:
                        logger.error("ML_PUBLICATION_UNKNOWN object_key=%s", object_key)
                    else:
                        cleanup.add(object_key)
                else:
                    keep.append((owner, object_key))
            s.info[namespace] = keep
            failed = []
            for object_key in cleanup:
                try:
                    ml_storage.delete_staged_object(object_key)
                except Exception:
                    # A failed delete must not interrupt rollback or erase the
                    # other compensations. Retain the key for the next end and
                    # emit a stable repair diagnostic, including on close.
                    failed.append(object_key)
                    logger.error("ML_ARTIFACT_CLEANUP_FAILED object_key=%s", object_key)
            s.info[namespace + "_failed"] = failed

        event.listen(session, "after_commit", committed)
        event.listen(session, "after_transaction_end", ended)
    transaction = session.get_nested_transaction() or session.get_transaction()
    session.info[namespace].append((transaction, key))


async def publish_transaction(
    db: AsyncSession, task: MLTaskScope, *, additional_owned_namespaces: tuple[str, ...] = ()
) -> None:
    """COMMIT success is final; cancellation cannot undo that publication."""
    await task.check()
    if db.in_nested_transaction():
        raise StatForgeError(
            422,
            "ML_PUBLICATION_SCOPE_INVALID",
            "Root publication cannot start inside an active savepoint.",
        )
    remaining = task.deadline - time.monotonic()
    if remaining <= 0:
        raise MLResourceError("ML_TIMEOUT", "ML publication did not start within its deadline.")
    confirmed = False

    def observe_commit(session):
        nonlocal confirmed
        transaction = session.get_transaction()
        if (
            transaction is not None
            and transaction.parent is None
            and session.get_nested_transaction() is None
        ):
            confirmed = True

    event.listen(db.sync_session, "after_commit", observe_commit)
    # Mark before the irreversible call: driver cancellation/connection cleanup
    # may end the transaction before control returns to this function.
    namespaces = ("_statforge_ml_artifacts", *additional_owned_namespaces)
    for namespace in namespaces:
        uncertain = set(db.sync_session.info.get(namespace + "_uncertain", []))
        uncertain.update(entry[1] for entry in db.sync_session.info.get(namespace, []))
        db.sync_session.info[namespace + "_uncertain"] = sorted(uncertain)
    try:
        # Do not poll request-disconnect or run the reversible-I/O post-check
        # during an atomic COMMIT. The database result, not a late request flag,
        # is authoritative. The remaining absolute time bound still applies.
        await asyncio.wait_for(db.commit(), timeout=remaining)
    except BaseException as exc:
        if confirmed:
            if isinstance(exc, (Exception, asyncio.CancelledError)):
                return
            raise
        # A failed/lost COMMIT acknowledgement is not proof of rollback. Keep
        # only these new keys for verification instead of deleting a potentially
        # committed run's artifact when the request session closes.
        for namespace in namespaces:
            uncertain = set(db.sync_session.info.get(namespace + "_uncertain", []))
            uncertain.update(entry[1] for entry in db.sync_session.info.get(namespace, []))
            db.sync_session.info[namespace + "_uncertain"] = sorted(uncertain)
        logger.error("ML_PUBLICATION_UNKNOWN; verify run history before retry")
        if isinstance(exc, (Exception, asyncio.CancelledError)):
            raise StatForgeError(
                503,
                "ML_PUBLICATION_UNKNOWN",
                "ML publication outcome is unknown; reopen run history before retrying.",
            ) from exc
        raise
    finally:
        event.remove(db.sync_session, "after_commit", observe_commit)


def _check_dataset_budget(dataset: Dataset) -> None:
    if (dataset.row_count or 0) > MAX_ROWS:
        raise StatForgeError(422, "ML_INPUT_LIMIT", "ML dataset exceeds the finite input budget.")


async def _bounded_dataset_operation(
    dataset: Dataset,
    config: MLConfig,
    operation: str,
    *,
    wall_seconds: float = 300,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    task: MLTaskScope | None = None,
    artifact_target=None,
) -> dict[str, Any]:
    if task is None:
        try:
            async with MLTaskScope(
                WorkerLimits(wall_seconds=wall_seconds), cancel_check
            ) as admitted:
                return await _bounded_dataset_operation(
                    dataset, config, operation, task=admitted, artifact_target=artifact_target
                )
        except MLResourceError as exc:
            raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc
    await task.check()
    _check_dataset_budget(dataset)
    try:
        validate_config_resources(config)
    except MLResourceError as exc:
        raise StatForgeError(422, exc.code, str(exc)) from exc
    path: str | None = None
    try:
        if settings.storage_backend == "local":
            source = str(storage_service.get_local_object_path(dataset.object_key))
        else:
            fd, path = tempfile.mkstemp(suffix="." + dataset.file_format, prefix="sf_ml_input_")
            os.close(fd)
            await task.wait(ml_storage.download_input(dataset.object_key, path, task))
            source = path
        return await run_ml_worker(
            source,
            dataset.file_format,
            config.to_dict(),
            dataset.sha256,
            operation=operation,
            task=task,
            artifact_target=artifact_target,
            sheet_name=dataset.sheet_name,
            csv_encoding=dataset.csv_encoding,
        )
    except MLResourceError as exc:
        raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc
    finally:
        if path is not None and os.path.exists(path):
            os.unlink(path)


async def partition_dataset(
    dataset: Dataset, config: MLConfig, *, task: MLTaskScope
) -> dict[str, Any]:
    """Actual immutable row allocation, generated inside the registered worker."""
    output = await _bounded_dataset_operation(dataset, config, "partition", task=task)
    return output["partition"]


async def bounded_workflow_frame(dataset: Dataset, *, task: MLTaskScope) -> pl.DataFrame:
    import io

    config = MLConfig(task_type=MLTaskType.REGRESSION, target="__workflow_frame__")
    output = await _bounded_dataset_operation(dataset, config, "frame", task=task)
    # This trusted worker channel is uncompressed Arrow, capped before write;
    # no original compressed file is parsed in the API process.
    frame = pl.read_ipc(io.BytesIO(output["artifact"]), memory_map=False)
    if (
        frame.height != output["frame_meta"]["rows"]
        or frame.columns != output["frame_meta"]["columns"]
    ):
        raise StatForgeError(422, "WORKFLOW_FRAME_INVALID", "Frame identity changed in transport.")
    return frame


async def target_stats(
    dataset: Dataset, config: MLConfig, *, task: MLTaskScope | None = None
) -> TrustedTargetStats:
    output = await _bounded_dataset_operation(dataset, config, "stats", task=task)
    return TrustedTargetStats(**output["stats"])


async def create_experiment(
    db: AsyncSession, dataset: Dataset, user: User, config: MLConfig, name: str
) -> MLExperiment:
    experiment = MLExperiment(
        project_id=dataset.project_id,
        dataset_id=dataset.id,
        name=name,
        task_type=config.task_type.value,
        target_variable=config.target,
        feature_variables_json=json.dumps(list(config.features), ensure_ascii=False),
        categorical_variables_json=json.dumps(
            list(config.categorical_features), ensure_ascii=False
        ),
        config_json=json.dumps(config.to_dict(), ensure_ascii=False),
        dataset_sha256=dataset.sha256,
        created_by=user.email,
    )
    db.add(experiment)
    await db.flush()
    await audit_service.record(
        db,
        event_type="ml_experiment.created",
        entity_type="ml_experiment",
        entity_id=str(experiment.id),
        actor=user,
        project_id=dataset.project_id,
        after={"task_type": config.task_type.value, "target": config.target},
    )
    return experiment


async def run_experiment(
    db: AsyncSession,
    experiment: MLExperiment,
    user: User,
    *,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    task: MLTaskScope | None = None,
    workflow_output_owner: ModelOutputOwner | None = None,
    on_started: Callable[[MLRun], Awaitable[None]] | None = None,
    on_computed: Callable[[MLRun], Awaitable[None]] | None = None,
) -> MLRun:
    """Execute and persist a run for an experiment."""
    if task is None:
        try:
            async with MLTaskScope(cancel_check=cancel_check) as admitted:
                return await run_experiment(
                    db,
                    experiment,
                    user,
                    task=admitted,
                    workflow_output_owner=workflow_output_owner,
                    on_started=on_started,
                    on_computed=on_computed,
                )
        except MLResourceError as exc:
            raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc
    await task.check()
    if experiment.config_json is None:
        raise StatForgeError(
            422,
            "ML_CONFIG_INVALID",
            "ML experiment configuration is missing.",
        )
    config = config_from_dict(json.loads(experiment.config_json))

    dataset = await task.wait(db.get(Dataset, experiment.dataset_id))
    if dataset is None:
        raise DatasetNotFoundError()
    if dataset.sha256 != experiment.dataset_sha256:
        raise StatForgeError(422, "ML_INPUT_CHANGED", "ML experiment input version has changed.")
    if workflow_output_owner is not None:
        from app.workflow import output_recovery

        if (
            not isinstance(workflow_output_owner, output_recovery.ModelOutputOwner)
            or not output_recovery.enabled()
            or workflow_output_owner.project_id != experiment.project_id
            or workflow_output_owner.dataset_id != dataset.id
            or workflow_output_owner.dataset_sha256 != dataset.sha256
        ):
            raise StatForgeError(422, "WORKFLOW_ML_OWNER_INVALID", "Workflow model owner differs.")
    # Dataset-aware preflight BEFORE persisting any run row. Infeasible configs
    # must 422 with zero run side effects and must not touch existing experiments.
    await preflight_experiment(dataset, config, task=task)

    run = MLRun(
        experiment_id=experiment.id,
        status=MLRunStatus.RUNNING,
        random_seed=config.random_seed,
        validation_strategy=config.split_strategy.value,
        started_at=datetime.now(UTC),
    )
    if on_started is not None:
        await on_started(run)
    else:
        db.add(run)
        await task.wait(db.flush())
    object_key = f"ml-artifacts/{experiment.project_id}/{experiment.id}/{run.id}.joblib"
    target = (
        storage_service.get_local_object_path(object_key)
        if settings.storage_backend == "local" and workflow_output_owner is None
        else None
    )
    if workflow_output_owner is not None:
        from app.workflow.publication import pending_object

        pending_object(db, object_key, output_recovery.delete_registered)
    else:
        _pending_artifact(db, object_key)
        if target:
            _pending_artifact(db, object_key + ".pending")
    try:
        output = await _bounded_dataset_operation(
            dataset,
            config,
            "execute",
            task=task,
            artifact_target=target,
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "error_code", getattr(exc, "code", "ML_RUN_FAILED"))
        if code in {"ML_TIMEOUT", "ML_CANCELLED"}:
            raise StatForgeError(422, code, "ML did not publish a completed result.") from exc
        if workflow_output_owner is None:
            ml_storage.delete_staged_object(object_key)
            if target:
                ml_storage.delete_staged_object(object_key + ".pending")
        if on_computed is not None:
            await on_computed(run)
        run.status = MLRunStatus.FAILED
        run.completed_at = datetime.now(UTC)
        run.error_code = getattr(exc, "error_code", getattr(exc, "code", "ML_RUN_FAILED"))
        run.error_message = str(getattr(exc, "detail", "ML worker did not complete."))[:2000]
        await db.flush()
        await audit_service.record(
            db,
            event_type="ml_run.failed",
            entity_type="ml_experiment",
            entity_id=str(experiment.id),
            actor=user,
            project_id=experiment.project_id,
            after={"error": run.error_message},
        )
        logger.warning("ML run failed for experiment %s: %s", experiment.id, run.error_code)
        return run
    if on_computed is not None:
        await on_computed(run)
    result = output["result"]
    artifact: bytes = output["artifact"]
    # Recheck access at the actual publication boundary after the awaited fit.
    await task.wait(dataset_service.get_dataset_with_access(db, dataset.id, user.id))
    await task.check()

    run.completed_at = datetime.now(UTC)
    run.split_json = json.dumps(result.get("split", {}), ensure_ascii=False)
    run.metrics_json = json.dumps(result.get("models", []), ensure_ascii=False)
    run.comparison_json = json.dumps(result.get("comparison", {}), ensure_ascii=False)
    run.leakage_checks_json = json.dumps(result.get("leakage_checks", []), ensure_ascii=False)
    run.n_observations = result.get("n_observations")
    run.software_versions_json = json.dumps(result.get("software_versions", {}), ensure_ascii=False)

    if result.get("status") != "completed":
        run.status = MLRunStatus.FAILED
        run.error_code = result.get("error_code", "ML_RUN_FAILED")
        run.error_message = result.get("error_message")
    else:
        # Store the serialized pipeline artifact.
        if (target and target.is_file() and output.get("artifact_meta")) or artifact:
            if workflow_output_owner is not None:
                meta = output.get("artifact_meta", {})
                if (
                    not isinstance(artifact, bytes)
                    or not artifact
                    or meta.get("bytes") != len(artifact)
                    or meta.get("sha256") != sha256(artifact).hexdigest()
                    or meta.get("evaluation_protocol_id")
                    != result["split"]["evaluation_protocol"]["protocol_id"]
                    or sorted(meta.get("models", []))
                    != sorted(m["model_id"] for m in result["models"] if m["status"] == "completed")
                ):
                    raise StatForgeError(
                        422, "ML_ARTIFACT_INVALID", "Workflow model byte channel differs."
                    )
                output_recovery.write_local_output(
                    object_key,
                    artifact,
                    project_id=workflow_output_owner.project_id,
                    workflow_id=workflow_output_owner.workflow_id,
                    run_id=workflow_output_owner.run_id,
                    model_source={
                        "dataset_id": str(dataset.id),
                        "dataset_sha256": dataset.sha256,
                        "experiment_id": str(experiment.id),
                        "ml_run_id": str(run.id),
                    },
                )
            elif target is None:
                await task.wait(ml_storage.upload_artifact(object_key, artifact, task))
            await task.check()
            run.artifact_key = object_key
            run.artifact_meta_json = json.dumps(
                output.get("artifact_meta")
                or {
                    "bytes": len(artifact),
                    "sha256": sha256(artifact).hexdigest(),
                    "evaluation_protocol_id": result["split"]["evaluation_protocol"]["protocol_id"],
                    "models": [
                        m["model_id"] for m in result["models"] if m["status"] == "completed"
                    ],
                },
                ensure_ascii=False,
            )
            run.status = MLRunStatus.COMPLETED
        else:
            run.status = MLRunStatus.FAILED
            run.error_code = "ML_ARTIFACT_MISSING"
            run.error_message = "ML execution produced no verified model artifact."

    await task.wait(
        audit_service.record(
            db,
            event_type=(
                "ml_run.completed" if run.status == MLRunStatus.COMPLETED else "ml_run.failed"
            ),
            entity_type="ml_experiment",
            entity_id=str(experiment.id),
            actor=user,
            project_id=experiment.project_id,
            after={"run_id": str(run.id), "status": run.status.value},
        )
    )
    await task.wait(db.flush())
    await task.wait(db.refresh(run))
    await task.check()
    logger.info("ML run complete: experiment=%s status=%s", experiment.id, run.status.value)
    return run


async def list_experiments(db: AsyncSession, dataset_id: Any) -> list[MLExperiment]:
    from sqlalchemy.orm import selectinload

    res = await db.execute(
        select(MLExperiment)
        .options(selectinload(MLExperiment.runs))
        .where(MLExperiment.dataset_id == dataset_id)
        .order_by(MLExperiment.created_at.desc())
    )
    return list(res.scalars().all())


async def latest_profile_id(db: AsyncSession, dataset: Dataset):
    from app.models.profiling import DatasetProfile, ProfileStatus

    res = await db.execute(
        select(DatasetProfile.id)
        .where(
            DatasetProfile.dataset_id == dataset.id,
            DatasetProfile.status == ProfileStatus.COMPLETED,
        )
        .order_by(DatasetProfile.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()
