"""Quality-run orchestration: profile (if needed) → run rules → persist.

The heavy work (MinIO download, parsing, profiling, rule evaluation) runs in a
worker thread. The same data is profiled and rule-checked in one pass so the
file is downloaded only once per run.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ProfileComputationError
from app.models.dataset import Dataset
from app.models.profiling import DatasetProfile, ProfileStatus
from app.models.quality import QualityFinding, QualityRun, QualityRunStatus
from app.models.user import User
from app.quality.registry import RULESET_VERSION
from app.quality.rules import OHLCV_RULESET_VERSION
from app.quality.service import run_quality_check
from app.quality.thresholds import ThresholdConfig
from app.services import audit_service, storage_service
from app.services.profiling_service import (
    ALGORITHM_VERSION,
    PROFILING_VERSION,
    compute_profile,
    load_dataframe,
)

logger = logging.getLogger("statforge.quality")


def _worker(
    object_key: str,
    tmp_path: str,
    file_format: str,
    sha256: str,
    thresholds_dict: dict[str, Any],
    enable_ohlcv: bool,
    ohlcv_mapping: dict[str, str] | None,
    sheet_name: str | None = None,
    csv_encoding: str | None = None,
) -> dict[str, Any]:
    """Blocking: download → parse → profile → run quality rules."""
    storage_service.download_to_path(object_key, tmp_path)
    df = load_dataframe(tmp_path, file_format, sheet_name, csv_encoding)
    profile = compute_profile(df, sha256)
    thresholds = ThresholdConfig(**thresholds_dict)
    report = run_quality_check(
        df,
        profile,
        thresholds,
        dataset_sha256=sha256,
        enable_ohlcv=enable_ohlcv,
        ohlcv_mapping=ohlcv_mapping,
    )
    return {"profile": profile, "report": report}


async def _ensure_profile(
    db: AsyncSession, dataset: Dataset, profile_result: dict[str, Any]
) -> DatasetProfile:
    """Return a completed profile for the dataset, creating one if needed.

    Reuses the latest completed profile when the content SHA still matches;
    otherwise marks prior profiles stale and persists a fresh one.
    """
    latest = await db.execute(
        select(DatasetProfile)
        .where(DatasetProfile.dataset_id == dataset.id)
        .order_by(DatasetProfile.created_at.desc())
        .limit(1)
    )
    existing = latest.scalar_one_or_none()
    if (
        existing is not None
        and existing.status == ProfileStatus.COMPLETED
        and existing.dataset_sha256 == dataset.sha256
    ):
        return existing

    # Mark older profiles stale.
    older = await db.execute(
        select(DatasetProfile).where(
            DatasetProfile.dataset_id == dataset.id,
            DatasetProfile.status != ProfileStatus.STALE,
        )
    )
    for p in older.scalars().all():
        p.status = ProfileStatus.STALE

    overview = profile_result["overview"]
    profile = DatasetProfile(
        dataset_id=dataset.id,
        status=ProfileStatus.COMPLETED,
        dataset_sha256=dataset.sha256,
        profiling_version=PROFILING_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        row_count=overview["row_count"],
        column_count=overview["column_count"],
        sampled=overview["sampled"],
        sample_size=overview["sample_size"],
        result_json=json.dumps(profile_result, ensure_ascii=False),
    )
    db.add(profile)
    dataset.profile_status = "profiled"
    await db.flush()
    return profile


def _persist_findings(db: AsyncSession, run: QualityRun, findings: list[dict[str, Any]]) -> None:
    for f in findings:
        db.add(
            QualityFinding(
                quality_run_id=run.id,
                rule_id=f["rule_id"],
                rule_version=f.get("rule_version", "1"),
                category=f["category"],
                severity=f["severity"],
                status=f["status"],
                title=f["title"],
                summary=f.get("summary"),
                evidence_json=json.dumps(f.get("evidence", {}), ensure_ascii=False),
                affected_columns_json=json.dumps(f.get("affected_columns", []), ensure_ascii=False),
                affected_count=int(f.get("affected_count", 0) or 0),
                affected_ratio=float(f.get("affected_ratio", 0.0) or 0.0),
                threshold_json=json.dumps(f.get("threshold"), ensure_ascii=False)
                if f.get("threshold") is not None
                else None,
                observed_value_json=json.dumps(
                    f.get("observed_value"), ensure_ascii=False, default=str
                )
                if f.get("observed_value") is not None
                else None,
                remediation=f.get("remediation", ""),
                created_at=datetime.now(UTC),
            )
        )


async def run_quality(
    db: AsyncSession,
    dataset: Dataset,
    user: User,
    thresholds: ThresholdConfig,
    enable_ohlcv: bool = False,
    ohlcv_mapping: dict[str, str] | None = None,
) -> QualityRun:
    """Execute and persist a quality run for a dataset."""
    started_at = datetime.now(UTC)
    ruleset_versions = [RULESET_VERSION] + ([OHLCV_RULESET_VERSION] if enable_ohlcv else [])
    run = QualityRun(
        dataset_id=dataset.id,
        status=QualityRunStatus.RUNNING,
        ruleset_version="+".join(ruleset_versions),
        threshold_config_json=json.dumps(thresholds.to_dict(), ensure_ascii=False),
        dataset_sha256=dataset.sha256,
        started_at=started_at,
        ohlcv_enabled=enable_ohlcv,
        created_by=user.email,
    )
    db.add(run)
    await db.flush()

    suffix = f".{dataset.file_format}" if dataset.file_format else ".tmp"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sf_quality_")
    os.close(fd)
    try:
        output = await anyio.to_thread.run_sync(
            _worker,
            dataset.object_key,
            tmp_path,
            dataset.file_format,
            dataset.sha256,
            thresholds.to_dict(),
            enable_ohlcv,
            ohlcv_mapping,
            dataset.sheet_name,
            dataset.csv_encoding,
        )
    except Exception as exc:  # noqa: BLE001
        run.status = QualityRunStatus.FAILED
        run.completed_at = datetime.now(UTC)
        run.error_code = "QUALITY_RUN_FAILED"
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        await db.flush()
        await audit_service.record(
            db,
            event_type="quality_run.failed",
            entity_type="dataset",
            entity_id=dataset.id,
            actor=user,
            project_id=dataset.project_id,
            after={"error": run.error_message},
        )
        logger.exception("Quality run failed for dataset %s", dataset.id)
        raise ProfileComputationError() from exc
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    profile_result = output["profile"]
    report = output["report"]

    profile = await _ensure_profile(db, dataset, profile_result)

    counts = report["counts"]
    run.profile_id = profile.id
    run.completed_at = datetime.now(UTC)
    run.sampled = bool(report.get("sampled", False))
    run.total_rules = int(report.get("total_rules", 0))
    run.passed_count = int(counts.get("passed", 0))
    run.warning_count = int(counts.get("warning", 0))
    run.failed_count = int(counts.get("failed", 0))
    run.error_count = int(counts.get("error", 0))
    run.skipped_count = int(counts.get("skipped", 0))
    run.overall_score = float(report["score"]["total_score"])
    run.score_json = json.dumps(report["score"], ensure_ascii=False)
    run.status = (
        QualityRunStatus.COMPLETED_WITH_ERRORS
        if run.error_count > 0
        else QualityRunStatus.COMPLETED
    )

    _persist_findings(db, run, report["findings"])

    await audit_service.record(
        db,
        event_type="quality_run.completed",
        entity_type="dataset",
        entity_id=dataset.id,
        actor=user,
        project_id=dataset.project_id,
        after={
            "run_id": str(run.id),
            "score": run.overall_score,
            "failed": run.failed_count,
            "warning": run.warning_count,
            "ohlcv": enable_ohlcv,
        },
    )

    await db.flush()
    await db.refresh(run)
    logger.info(
        "Quality run complete: dataset=%s score=%.2f failed=%d warning=%d",
        dataset.id,
        run.overall_score or 0.0,
        run.failed_count,
        run.warning_count,
    )
    return run


async def latest_run(db: AsyncSession, dataset_id: uuid.UUID) -> QualityRun | None:
    result = await db.execute(
        select(QualityRun)
        .where(QualityRun.dataset_id == dataset_id)
        .order_by(QualityRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
