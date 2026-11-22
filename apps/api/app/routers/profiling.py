"""Data profiling routes: compute, retrieve, and slice dataset profiles."""

import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import (
    DatasetNotReadyError,
    ProfileColumnNotFoundError,
    ProfileComputationError,
    ProfileNotFoundError,
)
from app.models.dataset import UploadStatus
from app.models.profiling import DatasetProfile, ProfileStatus
from app.models.user import User
from app.schemas.profiling import (
    ProfileColumnsResponse,
    ProfileCorrelationsResponse,
    ProfileMissingnessResponse,
    ProfileResponse,
)
from app.services import audit_service, dataset_service, storage_service
from app.services.profiling_service import (
    ALGORITHM_VERSION,
    PROFILING_VERSION,
    compute_profile,
    load_dataframe,
)

logger = logging.getLogger("statforge.profiling")
router = APIRouter(tags=["profiling"])


# ── Helpers ──────────────────────────────────────────────────────────────────


def _to_response(profile: DatasetProfile) -> ProfileResponse:
    """Build a ProfileResponse, parsing the stored result JSON."""
    return ProfileResponse(
        id=profile.id,
        dataset_id=profile.dataset_id,
        status=profile.status,
        profiling_version=profile.profiling_version,
        algorithm_version=profile.algorithm_version,
        dataset_sha256=profile.dataset_sha256,
        started_at=profile.started_at,
        completed_at=profile.completed_at,
        error_code=profile.error_code,
        error_message=profile.error_message,
        row_count=profile.row_count,
        column_count=profile.column_count,
        sampled=profile.sampled,
        sample_size=profile.sample_size,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        result=json.loads(profile.result_json) if profile.result_json else None,
    )


async def _latest_profile(db: AsyncSession, dataset_id: uuid.UUID) -> DatasetProfile | None:
    result = await db.execute(
        select(DatasetProfile)
        .where(DatasetProfile.dataset_id == dataset_id)
        .order_by(DatasetProfile.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _require_completed_profile(
    db: AsyncSession, dataset_id: uuid.UUID
) -> tuple[DatasetProfile, dict[str, Any]]:
    profile = await _latest_profile(db, dataset_id)
    if profile is None or not profile.result_json:
        raise ProfileNotFoundError()
    return profile, json.loads(profile.result_json)


async def _mark_existing_stale(db: AsyncSession, dataset_id: uuid.UUID) -> None:
    """Mark all existing profiles for a dataset as stale."""
    result = await db.execute(
        select(DatasetProfile).where(
            DatasetProfile.dataset_id == dataset_id,
            DatasetProfile.status != ProfileStatus.STALE,
        )
    )
    for existing in result.scalars().all():
        existing.status = ProfileStatus.STALE


def _download_and_compute(
    object_key: str,
    tmp_path: str,
    file_format: str,
    sha256: str,
    sheet_name: str | None = None,
    csv_encoding: str | None = None,
) -> dict:
    """Blocking work (runs in a worker thread): MinIO download → parse → profile."""
    storage_service.download_to_path(object_key, tmp_path)
    df = load_dataframe(tmp_path, file_format, sheet_name, csv_encoding)
    return compute_profile(df, sha256)


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post(
    "/api/v1/datasets/{dataset_id}/profile",
    response_model=ProfileResponse,
    status_code=201,
)
async def create_profile(
    dataset_id: uuid.UUID,
    force: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """Compute and persist a data profile for a dataset.

    Returns the existing profile when the dataset content (SHA) is unchanged,
    unless ``?force=true`` is passed. Older profiles are marked stale.
    """
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    if dataset.upload_status != UploadStatus.READY:
        raise DatasetNotReadyError()

    # Reuse an existing completed profile when the content SHA still matches
    if not force:
        latest = await _latest_profile(db, dataset.id)
        if (
            latest is not None
            and latest.status == ProfileStatus.COMPLETED
            and latest.dataset_sha256 == dataset.sha256
        ):
            return _to_response(latest)

    # Content changed (or forced): invalidate previous profiles
    await _mark_existing_stale(db, dataset.id)

    started_at = datetime.now(UTC)
    profile = DatasetProfile(
        dataset_id=dataset.id,
        status=ProfileStatus.PROFILING,
        dataset_sha256=dataset.sha256,
        profiling_version=PROFILING_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        started_at=started_at,
    )
    db.add(profile)
    await db.flush()

    suffix = f".{dataset.file_format}" if dataset.file_format else ".tmp"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="sf_profile_")
    os.close(fd)
    try:
        result = await anyio.to_thread.run_sync(
            _download_and_compute,
            dataset.object_key,
            tmp_path,
            dataset.file_format,
            dataset.sha256,
            dataset.sheet_name,
            dataset.csv_encoding,
        )
        overview = result["overview"]
        profile.status = ProfileStatus.COMPLETED
        profile.completed_at = datetime.now(UTC)
        profile.row_count = overview["row_count"]
        profile.column_count = overview["column_count"]
        profile.sampled = overview["sampled"]
        profile.sample_size = overview["sample_size"]
        profile.result_json = json.dumps(result, ensure_ascii=False)
        dataset.profile_status = "profiled"
        logger.info(
            "Profile computed: dataset=%s rows=%d cols=%d sampled=%s",
            dataset.id,
            profile.row_count,
            profile.column_count,
            profile.sampled,
        )
        await audit_service.record(
            db,
            event_type="profile.run",
            entity_type="dataset",
            entity_id=dataset.id,
            actor=current_user,
            project_id=dataset.project_id,
            after={
                "profile_id": str(profile.id),
                "rows": profile.row_count,
                "cols": profile.column_count,
                "sampled": profile.sampled,
            },
        )
    except Exception as exc:
        profile.status = ProfileStatus.FAILED
        profile.completed_at = datetime.now(UTC)
        profile.error_code = "PROFILE_COMPUTE_FAILED"
        profile.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        await db.flush()
        logger.exception("Profile computation failed for dataset %s", dataset.id)
        raise ProfileComputationError() from exc
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    await db.flush()
    await db.refresh(profile)
    return _to_response(profile)


@router.get(
    "/api/v1/datasets/{dataset_id}/profile",
    response_model=ProfileResponse,
)
async def get_profile(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """Get the latest profile for a dataset."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    profile = await _latest_profile(db, dataset.id)
    if profile is None:
        raise ProfileNotFoundError()
    return _to_response(profile)


@router.get(
    "/api/v1/datasets/{dataset_id}/profiles/{profile_id}",
    response_model=ProfileResponse,
)
async def get_profile_version(
    dataset_id: uuid.UUID,
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    profile = await db.get(DatasetProfile, profile_id)
    if profile is None or profile.dataset_id != dataset_id:
        raise ProfileNotFoundError()
    return _to_response(profile)


@router.get(
    "/api/v1/datasets/{dataset_id}/profile/columns",
    response_model=ProfileColumnsResponse,
)
async def get_profile_columns(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Get the per-column profiles array."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    _, result = await _require_completed_profile(db, dataset.id)
    return {"columns": result["columns"]}


@router.get(
    "/api/v1/datasets/{dataset_id}/profile/columns/{column_name}",
)
async def get_profile_column(
    dataset_id: uuid.UUID,
    column_name: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Get a single column's profile by name."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    _, result = await _require_completed_profile(db, dataset.id)
    for column in result["columns"]:
        if column["name"] == column_name:
            return column
    raise ProfileColumnNotFoundError(column_name)


@router.get(
    "/api/v1/datasets/{dataset_id}/profile/correlations",
    response_model=ProfileCorrelationsResponse,
)
async def get_profile_correlations(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Get the correlations section of the profile."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    _, result = await _require_completed_profile(db, dataset.id)
    return result["correlations"]


@router.get(
    "/api/v1/datasets/{dataset_id}/profile/missingness",
    response_model=ProfileMissingnessResponse,
)
async def get_profile_missingness(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Get the missingness section of the profile."""
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, current_user.id)
    _, result = await _require_completed_profile(db, dataset.id)
    return result["missingness"]
