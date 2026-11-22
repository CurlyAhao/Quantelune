"""Fresh, bounded local metadata with a separate reference for every field."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset
from app.models.research import ResearchArtifact, ResearchAttachment, ResearchEvent
from app.models.user import User
from app.preview.write_fence import acquire_write_fence
from app.research.context_contracts import (
    ArtifactContext,
    ContextField,
    ContextSnapshot,
    DatasetContext,
    FieldReference,
)
from app.research.contracts import canonical_bytes, logical_hash
from app.services import research_service as ledger
from app.services import research_source_service as sources

MAX_CONTEXT_BYTES = 512 * 1024


async def snapshot(
    db: AsyncSession,
    owner: uuid.UUID,
    session_id: uuid.UUID,
    *,
    expected_source_version: str | None = None,
) -> ContextSnapshot:
    await acquire_write_fence(db, owner)
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    conversation, scope = await ledger.session(db, session_id, owner, mode="live")
    if scope.event_sequence > 10000:
        raise ledger.error("VERIFY_LIMIT")
    await ledger.verify_events(db, owner, scope.id)
    bindings = await ledger.bindings(db, owner, session_id, mode="live")
    # Include revoked rows too: reactivation of the same target must never
    # revive a source authorization captured before its revocation.
    attachment_revision = (
        await db.scalar(
            select(func.max(ResearchEvent.sequence)).where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_kind == "attachment",
                ResearchEvent.event_type.in_(["attachment.added", "attachment.revoked"]),
                ResearchEvent.entity_id.in_(
                    select(ResearchAttachment.id).where(ResearchAttachment.session_id == session_id)
                ),
            )
        )
        or 0
    )
    dataset_bindings = [item for item in bindings if item["kind"] == "dataset_version"]
    artifact_bindings = [item for item in bindings if item["kind"] == "artifact"]
    if len(dataset_bindings) > 5 or len(artifact_bindings) > 5:
        raise sources.failure("CONTEXT_LIMIT", 422)
    datasets, artifacts = [], []
    for binding in dataset_bindings:
        identifier = uuid.UUID(binding["id"])
        content = await db.scalar(
            select(Dataset.schema_json).where(
                Dataset.id == identifier,
                func.length(Dataset.schema_json) <= sources.MAX_SCHEMA_BYTES,
            )
        )
        mapping, schema_hash = sources._schema(content)
        metadata, proof, _key, _format, _types = await sources._dataset(
            db, owner, identifier, list(mapping), "metadata_only"
        )
        if metadata["sha256"] != binding["sha256"] or proof["schema_sha256"] != schema_hash:
            raise sources.failure("BINDING_CHANGED")
        fields = []
        for column in metadata["columns"]:
            roles = [item for item in proof["roles"] if item["name"] == column["name"]]
            if len(roles) > 1:
                raise sources.failure("ROLE_UNVERIFIED")
            role = roles[0] if roles else None
            fields.append(
                ContextField(
                    reference=FieldReference(
                        dataset_id=identifier,
                        dataset_sha256=metadata["sha256"],
                        schema_sha256=schema_hash,
                        name=column["name"],
                        variable_id=uuid.UUID(role["id"]) if role else None,
                    ),
                    dtype=column["dtype"],
                    inferred_type=column["inferred_type"],
                    role=role["role"] if role else None,
                    semantic_type=role["semantic_type"] if role else None,
                )
            )
        datasets.append(
            DatasetContext(
                dataset_id=identifier,
                project_id=uuid.UUID(proof["project_id"]),
                sha256=metadata["sha256"],
                schema_sha256=schema_hash,
                reported_row_count=metadata["reported_row_count"],
                fields=fields,
            )
        )
    for binding in artifact_bindings:
        item = await db.get(ResearchArtifact, uuid.UUID(binding["id"]), populate_existing=True)
        if item is None or item.sha256 != binding["sha256"]:
            raise sources.failure("BINDING_CHANGED")
        artifacts.append(
            ArtifactContext(
                artifact_id=item.id,
                run_id=item.run_id,
                project_id=item.project_id,
                sha256=item.sha256,
                logical_hash=item.logical_hash,
                kind=item.kind,
                byte_size=item.byte_size,
            )
        )
    document = {
        "version": "research-context-v1",
        "workspace_id": str(scope.id),
        "session_id": str(conversation.id),
        "workspace_revision": scope.revision,
        "workspace_epoch": scope.lifecycle_epoch,
        "session_revision": conversation.revision,
        "session_epoch": conversation.lifecycle_epoch,
        "attachment_revision": attachment_revision,
        "datasets": [item.model_dump(mode="json") for item in datasets],
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
    }
    # Include all exact attachments (including explicit project origins) in the
    # signature without implicitly expanding their datasets into this context.
    digest = logical_hash({"context": document, "attachments": bindings})
    if expected_source_version is not None and digest != expected_source_version:
        raise sources.failure("CONTEXT_CHANGED")
    result = ContextSnapshot.model_validate(dict(document, source_version=digest))
    if len(canonical_bytes(result.model_dump(mode="json"))) > MAX_CONTEXT_BYTES:
        raise sources.failure("CONTEXT_LIMIT", 422)
    return result
