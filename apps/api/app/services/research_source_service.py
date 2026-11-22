"""Fresh formal source authority and bounded projection; never network or compute."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.ml.resources import WorkerLimits
from app.ml.worker_control import MLTaskScope
from app.models.dataset import Dataset, UploadStatus, Variable
from app.models.research import ResearchArtifact
from app.preview.write_fence import acquire_write_fence
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document
from app.research.dataset_metadata import preview_field
from app.research.egress import DisclosureSelection, validate_sources
from app.research.replay import verify_business
from app.services import research_service as ledger
from app.services import storage_service

MAX_SCHEMA_BYTES = 256000
MAX_SOURCES_BYTES = 128000
ALLOWED_ROLES = frozenset({"feature", "target", "group", "weight"})
NUMERIC_SEMANTICS = frozenset({"numeric_continuous", "numeric_discrete"})


def failure(code: str, status: int = 409) -> StatForgeError:
    return StatForgeError(
        status, "RESEARCH_SOURCE_" + code, "Registered source unavailable or changed."
    )


@dataclass(frozen=True)
class SourceProjection:
    sources_json: str
    binding_sha256: str

    @property
    def sources(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = parse_document(self.sources_json)
        return result


def _selection(selection: DisclosureSelection) -> DisclosureSelection:
    value = DisclosureSelection.model_validate(selection.model_dump())
    if len({item.dataset_id for item in value.datasets}) != len(value.datasets):
        raise failure("SELECTION", 422)
    if (
        (value.scope == "metadata_only" and value.rows != 0)
        or (value.scope == "sanitized_sample" and not 1 <= value.rows <= 5)
        or (
            value.scope == "raw_data_approved"
            and (not value.acknowledge_raw or not 1 <= value.rows <= 10)
        )
    ):
        raise failure("SCOPE", 422)
    return value


async def _run_binding(db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    item, scope = await ledger.run(db, run_id, owner)
    if item.state not in {State.QUEUED.value, State.RUNNING.value, State.WAITING_APPROVAL.value}:
        raise failure("RUN_INACTIVE")
    bindings = await ledger.bindings(db, owner, item.session_id, mode="live")
    captured = parse_document(item.input_json)
    if logical_hash(captured) != item.input_hash or captured["attachments"] != bindings:
        raise failure("BINDING_CHANGED")
    await verify_business(db, owner, scope.id)
    return {
        "owner_id": str(owner),
        "run_id": str(item.id),
        "workspace_id": str(scope.id),
        "input_hash": item.input_hash,
        "revision": item.revision,
        "attempt": item.attempt_count,
        "attachments": bindings,
    }


def _enum(value: Any) -> str | None:
    if value is None:
        return None
    result = value.value
    if not isinstance(result, str):
        raise failure("ROLE_UNVERIFIED", 422)
    return result


def _schema(content: str | None) -> tuple[dict[str, dict[str, Any]], str]:
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise failure("SCHEMA")
    try:
        document = parse_document(content)
        columns = document  # Registered upload/Workflow schema_json is the column array.
        if not isinstance(columns, list) or not 1 <= len(columns) <= 1000:
            raise ValueError("Invalid columns.")
        mapping: dict[str, dict[str, Any]] = {}
        for column in columns:
            if (
                not isinstance(column, dict)
                or not isinstance(column.get("name"), str)
                or not 1 <= len(column["name"]) <= 255
                or column["name"] in mapping
                or any(
                    not isinstance(column.get(field), str) or len(column[field]) > 100
                    for field in ("polars_type", "inferred_type")
                )
            ):
                raise ValueError("Invalid column metadata.")
            mapping[column["name"]] = column
        return mapping, logical_hash(document)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise failure("SCHEMA") from None


async def _dataset(
    db: AsyncSession, owner: uuid.UUID, dataset_id: uuid.UUID, columns: list[str], scope: str
) -> tuple[dict[str, Any], dict[str, Any], str, str, dict[str, str]]:
    project_id = await db.scalar(select(Dataset.project_id).where(Dataset.id == dataset_id))
    if project_id is None:
        raise failure("ACCESS_DENIED", 404)
    await ledger._project(
        db, project_id, owner
    )  # Authorize before fetching schema/sample metadata.
    with db.no_autoflush:
        row = (
            await db.execute(
                select(
                    Dataset.project_id,
                    Dataset.sha256,
                    Dataset.file_size,
                    Dataset.file_format,
                    preview_field(db, "csv_encoding"),
                    Dataset.object_key,
                    Dataset.bucket,
                    Dataset.upload_status,
                    Dataset.schema_json,
                    Dataset.row_count,
                ).where(
                    Dataset.id == dataset_id,
                    Dataset.project_id == project_id,
                    func.length(Dataset.schema_json) <= MAX_SCHEMA_BYTES,
                )
            )
        ).one_or_none()
    if row is None:
        raise failure("SCHEMA")
    await ledger._project(db, row.project_id, owner)
    if row.upload_status != UploadStatus.READY:
        raise failure("NOT_READY")
    mapping, schema_hash = _schema(row.schema_json)
    if not set(columns).issubset(mapping):
        raise failure("COLUMNS", 422)
    metadata = [
        {
            "name": name,
            "dtype": mapping[name]["polars_type"],
            "inferred_type": "numeric"
            if mapping[name]["inferred_type"] in {"integer", "float"}
            else mapping[name]["inferred_type"],
        }
        for name in columns
    ]
    with db.no_autoflush:
        variables = (
            await db.execute(
                select(
                    Variable.id,
                    Variable.name,
                    Variable.data_type,
                    Variable.semantic_type,
                    Variable.role,
                )
                .where(Variable.dataset_id == dataset_id)
                .order_by(Variable.name, Variable.id)
                .limit(1001)
            )
        ).all()
    if len(variables) > 1000:
        raise failure("ROLE_LIMIT")
    roles = [
        {
            "id": str(var.id),
            "name": var.name,
            "data_type": var.data_type,
            "semantic_type": _enum(var.semantic_type),
            "role": _enum(var.role),
        }
        for var in variables
    ]
    if scope == "sanitized_sample":
        for column in metadata:
            matches = [var for var in roles if var["name"] == column["name"]]
            if len(matches) != 1 or matches[0]["role"] not in ALLOWED_ROLES:
                raise failure("ROLE_UNVERIFIED", 422)
            semantic = matches[0]["semantic_type"]
            if not (
                (column["inferred_type"] == "numeric" and semantic in NUMERIC_SEMANTICS)
                or (column["inferred_type"] == "boolean" and semantic == "boolean")
            ):
                raise failure("SEMANTIC_UNVERIFIED", 422)
    result = {
        "kind": "dataset_version",
        "id": str(dataset_id),
        "sha256": row.sha256,
        "reported_row_count": row.row_count,
        "columns": metadata,
    }
    proof = {
        "kind": "dataset_version",
        "id": str(dataset_id),
        "project_id": str(row.project_id),
        "sha256": row.sha256,
        "bytes": row.file_size,
        "format": row.file_format,
        "schema_sha256": schema_hash,
        "roles": roles,
        "storage_binding_sha256": logical_hash({"key": row.object_key, "bucket": row.bucket}),
        "reported_row_count": row.row_count,
    }
    if row.csv_encoding is not None:
        proof["csv_encoding"] = row.csv_encoding
    return (
        result,
        proof,
        row.object_key,
        row.file_format,
        {column["name"]: column["inferred_type"] for column in metadata},
    )


async def _capture(
    db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID, selection: DisclosureSelection
) -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[str, str, dict[str, str]]]]:
    binding = await _run_binding(db, owner, run_id)
    registered = {(item["kind"], item["id"]): item for item in binding["attachments"]}
    sources, proofs, files = [], [], []
    for item in selection.datasets:
        if ("dataset_version", item.dataset_id) not in registered:
            raise failure("NOT_ATTACHED", 404)
        source, proof, key, fmt, types = await _dataset(
            db, owner, uuid.UUID(item.dataset_id), item.columns, selection.scope
        )
        if source["sha256"] != registered[("dataset_version", item.dataset_id)]["sha256"]:
            raise failure("BINDING_CHANGED")
        if selection.scope != "metadata_only" and fmt not in {"csv", "parquet"}:
            raise failure("FORMAT_UNSUPPORTED", 422)
        sources.append(source)
        proofs.append(proof)
        files.append((key, fmt, types))
    for identifier in selection.artifact_ids:
        if ("artifact", identifier) not in registered:
            raise failure("NOT_ATTACHED", 404)
        artifact = (
            await db.execute(
                select(
                    ResearchArtifact.id,
                    ResearchArtifact.sha256,
                    ResearchArtifact.kind,
                    ResearchArtifact.media_type,
                    ResearchArtifact.byte_size,
                    ResearchArtifact.logical_hash,
                ).where(ResearchArtifact.id == uuid.UUID(identifier))
            )
        ).one_or_none()
        if artifact is None or artifact.sha256 != registered[("artifact", identifier)]["sha256"]:
            raise failure("BINDING_CHANGED")
        source = {
            "kind": "artifact",
            "id": identifier,
            "sha256": artifact.sha256,
            "artifact_kind": artifact.kind,
            "media_type": artifact.media_type,
            "byte_size": artifact.byte_size,
            "logical_hash": artifact.logical_hash,
        }
        sources.append(source)
        proofs.append(source)
    proof_document = {
        "version": "research-source-v1",
        "run": binding,
        "scope": selection.scope,
        "rows": selection.rows,
        "datasets": [item.model_dump() for item in selection.datasets],
        "artifact_ids": selection.artifact_ids,
        "sources": proofs,
    }
    return proof_document, sources, files


async def project(
    db: AsyncSession, owner: uuid.UUID, run_id: uuid.UUID, selection: DisclosureSelection
) -> SourceProjection:
    selection = _selection(selection)
    await acquire_write_fence(db, owner)
    if selection.scope != "metadata_only":
        async with MLTaskScope(
            WorkerLimits(wall_seconds=45, memory_bytes=512 * 1024 * 1024)
        ) as task:
            return await _project(db, owner, run_id, selection, task)
    return await _project(db, owner, run_id, selection)


async def _project(
    db: AsyncSession,
    owner: uuid.UUID,
    run_id: uuid.UUID,
    selection: DisclosureSelection,
    task: MLTaskScope | None = None,
) -> SourceProjection:
    try:
        if task is not None:
            await task.check()
        proof, sources, files = await _capture(db, owner, run_id, selection)
        digest = logical_hash(proof)
        if selection.scope != "metadata_only":
            from app.ml.worker_control import run_ml_worker

            for index, (key, fmt, types) in enumerate(files):
                source = sources[index]
                response = await run_ml_worker(
                    str(storage_service.get_local_object_path(key)),
                    fmt,
                    {
                        "columns": [column["name"] for column in source["columns"]],
                        "rows": selection.rows,
                        "scope": selection.scope,
                        "column_types": types,
                    },
                    source["sha256"],
                    operation="egress_sample",
                    csv_encoding=proof["sources"][index].get("csv_encoding"),
                    task=task,
                )
                source.update(
                    rows=response["projection"]["rows"],
                    sample_policy=response["projection"]["sample_policy"],
                )
        # An await into the reader never makes an earlier authority check current.
        current, _, _ = await _capture(db, owner, run_id, selection)
        if logical_hash(current) != digest:
            raise failure("BINDING_CHANGED")
        if task is not None:
            await task.check()
        validate_sources(selection, sources)
        content = canonical_bytes(sources)
        if len(content) > MAX_SOURCES_BYTES:
            raise failure("OUTPUT_LIMIT")
        return SourceProjection(content.decode("utf-8"), digest)
    except (OSError, ValueError, TypeError, LookupError, RecursionError):
        # Stable errors only; parser/file repr must not disclose source content.
        raise failure("PROJECTION_UNAVAILABLE") from None
