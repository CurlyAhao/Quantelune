"""Actual saved reads and shared native request adapters; no implicit execution."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profiling import DatasetProfile, ProfileStatus
from app.research import plan_versioning
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.plan_contracts import PlanDocument
from app.research.plan_v2_contracts import PlanDocumentV2
from app.research.plan_validation import failure
from app.services import research_context_service as contexts
from app.services.profiling_service import ALGORITHM_VERSION, PROFILING_VERSION


async def read_step(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    plan: PlanDocument | PlanDocumentV2,
    step_id: str,
) -> dict:
    context = await contexts.snapshot(
        db, owner, session_id, expected_source_version=plan.source_version
    )
    current, _protocol = await plan_versioning.live(db, owner, session_id, plan, context)
    step = next((item for item in current.steps if item.id == step_id), None)
    if step is None or step.capability not in {"inspect", "profile"}:
        raise failure("READ_CAPABILITY_REFUSED")
    source = next(item for item in context.datasets if item.dataset_id == step.dataset_id)
    if step.capability == "inspect":
        return {
            "state": "read",
            "capability": "inspect",
            "dataset": source.model_dump(mode="json"),
            "citation": {
                "kind": "dataset_version",
                "id": str(source.dataset_id),
                "sha256": source.sha256,
                "schema_sha256": source.schema_sha256,
                "source_version": context.source_version,
            },
            "computed": False,
        }
    row = (
        await db.execute(
            select(
                DatasetProfile.id,
                DatasetProfile.dataset_sha256,
                DatasetProfile.algorithm_version,
                DatasetProfile.profiling_version,
                DatasetProfile.result_json,
            )
            .where(
                DatasetProfile.dataset_id == source.dataset_id,
                DatasetProfile.dataset_sha256 == source.sha256,
                DatasetProfile.status == ProfileStatus.COMPLETED,
                DatasetProfile.algorithm_version == ALGORITHM_VERSION,
                DatasetProfile.profiling_version == PROFILING_VERSION,
                func.length(DatasetProfile.result_json) <= 1024 * 1024,
            )
            .order_by(DatasetProfile.created_at.desc(), DatasetProfile.id)
            .limit(1)
        )
    ).one_or_none()
    if row is None or not row.result_json:
        return {
            "state": "unavailable",
            "capability": "profile",
            "code": "saved_current_profile_required",
            "computed": False,
        }
    try:
        result = parse_document(row.result_json)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("overview"), dict)
            or not isinstance(result.get("columns"), list)
        ):
            raise ValueError("Invalid saved profile shape.")
        projection = profile_projection(
            result,
            {item.name for item in step.fields} or {item.reference.name for item in source.fields},
        )

    except (ValueError, TypeError, KeyError):
        raise failure("PROFILE_UNPROVEN") from None
    await contexts.snapshot(db, owner, session_id, expected_source_version=plan.source_version)
    return {
        "state": "read",
        "capability": "profile",
        "facts": projection,
        "computed": False,
        "citation": {
            "kind": "saved_profile",
            "id": str(row.id),
            "dataset_id": str(source.dataset_id),
            "dataset_sha256": row.dataset_sha256,
            "algorithm_version": row.algorithm_version,
            "profiling_version": row.profiling_version,
            "result_hash": logical_hash(result),
        },
    }


def native_request(
    plan: PlanDocument | PlanDocumentV2,
    step_id: str,
    context,
    *,
    protocol_snapshot: dict | None = None,
) -> dict:
    current = plan_versioning.validate(plan, context, protocol_snapshot)
    step = next((item for item in current.steps if item.id == step_id), None)
    if step is None or step.capability not in {
        "quality",
        "descriptive",
        "correlation",
        "statistics",
    }:
        raise failure("NATIVE_CAPABILITY_REFUSED")
    # Returned request is advisory data. Only an explicit accepted-plan
    # coordinator may create and dispatch its real child Run.
    result: dict = {
        "kind": "quality" if step.capability == "quality" else "statistics",
        "parameters": {
            "operation": "execute",
            "dataset_id": str(step.dataset_id),
            "configuration": step.configuration,
        },
        "executed": False,
    }
    if isinstance(current, PlanDocumentV2):
        result["parameters"]["p4_protocol"] = plan_versioning.binding(current)
    return result


def profile_projection(result: dict, selected: set[str]) -> dict:
    """The existing bounded aggregate filter; no raw samples or new compute."""
    # Registered aggregate facts only; raw sample/top-value strings remain
    # outside this default metadata capability and Provider wire.
    overview_keys = {
        "row_count",
        "column_count",
        "total_nulls",
        "completeness",
        "duplicate_row_count",
        "duplicate_row_rate",
        "complete_row_count",
        "complete_row_rate",
        "sampled",
        "sample_size",
    }
    columns = []
    for column in result["columns"]:
        if not isinstance(column, dict) or column.get("name") not in selected:
            continue
        columns.append(
            {
                key: column[key]
                for key in (
                    "name",
                    "physical_type",
                    "inferred_type",
                    "count",
                    "null_count",
                    "null_rate",
                    "unique_count",
                    "unique_rate",
                )
                if key in column
            }
        )
    projection = {
        "overview": {
            key: value for key, value in result["overview"].items() if key in overview_keys
        },
        "columns": columns,
    }
    if len(canonical_bytes(projection)) > 512 * 1024:
        raise ValueError("Profile projection limit.")
    return projection
