"""Durable plan before files, then actual domain/Artifact/terminal commit."""

import asyncio
import hashlib
import time
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.research import ResearchArtifact, ResearchArtifactEdge, ResearchAttempt
from app.models.research_execution import (
    ResearchExecutionIntent,
    ResearchExecutionReceipt,
    ResearchPublicationIntent,
    ResearchPublicationReceipt,
)
from app.models.user import User
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document
from app.research.execution_intent import directory, verify_document
from app.research.execution_lease import ExecutionToken, heartbeat, validate
from app.research.produced import read_bytes, verified
from app.research.publication_files import copy_registered, targets, write_root
from app.research.publication_handles import HeldBytes
from app.research.publication_proof import binding, prove, snapshot
from app.research.publication_records import persist
from app.services import research_service as ledger


async def _capture(factory, token):
    # Read authorized metadata without a write fence, then verify potentially
    # large native bytes with no DB Session held open.
    async with factory() as db:
        run, _scope = await ledger.run(db, token.run_id, token.owner_id)
        intent = await db.get(ResearchExecutionIntent, token.attempt_id)
        receipt = await db.get(ResearchExecutionReceipt, token.attempt_id)
        if intent is None or receipt is None:
            raise ledger.error("PRODUCED_UNPROVEN")
        document = verify_document(intent, token, current_input_hash=run.input_hash)
        expected = (
            intent.intent_hash,
            receipt.manifest_sha256,
            receipt.document_json,
            receipt.result_sha256,
        )
    stage = directory(document)
    manifest, response, digest = verified(
        stage,
        expected[0],
        document["operation"],
        document["source"]["sha256"],
        document["configuration"],
    )
    payload = read_bytes(stage / "result.json", 16 * 1024 * 1024)
    if (
        digest != expected[1]
        or canonical_bytes(manifest).decode() != expected[2]
        or hashlib.sha256(payload).hexdigest() != expected[3]
    ):
        raise ledger.error("PRODUCED_CHANGED")
    return document, manifest, response, digest, stage, payload


async def _production(db, token, frozen=None):
    run, scope, lease, now = await validate(db, token)
    if lease.pause_requested:
        raise ledger.error("PUBLICATION_PAUSE_REQUESTED")
    intent = await db.get(ResearchExecutionIntent, token.attempt_id, populate_existing=True)
    receipt = await db.get(ResearchExecutionReceipt, token.attempt_id, populate_existing=True)
    if intent is None or receipt is None:
        raise ledger.error("PRODUCED_UNPROVEN")
    document = verify_document(intent, token, current_input_hash=run.input_hash)
    if frozen is None:
        raise ledger.error("PUBLICATION_PREVERIFICATION_REQUIRED")
    captured, manifest, response, digest, stage, _payload = frozen
    if document != captured:
        raise ledger.error("PRODUCED_CHANGED")
    if (
        digest != receipt.manifest_sha256
        or canonical_bytes(manifest).decode() != receipt.document_json
    ):
        raise ledger.error("PRODUCED_CHANGED")
    from app.research.credential_liveness import process_identity

    status, birth = process_identity(manifest["producer"]["pid"])
    if status != "dead" and not (
        status == "alive" and birth is not None and birth != manifest["producer"]["birth"]
    ):
        raise ledger.error("PRODUCER_NOT_QUIESCENT")
    from app.models.dataset import Dataset
    from app.models.ml import MLExperiment
    from app.models.workflow import Workflow

    selected = (
        document["configuration"]["sources"]
        if run.kind == "workflow"
        else {document["source"]["dataset_id"]: document["source"]}
    )
    for identity, expected in selected.items():
        row = (
            await db.execute(
                select(
                    Dataset.project_id,
                    Dataset.sha256,
                    Dataset.object_key,
                    Dataset.file_format,
                    Dataset.file_size,
                ).where(Dataset.id == uuid.UUID(identity))
            )
        ).one_or_none()
        if row is None or (
            str(row.project_id),
            row.sha256,
            row.object_key,
            row.file_format,
            row.file_size,
        ) != (
            expected["project_id"],
            expected["sha256"],
            expected["object_key"],
            expected["format"],
            expected["bytes"],
        ):
            raise ledger.error("BINDING_CHANGED")
    origin = document["configuration_source"]
    if origin is not None:
        if run.kind == "ml":
            row = (
                await db.execute(
                    select(
                        MLExperiment.project_id, MLExperiment.dataset_id, MLExperiment.config_json
                    ).where(MLExperiment.id == uuid.UUID(origin["id"]))
                )
            ).one_or_none()
            if row is None or logical_hash(parse_document(row.config_json)) != origin["hash"]:
                raise ledger.error("BINDING_CHANGED")
        else:
            row = (
                await db.execute(
                    select(Workflow.project_id, Workflow.dataset_id).where(
                        Workflow.id == uuid.UUID(origin["id"])
                    )
                )
            ).one_or_none()
        if (
            row is None
            or str(row.project_id) != document["source"]["project_id"]
            or str(row.dataset_id) != document["source"]["dataset_id"]
        ):
            raise ledger.error("BINDING_CHANGED")
    return run, scope, lease, now, intent, receipt, document, manifest, response, stage


async def _plan(factory, token, frozen=None):
    frozen = frozen or await _capture(factory, token)
    async with factory() as db:
        (
            run,
            _scope,
            _lease,
            _now,
            intent,
            receipt,
            document,
            manifest,
            _response,
            stage,
        ) = await _production(db, token, frozen)
        old = await db.get(ResearchPublicationIntent, token.attempt_id, populate_existing=True)
        if old is not None:
            attempt = await db.get(ResearchAttempt, token.attempt_id)
            _i, _r, _p, _d, plan, _response = await binding(db, token.owner_id, run, attempt)
        else:
            payload = frozen[5]
            if hashlib.sha256(payload).hexdigest() != receipt.result_sha256:
                raise ledger.error("PRODUCED_CHANGED")
            root = uuid.uuid5(intent.domain_id, "research:domain-result")
            plan = {
                "version": 1,
                "owner_id": str(token.owner_id),
                "run_id": str(token.run_id),
                "attempt_id": str(token.attempt_id),
                "input_hash": run.input_hash,
                "intent_hash": intent.intent_hash,
                "manifest_sha256": receipt.manifest_sha256,
                "domain_id": str(intent.domain_id),
                "domain_kind": run.kind,
                "root_id": str(root),
                "recorded_at": datetime.now(UTC).isoformat(),
                "files": targets(document, _response, manifest),
            }
            db.add(
                ResearchPublicationIntent(
                    attempt_id=token.attempt_id,
                    plan_hash=logical_hash(plan),
                    manifest_sha256=receipt.manifest_sha256,
                    root_id=root,
                    document_json=canonical_bytes(plan).decode(),
                    result_json=payload.decode(),
                )
            )
        await heartbeat(db, token)
        await db.commit()  # No formal storage write occurs before this succeeds.
        return plan, stage


async def _confirmed(factory, token):
    async with factory() as db:
        owner = await db.get(User, token.owner_id)
        if owner is None or not owner.is_active:
            raise ledger.error("ACCESS_DENIED", 404)
        run, _scope = await ledger.run(db, token.run_id, token.owner_id)
        attempt = await db.get(ResearchAttempt, token.attempt_id)
        receipt = await db.get(ResearchPublicationReceipt, token.attempt_id)
        if receipt is None:
            return None
        if (
            attempt is None
            or attempt.run_id != run.id
            or attempt.number != run.attempt_count
            or run.state != receipt.state
            or attempt.state != receipt.state
        ):
            raise ledger.error("PUBLICATION_STATE_CHANGED")
        actual = await snapshot(db, token.owner_id, run, attempt)
        await prove(db, token.owner_id, run, attempt, actual)
        if run.state == State.COMPLETED.value:
            if await ledger._prove_completion(db, token.owner_id, run) != run.completion_hash:
                raise ledger.error("SOURCE_UNPROVEN")
        else:
            await _verify_artifacts(db, token.owner_id, run, attempt, actual)
        return {
            "run_id": str(run.id),
            "attempt_id": str(attempt.id),
            "state": run.state,
            "domain_id": str(run.domain_run_id),
            "snapshot_hash": actual.logical_hash,
            "root_id": str(receipt.root_id),
        }


async def _verify_artifacts(db, owner, run, attempt, actual):
    from app.research.domain_results import verify_artifact

    receipt = await db.get(ResearchPublicationReceipt, attempt.id)
    root = await db.get(ResearchArtifact, receipt.root_id)
    if root is None or root.run_id != run.id:
        raise ledger.error("SOURCE_UNPROVEN")
    verify_artifact(root, actual, owner)
    children = (
        await db.scalars(
            select(ResearchArtifact)
            .join(ResearchArtifactEdge, ResearchArtifactEdge.child_id == ResearchArtifact.id)
            .where(
                ResearchArtifactEdge.parent_id == root.id,
                ResearchArtifactEdge.relation == "saved_output",
            )
        )
    ).all()
    if len(children) != len(actual.outputs) or {child.object_key for child in children} != {
        output.object_key for output in actual.outputs
    }:
        raise ledger.error("SOURCE_UNPROVEN")
    for child in children:
        verify_artifact(child, actual, owner)


async def _register(db, run, attempt, actual, plan, root_bytes):
    from app.research.domain_results import verify_artifact

    provenance = {
        "domain_kind": actual.kind,
        "domain_run_id": str(actual.source_id),
        "snapshot_hash": actual.logical_hash,
        "datasets": list(actual.datasets),
        "operation": "execute",
        "attempt_number": attempt.number,
    }
    key, data, digest = root_bytes
    if data != canonical_bytes(actual.document):
        raise ledger.error("PUBLICATION_RECORD_CHANGED")
    ledger.verify_local_bytes(key, len(data), digest)
    root_id = uuid.UUID(plan["root_id"])
    root = ResearchArtifact(
        id=root_id,
        run_id=run.id,
        project_id=actual.project_id,
        kind="domain_result",
        media_type="application/json",
        byte_size=len(data),
        sha256=digest,
        logical_hash=actual.logical_hash,
        object_key=key,
        provenance_json=canonical_bytes(provenance).decode(),
        created_at=datetime.fromisoformat(plan["recorded_at"]),
    )
    db.add(root)
    await db.flush()
    verify_artifact(root, actual, uuid.UUID(plan["owner_id"]))
    for output in actual.outputs:
        child_id = uuid.uuid5(root_id, output.object_key)
        child = ResearchArtifact(
            id=child_id,
            run_id=run.id,
            project_id=actual.project_id,
            kind=output.kind,
            media_type=output.media_type,
            byte_size=output.byte_size,
            sha256=output.sha256,
            logical_hash=logical_hash(output.semantic()),
            object_key=output.object_key,
            provenance_json=canonical_bytes(provenance).decode(),
            created_at=datetime.fromisoformat(plan["recorded_at"]),
        )
        db.add(child)
        await db.flush()
        verify_artifact(child, actual, uuid.UUID(plan["owner_id"]))
        db.add(
            ResearchArtifactEdge(
                id=uuid.uuid5(root_id, "edge:" + output.object_key),
                parent_id=root_id,
                child_id=child_id,
                relation="saved_output",
            )
        )


async def publish(factory, token: ExecutionToken) -> dict:
    """Server supervisor entry. HTTP assertions and SDK state cannot call complete."""
    confirmed = await _confirmed(factory, token)
    if confirmed is not None:
        return confirmed
    deadline = time.monotonic() + 300
    frozen = await _capture(factory, token)
    with ExitStack() as stack:
        held = HeldBytes(stack, deadline)
        stage = frozen[4]
        from pathlib import Path

        from app.config import settings
        from app.research.output_handles import lexical, long_path

        storage = Path(long_path(lexical(Path(settings.local_storage_path))))
        stage_key = stage.relative_to(storage).as_posix()
        for name in ("produced.json", "result.json", "launch.json"):
            content = read_bytes(stage / name, 16 * 1024 * 1024)
            expected_digest = (
                frozen[3]
                if name == "produced.json"
                else hashlib.sha256(frozen[5]).hexdigest()
                if name == "result.json"
                else frozen[1]["launch_sha256"]
            )
            held.capture(stage_key + "/" + name, len(content), expected_digest)
        with held.context(collect=True):
            async with factory() as db:
                run, _scope = await ledger.run(db, token.run_id, token.owner_id)
                await ledger.bindings(db, token.owner_id, run.session_id)
        from app.research.publication_heartbeat import publisher_scope

        with publisher_scope(token, held):
            return await _publish_held(factory, token, frozen, held)


async def _publish_held(factory, token, frozen, held):
    with held.context():
        plan, stage = await _plan(factory, token, frozen)
    previous = time.monotonic()
    for item in plan["files"]:
        held.check_time()
        copy_registered(stage, [item])
        await asyncio.sleep(0)
        if time.monotonic() - previous >= 3:
            with held.context():
                async with factory() as db:
                    await heartbeat(db, token)
                    await db.commit()
            previous = time.monotonic()
    for item in plan["files"]:
        held.capture(item["object_key"], item["bytes"], item["sha256"])
    with held.context():
        # Generate a snapshot from actual flushed domain rows, rollback
        # the rehearsal, then fsync root bytes outside any write Session.
        async with factory() as db:
            (
                run,
                _scope,
                _lease,
                _now,
                _i,
                _r,
                document,
                _m,
                response,
                _stage,
            ) = await _production(db, token, frozen)
            attempt = await db.get(ResearchAttempt, token.attempt_id)
            await persist(db, document, response, plan, await db.get(User, token.owner_id))
            run.domain_kind, run.domain_run_id = run.kind, uuid.UUID(document["domain_id"])
            attempt.domain_kind, attempt.domain_run_id = run.domain_kind, run.domain_run_id
            await db.flush()
            prepared = await snapshot(db, token.owner_id, run, attempt)
            await db.rollback()
    root_bytes = write_root(plan, prepared.document)
    held.capture(root_bytes[0], len(root_bytes[1]), root_bytes[2])
    with held.context():
        return await _commit(factory, token, frozen, plan, root_bytes)


async def _commit(factory, token, frozen, plan, root_bytes):
    final_deadline = time.monotonic() + 5
    async with factory() as db:
        run, scope, lease, now, _i, produced, document, _m, response, _stage = await _production(
            db, token, frozen
        )
        attempt = await db.get(ResearchAttempt, token.attempt_id)
        _i, _r, publication, _d, current_plan, _response = await binding(
            db, token.owner_id, run, attempt
        )
        if current_plan != plan:
            raise ledger.error("PUBLICATION_PLAN_CHANGED")
        owner = await db.get(User, token.owner_id)
        await persist(db, document, response, plan, owner)
        run.domain_kind, run.domain_run_id = run.kind, uuid.UUID(document["domain_id"])
        attempt.domain_kind, attempt.domain_run_id = run.domain_kind, run.domain_run_id
        await db.flush()
        actual = await snapshot(db, token.owner_id, run, attempt)
        await _register(db, run, attempt, actual, plan, root_bytes)
        db.add(
            ResearchPublicationReceipt(
                attempt_id=attempt.id,
                plan_hash=publication.plan_hash,
                manifest_sha256=produced.manifest_sha256,
                snapshot_hash=actual.logical_hash,
                root_id=publication.root_id,
                publication_fence=token.fence,
                state=actual.state.value,
            )
        )
        await db.flush()
        run.completion_hash = actual.logical_hash
        await ledger._append(
            db,
            scope,
            token.owner_id,
            "artifact",
            publication.root_id,
            "artifact.registered",
            {
                "run_id": str(run.id),
                "sha256": root_bytes[2],
                "domain_kind": run.kind,
                "domain_run_id": str(run.domain_run_id),
                "logical_hash": actual.logical_hash,
                "output_sha256": [item.sha256 for item in actual.outputs],
            },
        )
        await ledger.transition(db, token.owner_id, run.id, run.revision, actual.state)
        _run, _scope, lease, now = await validate(db, token, running=False)
        lease.active_slot, lease.released_at_ms = None, now
        await db.flush()
        if time.monotonic() >= final_deadline:
            raise ledger.error("PUBLICATION_TIMEOUT", 504)
        commit = asyncio.create_task(db.commit())
        while not commit.done():
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                continue  # Repeated cancellation must never close a live commit.
            except Exception:
                break  # Actual terminal failure is confirmed from another Session.
        # Consume a failed task's exception; never trust the same connection's
        # acknowledgement as authoritative business success.
        try:
            commit.result()
        except BaseException:
            pass
    confirmed = await _confirmed(factory, token)
    if confirmed is None:
        raise ledger.error("PUBLICATION_UNKNOWN", 503)
    return confirmed
