"""Server authorization precedes payload reads; disclosure is a whitelist projection."""

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload

from app.config import settings
from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.ml import MLExperiment, MLRun
from app.models.project import Project
from app.models.statistics import StatisticalRun, StatResult
from app.models.user import User
from app.routers import ml as ml_routes
from app.routers import statistics as stat_routes
from app.schemas.preview import PreviewScope
from app.services import storage_service
from app.services.material_service import resolve_references
from app.services.workflow_service import check_dataset_access, check_project_access


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def invalid() -> StatForgeError:
    return StatForgeError(
        409, "PREVIEW_SCOPE_INVALID", "Context changed or is unavailable; approve again."
    )


def schema_snapshot(schema_json: str | None) -> list[dict[str, str]]:
    try:
        raw = json.loads(schema_json or "null")
        if not isinstance(raw, list) or not raw:
            raise invalid()
        columns = [
            {"name": c["name"], "dtype": c["polars_type"], "inferred_type": c["inferred_type"]}
            for c in raw
        ]
        if any(not isinstance(v, str) for c in columns for v in c.values()):
            raise invalid()
        if len({c["name"] for c in columns}) != len(columns):
            raise invalid()
        return columns
    except (KeyError, TypeError, ValueError):
        raise invalid() from None


async def prepare_context(db: AsyncSession, scope: PreviewScope, user: User) -> dict[str, Any]:
    """Prepare an explicit approval candidate; never silently refresh an approved scope."""
    if scope.dataset_id is not None:
        await check_dataset_access(db, scope.project_id, scope.dataset_id, user)
        with db.no_autoflush:
            row = (
                await db.execute(
                    select(Dataset.sha256, Dataset.schema_json).where(
                        Dataset.id == scope.dataset_id
                    )
                )
            ).one()
        columns = schema_snapshot(row.schema_json)
        scope = scope.model_copy(
            update={
                "dataset_sha256": scope.dataset_sha256 or row.sha256,
                "schema_sha256": scope.schema_sha256 or digest(columns),
            }
        )
    return await resolve_context(db, scope, user)


async def load_dataset(db: AsyncSession, scope: PreviewScope, user: User) -> Dataset:
    await check_project_access(db, scope.project_id, user)
    if scope.dataset_id is None:
        raise invalid()
    await check_dataset_access(db, scope.project_id, scope.dataset_id, user)
    with db.no_autoflush:
        dataset = await db.scalar(
            select(Dataset)
            .options(
                noload("*"),
                load_only(
                    Dataset.id,
                    Dataset.project_id,
                    Dataset.sha256,
                    Dataset.schema_json,
                    Dataset.upload_status,
                    Dataset.row_count,
                    Dataset.column_count,
                    Dataset.object_key,
                    Dataset.file_format,
                    Dataset.preview_json,
                ),
            )
            .where(Dataset.id == scope.dataset_id)
            .execution_options(populate_existing=True)
        )
    if dataset is None or dataset.upload_status != UploadStatus.READY:
        raise invalid()
    columns = schema_snapshot(dataset.schema_json)
    if (
        scope.dataset_sha256 != dataset.sha256
        or scope.schema_sha256 != digest(columns)
        or not scope.columns
        or len(set(scope.columns)) != len(scope.columns)
        or not set(scope.columns) <= {c["name"] for c in columns}
    ):
        raise invalid()
    return dataset


async def verify_storage_version(dataset: Dataset) -> None:
    """Verify authorized immutable storage bytes before local compute/side effects."""

    def verify() -> None:
        parent = Path(settings.app_data_path or settings.upload_temp_dir) / "preview-temp"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="source-", dir=parent) as directory:
            path = Path(directory) / "dataset"
            storage_service.download_to_path(dataset.object_key, str(path))
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != dataset.sha256:
                raise invalid()

    await anyio.to_thread.run_sync(verify)


async def statistical_facts(
    db: AsyncSession, scope: PreviewScope, user: User, run_id: uuid.UUID
) -> dict[str, Any]:
    await load_dataset(db, scope, user)
    with db.no_autoflush:
        identity = (
            await db.execute(
                select(
                    StatisticalRun.dataset_id,
                    StatisticalRun.project_id,
                    StatisticalRun.dataset_sha256,
                    StatisticalRun.input_config_json,
                ).where(StatisticalRun.id == run_id)
            )
        ).one_or_none()
    if (
        identity is None
        or identity.dataset_id != scope.dataset_id
        or identity.project_id != scope.project_id
        or identity.dataset_sha256 != scope.dataset_sha256
    ):
        raise invalid()
    saved_input = json.loads(identity.input_config_json or "{}")
    selected = [*saved_input.get("variables", []), *saved_input.get("categorical_variables", [])]
    selected += [v for v in (saved_input.get("group_variable"), saved_input.get("target")) if v]
    selected += list(saved_input.get("reference_categories") or {})
    if saved_input.get("method_id") in {"evaluate_predictions", "evaluate_binary_predictions"}:
        from app.schemas.preview import StatisticalForm

        parameters = dict(saved_input)
        parameters["missing_value_policy"] = parameters.pop("missing_policy", "error_on_missing")
        evaluation = StatisticalForm.model_validate(parameters).evaluation
        if evaluation is None or any(
            field.dataset_id != scope.dataset_id or field.dataset_sha256 != scope.dataset_sha256
            for field in (evaluation.actual, evaluation.predicted)
        ):
            raise invalid()
        selected = [evaluation.actual.name, evaluation.predicted.name]
    if not selected or not set(selected) <= set(scope.columns):
        raise invalid()
    with db.no_autoflush:
        row = (
            await db.execute(
                select(
                    StatisticalRun.method,
                    StatisticalRun.status,
                    StatisticalRun.sample_size,
                    StatResult.statistic_name,
                    StatResult.statistic_value,
                    StatResult.p_value,
                )
                .outerjoin(StatResult, StatResult.statistical_run_id == StatisticalRun.id)
                .where(StatisticalRun.id == run_id)
            )
        ).first()
    assert row is not None
    return {
        "kind": "statistics",
        "project_id": str(scope.project_id),
        "dataset_id": str(scope.dataset_id),
        "schema_sha256": scope.schema_sha256,
        "run_id": str(run_id),
        "dataset_sha256": scope.dataset_sha256,
        "method": row.method,
        "status": row.status.value,
        "n": row.sample_size,
        "statistic_name": row.statistic_name,
        "statistic": row.statistic_value,
        "p_value": row.p_value,
    }


async def ml_facts(
    db: AsyncSession, scope: PreviewScope, user: User, run_id: uuid.UUID
) -> dict[str, Any]:
    await load_dataset(db, scope, user)
    with db.no_autoflush:
        identity = (
            await db.execute(
                select(
                    MLExperiment.dataset_id,
                    MLExperiment.project_id,
                    MLExperiment.dataset_sha256,
                    MLExperiment.config_json,
                )
                .join(MLRun, MLRun.experiment_id == MLExperiment.id)
                .where(MLRun.id == run_id)
            )
        ).one_or_none()
    if (
        identity is None
        or identity.dataset_id != scope.dataset_id
        or identity.project_id != scope.project_id
        or identity.dataset_sha256 != scope.dataset_sha256
    ):
        raise invalid()
    saved_input = json.loads(identity.config_json or "{}")
    selected = [*saved_input.get("features", []), *saved_input.get("categorical_features", [])]
    selected += [
        v
        for v in (
            saved_input.get("target"),
            saved_input.get("group_variable"),
            saved_input.get("time_variable"),
        )
        if v
    ]
    if not selected or not set(selected) <= set(scope.columns):
        raise invalid()
    with db.no_autoflush:
        row = (
            await db.execute(
                select(MLRun.status, MLRun.n_observations, MLRun.comparison_json).where(
                    MLRun.id == run_id
                )
            )
        ).one()
    comparison = json.loads(row.comparison_json or "{}")
    # Whitelist scalar scores only. Never serialize metrics_json, predictions,
    # diagnostics, leakage examples, artifact paths, errors or class/row labels.
    scores = [
        {"model_id": r.get("model_id"), "score": r.get("primary_value")}
        for r in comparison.get("rows", [])
        if isinstance(r, dict) and isinstance(r.get("primary_value"), (int, float))
    ]
    return {
        "kind": "ml",
        "project_id": str(scope.project_id),
        "dataset_id": str(scope.dataset_id),
        "schema_sha256": scope.schema_sha256,
        "run_id": str(run_id),
        "dataset_sha256": scope.dataset_sha256,
        "status": row.status.value,
        "n": row.n_observations,
        "primary_metric": comparison.get("primary_metric"),
        "scores": scores,
    }


ML_PREFLIGHT_FIELDS = (
    "n_rows",
    "n_non_null_target",
    "n_classes",
    "min_class_count",
    "n_groups",
    "trusted",
)


async def resolve_context(db: AsyncSession, scope: PreviewScope, user: User) -> dict[str, Any]:
    await check_project_access(db, scope.project_id, user)
    with db.no_autoflush:
        project = (
            await db.execute(
                select(Project.title, Project.description).where(Project.id == scope.project_id)
            )
        ).one()
    categories: dict[str, Any] = {
        "project": {"title": project.title, "description": project.description}
    }
    if scope.dataset_id is not None:
        dataset = await load_dataset(db, scope, user)
        categories["schema"] = {
            "dataset_sha256": dataset.sha256,
            "columns": [
                c for c in schema_snapshot(dataset.schema_json) if c["name"] in scope.columns
            ],
        }
        categories["quality"] = {
            "row_count": dataset.row_count,
            "allowed_summary": "selected-column null counts; no row examples",
        }
    elif any(
        [
            scope.columns,
            scope.dataset_sha256,
            scope.schema_sha256,
            scope.statistical_run_id,
            scope.ml_run_id,
            scope.statistical_config,
            scope.ml_config,
        ]
    ):
        raise invalid()
    if scope.statistical_config is not None and scope.ml_config is not None:
        raise invalid()
    if scope.statistical_config is not None:
        form = scope.statistical_config
        used = [*form.variables, *form.categorical_variables, *(form.reference_categories or {})]
        used += [v for v in (form.group_variable, form.target) if v]
        if not set(used) <= set(scope.columns):
            raise invalid()
        validation = await stat_routes.validate_method(body=form, current_user=user)
        if not validation.applicable:
            raise invalid()
        categories["form"] = {"kind": "statistics", "config": form.model_dump(mode="json")}
    if scope.ml_config is not None:
        ml_form = scope.ml_config
        used_ml = [*ml_form.features, *ml_form.categorical_features, ml_form.target]
        used_ml += [v for v in (ml_form.group_variable, ml_form.time_variable) if v]
        if ml_form.model_params or not set(used_ml) <= set(scope.columns):
            raise invalid()
        validation_ml = await ml_routes.ml_validate(body=ml_form, current_user=user)
        if not validation_ml["valid"]:
            raise invalid()
        categories["form"] = {"kind": "ml", "config": ml_form.model_dump(mode="json")}
        categories["quality"]["allowed_ml_preflight"] = {
            "requires_exact_approved_form": True,
            "fields": list(ML_PREFLIGHT_FIELDS),
            "config_digest": digest(ml_form.model_dump(mode="json")),
            "meaning": "Aggregate target/group counts and trust; no labels, rows or reasons.",
        }
    results = []
    if scope.statistical_run_id:
        results.append(await statistical_facts(db, scope, user, scope.statistical_run_id))
    if scope.ml_run_id:
        results.append(await ml_facts(db, scope, user, scope.ml_run_id))
    if scope.dataset_id:
        categories["results"] = {
            "current": results,
            "allowed_summary": "run ref/status/n/statistic/p-value or aggregate ML scores",
        }
    if scope.material_references:
        categories["materials"] = await resolve_references(
            db, scope.project_id, user.id, scope.material_references
        )
    if scope.session_id is not None or scope.session_revision is not None:
        from app.preview.extensions import session_categories

        categories.update(await session_categories(db, scope, user))
    return {
        "scope": scope.model_dump(mode="json"),
        "categories": categories,
        "disclosure_hash": digest(
            {"scope": scope.model_dump(mode="json"), "categories": categories}
        ),
    }
