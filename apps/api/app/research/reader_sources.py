"""Fresh selected result proof, without attaching or executing its source Dataset."""

from dataclasses import dataclass

from app.models.user import User
from app.research import execution_lease, reader_contracts, result_contexts
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import failure
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger


@dataclass(frozen=True)
class ReaderSourceProof:
    """Immutable bytes; current schema/facts are bound in addition to SourceVersion."""

    document_json: str
    document_hash: str


async def prove(
    db, owner, session_id, selected, source_version, *, projection_version=1
) -> ReaderSourceProof:
    reader_contracts.view_operation(projection_version)
    await execution_lease._boundary(db, owner)
    await ledger.session(db, session_id, owner, mode="live")
    actor = await db.get(User, owner, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ledger.error("ACCESS_DENIED", 404)
    selected = reader_contracts.selection(selected)
    values = await result_contexts.read(
        db, owner, session_id, selected, source_version, artifact_only=True
    )
    if not values:
        raise failure("READER_RESULT_REQUIRED")
    if projection_version == 2:
        import uuid

        from app.research import reader_saved_descriptive

        for (kind, identifier), item in values.items():
            snapshot, _ = await result_contexts.prove_result(
                db, owner, uuid.UUID(item["citations"][0]["artifact_id"])
            )
            if (snapshot.kind, snapshot.source_id) != (kind, identifier):
                raise failure("READER_SOURCE_CHANGED")
            item["facts"] = reader_saved_descriptive.augment(snapshot, item["facts"])
    # Result facts already use the original whitelist. Never include scope
    # objects, source rows, arbitrary Artifact JSON or filesystem keys here.
    results = [
        dict(
            domain_kind=kind,
            domain_run_id=str(identifier),
            facts=item["facts"],
            citations=sorted(item["citations"], key=lambda x: x["artifact_id"]),
        )
        for (kind, identifier), item in sorted(
            values.items(), key=lambda x: (x[0][0], str(x[0][1]))
        )
    ]
    document = dict(
        version="reader-source-v1" if projection_version == 1 else "reader-source-v2",
        owner_id=str(owner),
        session_id=str(session_id),
        source_version=source_version,
        selection_hash=logical_hash(selected.model_dump(mode="json")),
        results=results,
    )
    SafeSerializer().check(document)
    content = canonical_bytes(document)
    if len(content) > reader_contracts.MAX_SOURCE_BYTES:
        raise failure("READER_SOURCE_LIMIT")
    return ReaderSourceProof(content.decode("utf-8"), logical_hash(document))


def document(proof: ReaderSourceProof) -> dict:
    value = parse_document(proof.document_json)
    if logical_hash(value) != proof.document_hash:
        raise failure("READER_SOURCE_CHANGED")
    return value


async def verify(
    db, owner, session_id, selected, source_version, expected_hash, *, projection_version=1
):
    proof = await prove(
        db, owner, session_id, selected, source_version, projection_version=projection_version
    )
    if proof.document_hash != expected_hash:
        raise failure("READER_SOURCE_CHANGED")
    return proof
