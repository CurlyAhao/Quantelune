"""Freeze trusted inputs before computation; callers own the short transaction."""

import os
import uuid
from contextlib import ExitStack
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.resources import WorkerLimits
from app.models.research_execution import ResearchExecutionIntent, ResearchExecutionReceipt
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.execution_lease import ExecutionToken, validate
from app.services import research_service as ledger


async def prepare(db: AsyncSession, token: ExecutionToken) -> ResearchExecutionIntent:
    run, _scope, lease, _now = await validate(db, token)
    if lease.result_only or token.origin_fence is not None:
        raise ledger.error("EXECUTION_RECOVERY_PUBLICATION_ONLY")
    old = await db.get(ResearchExecutionIntent, token.attempt_id, populate_existing=True)
    if old is not None:
        verify_document(old, token, current_input_hash=run.input_hash)
        return old
    from app.research.plan_execution import authorize_child

    await authorize_child(db, token.owner_id, run, stage="intent")
    from app.models.research import ResearchAttempt
    from app.research import assistant_invocation

    attempt = await db.get(ResearchAttempt, token.attempt_id, populate_existing=True)
    invocation = await assistant_invocation.load(db, token.owner_id, run, attempt)
    if invocation is not None:
        await assistant_invocation.verify_current(db, token.owner_id, run, attempt, invocation)
    from app.research.execution_spec import describe

    source, config, configuration_source, operation, attached, workflow = await describe(
        db, token.owner_id, run
    )
    identity, staging = uuid.uuid4(), uuid.uuid4().hex
    if run.kind == "workflow":
        from app.research.workflow_inputs import capture

        config = await capture(db, workflow, config, token.owner_id, attached, identity)
    document = {
        "version": 1,
        "owner_id": str(token.owner_id),
        "run_id": str(token.run_id),
        "attempt_id": str(token.attempt_id),
        "input_hash": run.input_hash,
        "fence": lease.fence,
        "domain_kind": run.kind,
        "domain_id": str(identity),
        "staging_id": staging,
        "operation": operation,
        "source": {
            "dataset_id": str(source.id),
            "project_id": str(source.project_id),
            "sha256": source.sha256,
            "bytes": source.file_size,
            "object_key": source.object_key,
            "format": source.file_format,
        },
        "configuration": config,
        "configuration_source": configuration_source,
        "limits": {"wall_seconds": 300, "memory_bytes": WorkerLimits().memory_bytes},
    }
    if source.sheet_name is not None:
        document["source"]["sheet_name"] = source.sheet_name
    if source.csv_encoding is not None:
        document["source"]["csv_encoding"] = source.csv_encoding
    item = ResearchExecutionIntent(
        attempt_id=token.attempt_id,
        input_hash=run.input_hash,
        intent_hash=logical_hash(document),
        domain_id=identity,
        staging_id=staging,
        fence=lease.fence,
        document_json=canonical_bytes(document).decode(),
    )
    db.add(item)
    await db.flush()
    return item


def verify_document(
    intent: ResearchExecutionIntent, token: ExecutionToken, *, current_input_hash: str | None = None
) -> dict:
    document = parse_document(intent.document_json)
    if (
        logical_hash(document) != intent.intent_hash
        or document["owner_id"] != str(token.owner_id)
        or document["run_id"] != str(token.run_id)
        or document["attempt_id"] != str(token.attempt_id)
        or document["input_hash"] != intent.input_hash
        or document["fence"] != intent.fence
        or document["domain_id"] != str(intent.domain_id)
        or document["staging_id"] != intent.staging_id
        or intent.fence != (token.origin_fence if token.origin_fence is not None else token.fence)
        or (token.origin_fence is not None and token.fence <= token.origin_fence)
        or (current_input_hash is not None and intent.input_hash != current_input_hash)
    ):
        raise ledger.error("EXECUTION_INTENT_CHANGED")
    return document


def directory(document: dict, *, create: bool = False) -> Path:
    owner, attempt, staging = (
        uuid.UUID(document[k]).hex for k in ("owner_id", "attempt_id", "staging_id")
    )
    from app.config import settings
    from app.research.output_handles import directories, lexical, long_path

    if settings.storage_backend != "local":
        raise ledger.error("CONTENT_UNAVAILABLE")
    # Keep the same lexical/held identity while all descendants use Windows'
    # extended path API, including mkdir and the native worker's own I/O.
    storage = Path(long_path(lexical(Path(settings.local_storage_path))))
    root = storage / "research-execution"
    path = root / owner / attempt / staging
    if create:
        with ExitStack() as stack:
            parent = storage
            parent_fd = stack.enter_context(directories(parent))
            for child in (root, root / owner, root / owner / attempt, path):
                if os.name == "posix":
                    try:
                        os.mkdir(child.name, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        if child == path:
                            raise
                else:
                    child.mkdir(exist_ok=child != path)
                parent = child
                parent_fd = stack.enter_context(directories(parent))
    with directories(path):
        pass
    return path


async def accept_produced(db: AsyncSession, token: ExecutionToken) -> ResearchExecutionReceipt:
    run, _scope, _lease, _now = await validate(db, token)
    intent = await db.get(ResearchExecutionIntent, token.attempt_id, populate_existing=True)
    if intent is None or intent.input_hash != run.input_hash:
        raise ledger.error("EXECUTION_INTENT_CHANGED")
    document = verify_document(intent, token, current_input_hash=run.input_hash)
    from app.research.produced import verified

    path = directory(document)
    manifest, _response, digest = verified(
        path,
        intent.intent_hash,
        document["operation"],
        document["source"]["sha256"],
        document["configuration"],
    )
    old = await db.get(ResearchExecutionReceipt, token.attempt_id, populate_existing=True)
    if old is not None:
        if old.manifest_sha256 != digest or old.document_json != canonical_bytes(manifest).decode():
            raise ledger.error("EXECUTION_PRODUCED_CHANGED")
        return old
    receipt = ResearchExecutionReceipt(
        attempt_id=token.attempt_id,
        manifest_sha256=digest,
        result_sha256=manifest["result"]["sha256"],
        result_bytes=manifest["result"]["bytes"],
        document_json=canonical_bytes(manifest).decode(),
    )
    db.add(receipt)
    await db.flush()
    return receipt
