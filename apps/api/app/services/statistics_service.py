"""Statistical-run orchestration: load data → run method → persist.

The heavy work (MinIO download, parsing, computation) runs in a worker thread.
Each run links the latest completed profile and quality run when present, and
records full reproducibility metadata (dataset hash, input config, policy,
software versions).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ProfileComputationError
from app.models.dataset import Dataset
from app.models.profiling import DatasetProfile, ProfileStatus
from app.models.quality import QualityRun, QualityRunStatus
from app.models.statistics import StatFinding, StatisticalRun, StatisticalRunStatus, StatResult
from app.models.user import User
from app.services import audit_service, storage_service
from app.services.profiling_service import load_dataframe
from app.statistics.context import MethodConfig
from app.statistics.registry import METHODSET_VERSION
from app.statistics.service import run_statistical_analysis
from app.statistics.types import MethodStatus

logger = logging.getLogger("statforge.statistics")


def _config_from_request(request: Any) -> MethodConfig:
    evaluation = getattr(request, "evaluation", None)
    return MethodConfig(
        method_id=request.method_id,
        variables=tuple(request.variables),
        group_variable=request.group_variable,
        paired=request.paired,
        mu=request.mu,
        expected_proportions=request.expected_proportions,
        alternative=request.alternative,
        confidence_level=request.confidence_level,
        missing_policy=request.missing_value_policy,
        target=request.target,
        categorical_variables=tuple(request.categorical_variables),
        reference_categories=request.reference_categories,
        standardize=request.standardize,
        alpha=request.alpha,
        l1_ratio=request.l1_ratio,
        evaluation=(evaluation.model_dump(mode="json") if evaluation else None),
    )


def _worker(
    object_key: str,
    tmp_path: str,
    file_format: str,
    config: MethodConfig,
    sha256: str,
    sheet_name: str | None = None,
    csv_encoding: str | None = None,
) -> dict:
    """Blocking: download → parse → run the statistical method."""
    storage_service.download_to_path(object_key, tmp_path)
    if config.evaluation is not None:
        import hashlib

        with open(tmp_path, "rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != sha256:
                raise ValueError("Evaluation stored snapshot bytes changed.")
    df = load_dataframe(tmp_path, file_format, sheet_name, csv_encoding)
    result = run_statistical_analysis(df, config, dataset_sha256=sha256)
    return result.to_dict()


async def _latest_profile(db: AsyncSession, dataset: Dataset) -> DatasetProfile | None:
    res = await db.execute(
        select(DatasetProfile)
        .where(
            DatasetProfile.dataset_id == dataset.id,
            DatasetProfile.status == ProfileStatus.COMPLETED,
            DatasetProfile.dataset_sha256 == dataset.sha256,
        )
        .order_by(DatasetProfile.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _latest_quality_run(db: AsyncSession, dataset: Dataset) -> QualityRun | None:
    res = await db.execute(
        select(QualityRun)
        .where(
            QualityRun.dataset_id == dataset.id,
            QualityRun.status.in_(
                [QualityRunStatus.COMPLETED, QualityRunStatus.COMPLETED_WITH_ERRORS]
            ),
        )
        .order_by(QualityRun.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


def _persist_result_and_findings(db: AsyncSession, run: StatisticalRun, result: dict) -> None:
    statistic = result.get("statistic", {}) or {}
    db.add(
        StatResult(
            statistical_run_id=run.id,
            method=result["method"],
            result_type="test",
            statistic_name=statistic.get("name"),
            statistic_value=statistic.get("value"),
            degrees_of_freedom_json=json.dumps(
                {k: v for k, v in statistic.items() if "df" in k}, ensure_ascii=False
            ),
            p_value=result.get("p_value"),
            alternative=result.get("alternative", "two_sided"),
            confidence_level=result.get("confidence_level", 0.95),
            confidence_interval_json=json.dumps(
                result.get("confidence_interval", {}), ensure_ascii=False
            ),
            effect_size_json=json.dumps(result.get("effect_size", {}), ensure_ascii=False),
            descriptive_statistics_json=json.dumps(
                result.get("descriptive_statistics", {}), ensure_ascii=False
            ),
            assumption_checks_json=json.dumps(
                result.get("assumption_checks", []), ensure_ascii=False
            ),
            warnings_json=json.dumps(result.get("warnings", []), ensure_ascii=False),
            result_json=json.dumps(result, ensure_ascii=False),
            software_versions_json=json.dumps(
                result.get("software_versions", {}), ensure_ascii=False
            ),
            created_at=datetime.now(UTC),
        )
    )
    # Persist failed assumption checks as findings (so they surface in the UI).
    for check in result.get("assumption_checks", []):
        if check.get("passed"):
            continue
        db.add(
            StatFinding(
                statistical_run_id=run.id,
                rule_id=check.get("name", "assumption"),
                category="assumption",
                severity="medium",
                title=f"Assumption not met: {check.get('name')}",
                summary=check.get("detail"),
                evidence_json=json.dumps(
                    {"value": check.get("value"), "threshold": check.get("threshold")},
                    ensure_ascii=False,
                    default=str,
                ),
                remediation="Review the method's assumptions before trusting this result.",
                created_at=datetime.now(UTC),
            )
        )


_STATUS_MAP = {
    MethodStatus.COMPLETED.value: StatisticalRunStatus.COMPLETED,
    MethodStatus.NOT_APPLICABLE.value: StatisticalRunStatus.NOT_APPLICABLE,
    MethodStatus.FAILED.value: StatisticalRunStatus.FAILED,
}


async def run_statistics(
    db: AsyncSession,
    dataset: Dataset,
    user: User,
    request: Any,
    *,
    task=None,
    on_started=None,
    on_computed=None,
) -> StatisticalRun:
    """Execute and persist a statistical method run for a dataset."""
    config = _config_from_request(request)
    if request.evaluation is not None:
        # Direct statistical requests need the same exact metadata binding as
        # native Plans. The arithmetic core separately checks the executed SHA.
        from app.services import research_source_service as sources

        references = (request.evaluation.actual, request.evaluation.predicted)
        metadata, proof, *_ = await sources._dataset(
            db, user.id, dataset.id, [ref.name for ref in references], "metadata_only"
        )
        for ref in references:
            roles = [item for item in proof["roles"] if item["name"] == ref.name]
            if (
                ref.dataset_id != dataset.id
                or ref.dataset_sha256 != metadata["sha256"]
                or ref.schema_sha256 != proof["schema_sha256"]
                or len(roles) > 1
                or (str(ref.variable_id) if ref.variable_id else None)
                != (roles[0]["id"] if roles else None)
            ):
                raise sources.failure("EVALUATION_SOURCE_CHANGED", 422)
    started_at = datetime.now(UTC)
    run = StatisticalRun(
        project_id=dataset.project_id,
        dataset_id=dataset.id,
        method=request.method_id.value,
        status=StatisticalRunStatus.RUNNING,
        methodset_version=METHODSET_VERSION,
        dataset_sha256=dataset.sha256,
        input_config_json=json.dumps(config.to_dict(), ensure_ascii=False),
        missing_value_policy=request.missing_value_policy.value,
        started_at=started_at,
        created_by=user.email,
    )
    if on_started is not None:
        await on_started(run)
    else:
        db.add(run)
        await db.flush()

    suffix = f".{dataset.file_format}" if dataset.file_format else ".tmp"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sf_stat_")
    os.close(fd)
    try:
        if task is None:
            result = await anyio.to_thread.run_sync(
                _worker,
                dataset.object_key,
                tmp_path,
                dataset.file_format,
                config,
                dataset.sha256,
                dataset.sheet_name,
                dataset.csv_encoding,
            )
        else:
            from app.ml.worker_control import run_ml_worker

            await task.wait(
                anyio.to_thread.run_sync(
                    storage_service.download_to_path, dataset.object_key, tmp_path
                )
            )
            output = await run_ml_worker(
                tmp_path,
                dataset.file_format,
                request.model_dump(mode="json"),
                dataset.sha256,
                operation="manual_statistics",
                task=task,
                sheet_name=dataset.sheet_name,
                csv_encoding=dataset.csv_encoding,
            )
            result = output["result"]
    except Exception as exc:  # noqa: BLE001
        if task is not None:
            raise
        run.status = StatisticalRunStatus.FAILED
        run.completed_at = datetime.now(UTC)
        run.error_code = "STATISTICS_RUN_FAILED"
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        await db.flush()
        await audit_service.record(
            db,
            event_type="statistics_run.failed",
            entity_type="dataset",
            entity_id=str(dataset.id),
            actor=user,
            project_id=dataset.project_id,
            after={"method": run.method, "error": run.error_message},
        )
        logger.exception("Statistics run failed for dataset %s", dataset.id)
        raise ProfileComputationError() from exc
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if on_computed is not None:
        await on_computed(run)
    profile = await _latest_profile(db, dataset)
    quality_run = await _latest_quality_run(db, dataset)
    run.profile_id = profile.id if profile else None
    run.quality_run_id = quality_run.id if quality_run else None
    run.completed_at = datetime.now(UTC)
    state = result.get("status")
    run.status = (
        _STATUS_MAP.get(state, StatisticalRunStatus.FAILED)
        if isinstance(state, str)
        else StatisticalRunStatus.FAILED
    )
    sample = result.get("sample") or {}
    run.sample_size = sample.get("final_n")
    if result.get("error_code"):
        run.error_code = result.get("error_code")
        run.error_message = result.get("error_message")

    _persist_result_and_findings(db, run, result)

    await audit_service.record(
        db,
        event_type="statistics_run.completed",
        entity_type="dataset",
        entity_id=str(dataset.id),
        actor=user,
        project_id=dataset.project_id,
        after={
            "run_id": str(run.id),
            "method": run.method,
            "status": run.status.value,
            "p_value": result.get("p_value"),
        },
    )
    await db.flush()
    await db.refresh(run)
    logger.info(
        "Statistics run complete: dataset=%s method=%s status=%s",
        dataset.id,
        run.method,
        run.status.value,
    )
    return run


async def latest_run(
    db: AsyncSession, dataset_id: Any, method: str | None = None
) -> StatisticalRun | None:
    stmt = (
        select(StatisticalRun)
        .where(StatisticalRun.dataset_id == dataset_id)
        .order_by(StatisticalRun.created_at.desc())
    )
    if method:
        stmt = stmt.where(StatisticalRun.method == method)
    res = await db.execute(stmt.limit(1))
    return res.scalar_one_or_none()
