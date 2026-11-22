"""Acquire a result-only epoch after both old parent and native producer died."""

from contextlib import ExitStack

from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_execution import (
    ResearchExecutionDispatch,
    ResearchExecutionIntent,
    ResearchExecutionLease,
    ResearchExecutionReceipt,
    ResearchPublicationReceipt,
)
from app.research.contracts import State, canonical_bytes
from app.research.execution_identity import WorkerIdentity, producer_dead
from app.research.execution_intent import directory, verify_document
from app.research.execution_lease import LEASE_MS, ExecutionToken, _boundary, _source
from app.research.produced import verified
from app.research.publication_handles import HeldBytes
from app.services import research_service as ledger


async def acquire(
    factory, owner, run_id, expected_revision: int, worker: WorkerIdentity
) -> ExecutionToken:
    """No timers prove death. No recovered token may create another dispatch."""
    if not worker.validate_current():
        raise ledger.error("EXECUTION_IDENTITY_UNKNOWN")
    async with factory() as db:
        run, _scope = await ledger.run(db, run_id, owner)
        attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
            )
        )
        if attempt is None:
            raise ledger.error("STATE_INCONSISTENT")
        lease = await db.get(ResearchExecutionLease, attempt.id)
        intent = await db.get(ResearchExecutionIntent, attempt.id)
        dispatch = await db.get(ResearchExecutionDispatch, attempt.id)
        if await db.get(ResearchPublicationReceipt, attempt.id) is not None:
            raise ledger.error("EXECUTION_ALREADY_PUBLISHED")
        if (
            run.revision != expected_revision
            or run.state != State.RUNNING.value
            or attempt.state != run.state
            or lease is None
            or lease.active_slot != 1
            or lease.released_at_ms is not None
            or intent is None
            or dispatch is None
            or dispatch.intent_hash != intent.intent_hash
            or dispatch.fence != intent.fence
            or lease.fence < intent.fence
        ):
            raise ledger.error("EXECUTION_RECOVERY_UNPROVEN")
        if not producer_dead(lease.producer_host, lease.producer_pid, lease.producer_birth):
            raise ledger.error("EXECUTION_PRODUCER_NOT_DEAD")
        old = (
            lease.worker_id,
            lease.fence,
            lease.producer_host,
            lease.producer_pid,
            lease.producer_birth,
        )
        origin = ExecutionToken(owner, run_id, attempt.id, lease.worker_id, intent.fence)
        document = verify_document(intent, origin, current_input_hash=run.input_hash)
        intent_hash, intent_json = intent.intent_hash, intent.document_json
        attempt_id = attempt.id
    # No write transaction or Session is held across large filesystem checks.
    stage = directory(document)
    manifest, _response, digest = verified(
        stage,
        intent_hash,
        document["operation"],
        document["source"]["sha256"],
        document["configuration"],
    )
    if not producer_dead(old[2], manifest["producer"]["pid"], manifest["producer"]["birth"]):
        raise ledger.error("EXECUTION_NATIVE_NOT_QUIESCENT")
    import hashlib
    import time
    from pathlib import Path

    from app.config import settings
    from app.research.output_handles import lexical, long_path
    from app.research.produced import read_bytes

    with ExitStack() as stack:
        held = HeldBytes(stack, time.monotonic() + 300)
        storage = Path(long_path(lexical(Path(settings.local_storage_path))))
        key = stage.relative_to(storage).as_posix()
        expected = {
            "produced.json": digest,
            "result.json": manifest["result"]["sha256"],
            "launch.json": manifest["launch_sha256"],
        }
        for item in manifest["artifacts"]:
            expected[item["name"]] = item["sha256"]
        for name, sha in expected.items():
            content = read_bytes(stage / name, 128 * 1024 * 1024)
            if hashlib.sha256(content).hexdigest() != sha:
                raise ledger.error("EXECUTION_PRODUCED_CHANGED")
            held.capture(key + "/" + name, len(content), sha)
        with held.context(collect=True):
            async with factory() as db:
                run, _scope = await ledger.run(db, run_id, owner)
                await ledger.bindings(db, owner, run.session_id)
        with held.context():
            async with factory() as db:
                now = await _boundary(db, owner)
                run, scope = await ledger.run(db, run_id, owner)
                lease = await db.get(ResearchExecutionLease, attempt_id, populate_existing=True)
                current_intent = await db.get(
                    ResearchExecutionIntent, attempt_id, populate_existing=True
                )
                current_attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
                current_dispatch = await db.get(
                    ResearchExecutionDispatch, attempt_id, populate_existing=True
                )
                if (
                    run.revision != expected_revision
                    or run.state != State.RUNNING.value
                    or current_attempt is None
                    or current_attempt.run_id != run.id
                    or current_attempt.number != run.attempt_count
                    or current_attempt.state != run.state
                    or lease is None
                    or (
                        lease.worker_id,
                        lease.fence,
                        lease.producer_host,
                        lease.producer_pid,
                        lease.producer_birth,
                    )
                    != old
                    or lease.active_slot != 1
                    or lease.released_at_ms is not None
                    or current_intent is None
                    or current_intent.document_json != intent_json
                    or current_intent.intent_hash != intent_hash
                    or current_dispatch is None
                    or current_dispatch.intent_hash != intent_hash
                    or current_dispatch.fence != document["fence"]
                    or not worker.validate_current()
                    or not producer_dead(old[2], old[3], old[4])
                    or not producer_dead(
                        old[2], manifest["producer"]["pid"], manifest["producer"]["birth"]
                    )
                ):
                    raise ledger.error("EXECUTION_RECOVERY_CHANGED")
                if (
                    verify_document(current_intent, origin, current_input_hash=run.input_hash)
                    != document
                ):
                    raise ledger.error("EXECUTION_RECOVERY_CHANGED")
                if (
                    await db.get(ResearchPublicationReceipt, attempt_id, populate_existing=True)
                    is not None
                ):
                    raise ledger.error("EXECUTION_ALREADY_PUBLISHED")
                await _source(db, owner, run)
                for name, sha in expected.items():
                    record = held.files[key + "/" + name]
                    held.verify(key + "/" + name, record[2], sha)
                receipt = await db.get(ResearchExecutionReceipt, attempt_id, populate_existing=True)
                if receipt is None:
                    db.add(
                        ResearchExecutionReceipt(
                            attempt_id=attempt_id,
                            manifest_sha256=digest,
                            result_sha256=manifest["result"]["sha256"],
                            result_bytes=manifest["result"]["bytes"],
                            document_json=canonical_bytes(manifest).decode(),
                        )
                    )
                elif (
                    receipt.manifest_sha256 != digest
                    or receipt.document_json != canonical_bytes(manifest).decode()
                    or receipt.result_sha256 != manifest["result"]["sha256"]
                    or receipt.result_bytes != manifest["result"]["bytes"]
                ):
                    raise ledger.error("EXECUTION_PRODUCED_CHANGED")
                lease.worker_id, lease.fence = worker.worker_id, old[1] + 1
                lease.producer_host, lease.producer_pid, lease.producer_birth = (
                    worker.host,
                    worker.pid,
                    worker.birth,
                )
                lease.heartbeat_at_ms, lease.lease_until_ms, lease.result_only = (
                    now,
                    now + LEASE_MS,
                    True,
                )
                await ledger._append(
                    db,
                    scope,
                    owner,
                    "run",
                    run.id,
                    "execution.result_recovered",
                    {
                        "attempt_id": str(attempt_id),
                        "origin_fence": current_intent.fence,
                        "old_fence": old[1],
                        "fence": lease.fence,
                        "old_producer_pid": old[3],
                        "old_producer_birth": old[4],
                        "native_pid": manifest["producer"]["pid"],
                        "native_birth": manifest["producer"]["birth"],
                        "manifest_sha256": digest,
                    },
                )
                token = ExecutionToken(
                    owner, run_id, attempt_id, worker.worker_id, lease.fence, current_intent.fence
                )
                await db.commit()
                return token
