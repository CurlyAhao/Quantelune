"""ML experiment routes: create/list experiments, run, retrieve runs/artifacts."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import JSON, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import DatasetNotReadyError, StatForgeError
from app.ml.config import MLConfig
from app.ml.preflight import preflight_config_columns
from app.ml.resources import MLResourceError
from app.ml.types import (
    CLASSIFICATION_MODELS,
    REGRESSION_MODELS,
    MLModelId,
    MLTaskType,
    SplitStrategy,
)
from app.ml.worker_control import MLTaskScope
from app.models.dataset import Dataset, UploadStatus
from app.models.ml import MLExperiment, MLRun
from app.models.user import User
from app.models.workspace import WorkspaceRecord
from app.schemas.ml import (
    MLExperimentCreateRequest,
    MLExperimentResponse,
    MLMetadataResponse,
    MLRunResponse,
    MLRunsResponse,
    MLRunSummary,
    build_metadata,
)
from app.services import dataset_service, ml_service, storage_service

logger = logging.getLogger("statforge.ml")
router = APIRouter(tags=["machine-learning"])


async def _rollback_publication_without_masking_outcome(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except BaseException as exc:
        logger.error("ML publication rollback cleanup failed (%s)", type(exc).__name__)


class MLExperimentNotFoundError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(404, "ML_EXPERIMENT_NOT_FOUND", "ML experiment not found.")


class MLRunNotFoundError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(404, "ML_RUN_NOT_FOUND", "ML run not found.")


def _loads(text: str | None, default: Any) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _dataset_columns(dataset: Dataset) -> set[str] | None:
    """返回已验证的 schema 列集合；无法验证时返回 None 以便调用方拒绝请求。"""
    raw = _loads(dataset.schema_json, None)
    if not isinstance(raw, list) or not raw:
        return None
    names: list[str] = []
    for column in raw:
        if not isinstance(column, dict):
            return None
        name = column.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        names.append(name)
    if len(set(names)) != len(names):
        return None
    return set(names)


def _unknown_columns(column_names: set[str], target: str, features: list[str]) -> list[str]:
    """返回已验证 schema 中不存在的实验列。"""
    missing: list[str] = []
    if target not in column_names:
        missing.append(target)
    missing.extend(feature for feature in features if feature not in column_names)
    return missing


def _experiment_to_response(exp: MLExperiment) -> MLExperimentResponse:
    latest = exp.runs[0] if exp.runs else None
    return MLExperimentResponse(
        id=exp.id,
        project_id=exp.project_id,
        dataset_id=exp.dataset_id,
        name=exp.name,
        task_type=exp.task_type,
        target_variable=exp.target_variable,
        feature_variables=_loads(exp.feature_variables_json, []),
        categorical_variables=_loads(exp.categorical_variables_json, []),
        config=_loads(exp.config_json, None),
        dataset_sha256=exp.dataset_sha256,
        created_by=exp.created_by,
        created_at=exp.created_at,
        run_count=len(exp.runs),
        latest_run_status=latest.status.value if latest else None,
    )


def _run_to_response(run: MLRun) -> MLRunResponse:
    return MLRunResponse(
        id=run.id,
        experiment_id=run.experiment_id,
        status=run.status,
        random_seed=run.random_seed,
        validation_strategy=run.validation_strategy,
        split=_loads(run.split_json, None),
        models=_loads(run.metrics_json, []),
        comparison=_loads(run.comparison_json, None),
        leakage_checks=_loads(run.leakage_checks_json, []),
        n_observations=run.n_observations,
        artifact_key=run.artifact_key,
        artifact_meta=_loads(run.artifact_meta_json, None),
        software_versions=_loads(run.software_versions_json, {}),
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=run.created_at,
    )


def _run_summary(run: MLRun) -> MLRunSummary:
    comparison = _loads(run.comparison_json, {}) or {}
    rows = comparison.get("rows", [])
    best = (
        rows[0]
        if rows
        and comparison.get("protocol_verified") is True
        and rows[0].get("comparable") is True
        and rows[0].get("rank_by_primary") == 1
        else None
    )
    return MLRunSummary(
        id=run.id,
        experiment_id=run.experiment_id,
        status=run.status,
        validation_strategy=run.validation_strategy,
        n_observations=run.n_observations,
        primary_metric=comparison.get("primary_metric"),
        best_model=best.get("model_id") if best else None,
        best_score=best.get("primary_value") if best else None,
        error_code=run.error_code,
        created_at=run.created_at,
    )


async def _load_experiment_with_access(
    db: AsyncSession, experiment_id: uuid.UUID, user: User
) -> MLExperiment:
    res = await db.execute(
        select(MLExperiment)
        .options(selectinload(MLExperiment.runs))
        .where(MLExperiment.id == experiment_id)
    )
    exp = res.scalar_one_or_none()
    if exp is None:
        raise MLExperimentNotFoundError()
    await dataset_service.get_dataset_with_access(db, exp.dataset_id, user.id)
    return exp


async def _reload_experiment_with_runs(db: AsyncSession, experiment_id: uuid.UUID) -> MLExperiment:
    res = await db.execute(
        select(MLExperiment)
        .options(selectinload(MLExperiment.runs))
        .where(MLExperiment.id == experiment_id)
    )
    return res.scalar_one()


async def _load_run_with_access(db: AsyncSession, run_id: uuid.UUID, user: User) -> MLRun:
    run = await db.get(MLRun, run_id)
    if run is None:
        raise MLRunNotFoundError()
    exp = await db.get(MLExperiment, run.experiment_id)
    if exp is None:
        raise MLRunNotFoundError()
    await dataset_service.get_dataset_with_access(db, exp.dataset_id, user.id)
    return run


@router.get("/api/v1/ml/metadata", response_model=MLMetadataResponse)
async def ml_metadata(current_user: User = Depends(get_current_user)) -> MLMetadataResponse:
    """Available tasks, split strategies and models (for the experiment builder)."""
    return build_metadata()


@router.get("/api/v1/datasets/{dataset_id}/ml/draft")
async def latest_draft(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.preview.workspace import read_record

    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)

    def field(path):
        if db.bind.dialect.name == "sqlite":
            return func.json_extract(WorkspaceRecord.payload_json, "$" + path)
        value = cast(WorkspaceRecord.payload_json, JSON)
        for key in path.lstrip(".").split("."):
            value = value[key]
        return value.as_string()

    identity = await db.scalar(
        select(WorkspaceRecord.id)
        .where(
            WorkspaceRecord.project_id == dataset.project_id,
            WorkspaceRecord.user_id == current_user.id,
            WorkspaceRecord.kind == "analysis",
            field(".scope.dataset_id") == str(dataset.id),
            or_(field(".ml").is_not(None), field(".ml_editor").is_not(None)),
        )
        .order_by(WorkspaceRecord.updated_at.desc(), WorkspaceRecord.id.desc())
        .limit(1)
    )
    return {"record": await read_record(db, identity, current_user) if identity else None}


@router.post("/api/v1/ml/validate")
async def ml_validate(
    body: MLExperimentCreateRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Static validation of an experiment configuration (no execution)."""
    reasons: list[str] = []
    valid_models = (
        REGRESSION_MODELS if body.task_type == MLTaskType.REGRESSION else CLASSIFICATION_MODELS
    )
    bad = [m for m in body.model_ids if m not in valid_models]
    if bad:
        reasons.append(
            f"Models not valid for {body.task_type.value}: {', '.join(m.value for m in bad)}."
        )
    if body.target in body.features:
        reasons.append("Target must not also be a feature.")
    if body.split_strategy == SplitStrategy.GROUP_KFOLD and not body.group_variable:
        reasons.append("Group K-Fold requires an explicit group variable.")
    if body.split_strategy == SplitStrategy.TIME_SERIES_SPLIT and not body.time_variable:
        reasons.append("Time-series split requires an explicit time variable.")
    return {
        "valid": not reasons,
        "reasons": reasons,
        "error_code": "INVALID_ML_INPUT" if reasons else None,
    }


@router.get("/api/v1/ml/preflight/stats")
async def ml_preflight_stats_http(
    dataset_id: uuid.UUID,
    target: str,
    request: Request,
    task_type: MLTaskType = MLTaskType.BINARY_CLASSIFICATION,
    split_strategy: SplitStrategy = SplitStrategy.STRATIFIED_KFOLD,
    group_variable: str | None = None,
    time_variable: str | None = None,
    n_splits: int = 5,
    test_size: float = 0.2,
    random_seed: int = 42,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        async with MLTaskScope(cancel_check=request.is_disconnected) as task:
            return await ml_preflight_stats(
                dataset_id=dataset_id,
                target=target,
                task_type=task_type,
                split_strategy=split_strategy,
                group_variable=group_variable,
                time_variable=time_variable,
                n_splits=n_splits,
                test_size=test_size,
                random_seed=random_seed,
                db=db,
                current_user=current_user,
                task=task,
            )
    except MLResourceError as exc:
        raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc


async def ml_preflight_stats(
    dataset_id: uuid.UUID,
    target: str,
    task_type: MLTaskType = MLTaskType.BINARY_CLASSIFICATION,
    split_strategy: SplitStrategy = SplitStrategy.STRATIFIED_KFOLD,
    group_variable: str | None = None,
    time_variable: str | None = None,
    n_splits: int = 5,
    test_size: float = 0.2,
    random_seed: int = 42,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    *,
    task: MLTaskScope | None = None,
) -> dict[str, Any]:
    """Trusted full-data statistics for folds suggestions and preflight display.

    Never derived from dataset preview rows or sampled/truncated profile summaries.
    """
    access = dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    dataset = await task.wait(access) if task else await access
    if dataset.upload_status != UploadStatus.READY:
        raise DatasetNotReadyError()
    column_names = _dataset_columns(dataset)
    if column_names is None:
        raise StatForgeError(
            422,
            "ML_SCHEMA_UNVERIFIABLE",
            "Dataset schema snapshot is missing, empty or invalid; cannot verify columns.",
        )
    if target not in column_names:
        raise StatForgeError(
            422,
            "INVALID_ML_INPUT",
            f"Dataset does not contain the following columns: {target}.",
        )
    config = MLConfig(
        task_type=task_type,
        target=target,
        features=(),
        group_variable=group_variable,
        time_variable=time_variable,
        split_strategy=split_strategy,
        n_splits=n_splits,
        test_size=test_size,
        random_seed=random_seed,
    )
    preflight_config_columns(config, column_names)
    stats = await ml_service.target_stats(dataset, config, task=task)
    return {
        "dataset_sha256": stats.dataset_sha256,
        "target": stats.target,
        "task_type": stats.task_type,
        "split_strategy": config.split_strategy.value,
        "group_variable": config.group_variable,
        "time_variable": config.time_variable,
        "n_splits": config.n_splits,
        "test_size": config.test_size,
        "random_seed": config.random_seed,
        "n_rows": stats.n_rows,
        "n_non_null_target": stats.n_non_null_target,
        "class_counts": stats.class_counts,
        "n_classes": stats.n_classes,
        "min_class_count": stats.min_class_count,
        "n_groups": stats.n_groups,
        "time_is_sorted_ascending": stats.time_is_sorted_ascending,
        "suggested_n_splits": stats.suggested_n_splits,
        "trusted": stats.trusted,
        "trust_reason": stats.trust_reason,
        "source": "training_subset_statistics",
    }


@router.post(
    "/api/v1/datasets/{dataset_id}/ml/experiments",
    response_model=MLExperimentResponse,
    status_code=201,
)
async def create_experiment_http(
    dataset_id: uuid.UUID,
    body: MLExperimentCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> MLExperimentResponse:
    return await create_experiment(
        dataset_id,
        body,
        db,
        current_user,
        cancel_check=request.is_disconnected,
        commit_result=True,
    )


async def create_experiment(
    dataset_id: uuid.UUID,
    body: MLExperimentCreateRequest,
    db: AsyncSession,
    current_user: User,
    *,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    commit_result: bool = False,
) -> MLExperimentResponse:
    try:
        async with MLTaskScope(cancel_check=cancel_check) as task:
            response = await _create_experiment(dataset_id, body, db, current_user, task=task)
            await task.check()
            if commit_result:
                try:
                    await ml_service.publish_transaction(db, task)
                except BaseException:
                    await _rollback_publication_without_masking_outcome(db)
                    raise
            return response
    except MLResourceError as exc:
        raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc


async def _create_experiment(
    dataset_id: uuid.UUID,
    body: MLExperimentCreateRequest,
    db: AsyncSession,
    current_user: User,
    *,
    task: MLTaskScope,
) -> MLExperimentResponse:
    """Create an ML experiment configuration for a dataset."""
    dataset = await task.wait(
        dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    )
    if dataset.upload_status != UploadStatus.READY:
        raise DatasetNotReadyError()
    if body.target in body.features:
        raise StatForgeError(422, "INVALID_ML_INPUT", "Target must not also be a feature.")
    valid_models = (
        REGRESSION_MODELS if body.task_type == MLTaskType.REGRESSION else CLASSIFICATION_MODELS
    )
    bad = [m for m in body.model_ids if m not in valid_models]
    if bad:
        raise StatForgeError(
            422,
            "INVALID_ML_INPUT",
            f"Models invalid for {body.task_type.value}: {', '.join(m.value for m in bad)}.",
        )
    column_names = _dataset_columns(dataset)
    if column_names is None:
        raise StatForgeError(
            422,
            "ML_SCHEMA_UNVERIFIABLE",
            "Dataset schema snapshot is missing, empty or invalid; cannot verify the "
            "requested columns. Re-upload the dataset.",
        )
    missing = _unknown_columns(column_names, body.target, body.features)
    if missing:
        raise StatForgeError(
            422,
            "INVALID_ML_INPUT",
            "Dataset does not contain the following columns: " + ", ".join(missing) + ".",
        )
    # Group/Time columns must be explicit and known before any experiment is saved.
    probe = MLConfig(
        task_type=body.task_type,
        target=body.target,
        features=tuple(body.features),
        categorical_features=tuple(body.categorical_features),
        group_variable=body.group_variable,
        time_variable=body.time_variable,
        split_strategy=body.split_strategy,
        n_splits=body.n_splits,
    )
    preflight_config_columns(probe, column_names)

    # Default model selection when none provided.
    model_ids = list(body.model_ids)
    if not model_ids:
        model_ids = (
            [MLModelId.LOGISTIC_REGRESSION, MLModelId.RANDOM_FOREST]
            if body.task_type != MLTaskType.REGRESSION
            else [MLModelId.RIDGE, MLModelId.RANDOM_FOREST_REGRESSOR]
        )

    config = MLConfig(
        task_type=body.task_type,
        target=body.target,
        features=tuple(body.features),
        categorical_features=tuple(body.categorical_features),
        group_variable=body.group_variable,
        time_variable=body.time_variable,
        split_strategy=body.split_strategy,
        test_size=body.test_size,
        val_size=body.val_size,
        n_splits=body.n_splits,
        random_seed=body.random_seed,
        model_ids=tuple(model_ids),
        standardize=body.standardize,
        impute_numeric=body.impute_numeric,
        impute_categorical=body.impute_categorical,
        handle_outliers=body.handle_outliers,
        feature_selection_k=body.feature_selection_k,
        model_params=body.model_params,
    )
    profile_id = await task.wait(ml_service.latest_profile_id(db, dataset))
    # Dataset-aware preflight (class counts, group counts, time order) BEFORE
    # persisting the experiment. Infeasible requests get a stable 422 and must
    # leave zero experiment/run rows.
    await ml_service.preflight_experiment(dataset, config, column_names=column_names, task=task)
    await task.check()
    experiment = await task.wait(
        ml_service.create_experiment(db, dataset, current_user, config, body.name)
    )
    if profile_id:
        experiment.profile_id = profile_id
        await task.wait(db.flush())
    experiment = await task.wait(_reload_experiment_with_runs(db, experiment.id))
    return _experiment_to_response(experiment)


@router.get(
    "/api/v1/datasets/{dataset_id}/ml/experiments",
    response_model=list[MLExperimentResponse],
)
async def list_experiments(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[MLExperimentResponse]:
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    experiments = await ml_service.list_experiments(db, dataset.id)
    return [_experiment_to_response(e) for e in experiments]


@router.get("/api/v1/ml/experiments/{experiment_id}", response_model=MLExperimentResponse)
async def get_experiment(
    experiment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> MLExperimentResponse:
    exp = await _load_experiment_with_access(db, experiment_id, current_user)
    return _experiment_to_response(exp)


@router.post(
    "/api/v1/ml/experiments/{experiment_id}/run",
    response_model=MLRunResponse,
    status_code=201,
)
async def run_experiment_http(
    experiment_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> MLRunResponse:
    return await run_experiment(
        experiment_id, db, current_user, cancel_check=request.is_disconnected, commit_result=True
    )


async def run_experiment(
    experiment_id: uuid.UUID,
    db: AsyncSession,
    current_user: User,
    *,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
    commit_result: bool = False,
) -> MLRunResponse:
    """Run an experiment (creates a new run; history is preserved)."""
    try:
        async with MLTaskScope(cancel_check=cancel_check) as task:
            exp = await task.wait(_load_experiment_with_access(db, experiment_id, current_user))
            run = await ml_service.run_experiment(db, exp, current_user, task=task)
            # Cancellation is checked again after ACL/audit/flush awaits and at
            # the publication boundary. Workflow retains its own transaction.
            await task.check()
            if commit_result:
                try:
                    await ml_service.publish_transaction(db, task)
                except BaseException:
                    await _rollback_publication_without_masking_outcome(db)
                    raise
            return _run_to_response(run)
    except MLResourceError as exc:
        raise StatForgeError(429 if exc.code == "ML_BUSY" else 422, exc.code, str(exc)) from exc


@router.get("/api/v1/ml/experiments/{experiment_id}/runs", response_model=MLRunsResponse)
async def list_runs(
    experiment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> MLRunsResponse:
    exp = await _load_experiment_with_access(db, experiment_id, current_user)
    runs = list(exp.runs)
    return MLRunsResponse(runs=[_run_summary(r) for r in runs], total=len(runs))


@router.get("/api/v1/ml/runs/{run_id}", response_model=MLRunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> MLRunResponse:
    run = await _load_run_with_access(db, run_id, current_user)
    return _run_to_response(run)


@router.get("/api/v1/ml/runs/{run_id}/metrics")
async def get_run_metrics(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    run = await _load_run_with_access(db, run_id, current_user)
    return {
        "run_id": str(run.id),
        "status": run.status.value,
        "models": _loads(run.metrics_json, []),
        "comparison": _loads(run.comparison_json, None),
        "leakage_checks": _loads(run.leakage_checks_json, []),
    }


@router.get("/api/v1/ml/runs/{run_id}/artifacts", response_model=None)
async def get_run_artifact(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> FileResponse | RedirectResponse:
    """Stream a local model artifact or redirect to remote object storage."""
    run = await _load_run_with_access(db, run_id, current_user)
    if not run.artifact_key:
        raise MLRunNotFoundError()
    if settings.storage_backend == "local":
        return FileResponse(
            path=storage_service.get_local_object_path(run.artifact_key),
            media_type="application/octet-stream",
            filename=f"statforge-ml-run-{run.id}.joblib",
        )
    url = storage_service.get_presigned_download_url(run.artifact_key)
    return RedirectResponse(url=url, status_code=302)
