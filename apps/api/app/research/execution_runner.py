"""Run a committed intention outside HTTP/DB transactions, retaining real output."""

import time

from sqlalchemy import select

from app.ml.resources import WorkerLimits
from app.ml.worker_control import run_ml_worker
from app.models.research import ResearchRun
from app.models.research_execution import (
    ResearchExecutionDispatch,
    ResearchExecutionIntent,
    ResearchExecutionReceipt,
)
from app.research.contracts import State
from app.research.execution_intent import accept_produced, directory, verify_document
from app.research.execution_lease import ExecutionToken, heartbeat, validate
from app.services import research_service as ledger
from app.services import storage_service


async def produce(factory, token: ExecutionToken, *, on_dispatch_committed=None) -> dict:
    # This new Session sees only committed intent. The caller must have closed
    # its claim/intent transaction before work or this boundary safely refuses.
    async with factory() as db:
        run, _scope, _lease, now = await validate(db, token)
        if _lease.result_only or token.origin_fence is not None:
            raise ledger.error("EXECUTION_RECOVERY_PUBLICATION_ONLY")
        if _lease.pause_requested:
            raise ledger.error("EXECUTION_PAUSE_REQUESTED")
        intent = await db.get(ResearchExecutionIntent, token.attempt_id)
        if intent is None:
            raise ledger.error("EXECUTION_INTENT_UNCOMMITTED")
        document = verify_document(intent, token, current_input_hash=run.input_hash)
        digest = intent.intent_hash
        receipt = await db.get(ResearchExecutionReceipt, token.attempt_id)
        dispatch = await db.get(ResearchExecutionDispatch, token.attempt_id)
        prior_dispatch = dispatch is not None
        if dispatch is not None and (
            dispatch.intent_hash != digest or dispatch.fence != token.fence
        ):
            raise ledger.error("EXECUTION_INTENT_CHANGED")
        if dispatch is None:
            # First dispatch linearizes under validate's actor fence. A
            # claimed token permits existing work to close, not a new launch
            # after its conversation has been archived or deleted.
            await ledger.session(db, run.session_id, token.owner_id, mode="live")
            from app.research.plan_execution import authorize_child

            await authorize_child(db, token.owner_id, run, stage="dispatch")
            if receipt is not None:
                raise ledger.error("EXECUTION_PRODUCED_UNKNOWN")
            db.add(
                ResearchExecutionDispatch(
                    attempt_id=token.attempt_id,
                    intent_hash=digest,
                    fence=token.fence,
                    registered_at_ms=now,
                )
            )
        await db.commit()
    if on_dispatch_committed is not None:
        on_dispatch_committed()
    path = None
    try:
        path = directory(document)
    except FileNotFoundError:
        if prior_dispatch or receipt is not None:
            raise ledger.error("EXECUTION_PRODUCED_UNKNOWN") from None
        path = directory(document, create=True)
    # Actual durable result wins over any repeat dispatch. Missing/invalid
    # proof never causes deletion and a second fitting operation.
    if any(path.iterdir()):
        from app.research.produced import verify

        _manifest, response = verify(
            path,
            digest,
            document["operation"],
            document["source"]["sha256"],
            document["configuration"],
        )
    else:
        if prior_dispatch or receipt is not None:
            raise ledger.error("EXECUTION_PRODUCED_UNKNOWN")
        previous = time.monotonic()

        async def cancelled():
            nonlocal previous
            async with factory() as db:
                state = await db.scalar(
                    select(ResearchRun.state).where(ResearchRun.id == token.run_id)
                )
                if state != State.RUNNING.value:
                    return True
                if time.monotonic() - previous >= 3:
                    try:
                        await heartbeat(db, token)
                        await db.commit()
                    except Exception:
                        return True
                    previous = time.monotonic()
            return False

        source = document["source"]
        workflow_sources = None
        if document["operation"] == "research_workflow":
            workflow_sources = {
                identity: str(storage_service.get_local_object_path(item["object_key"]))
                for identity, item in document["configuration"]["sources"].items()
            }
        response = await run_ml_worker(
            str(storage_service.get_local_object_path(source["object_key"])),
            source["format"],
            document["configuration"],
            source["sha256"],
            operation=document["operation"],
            limits=WorkerLimits(**document["limits"]),
            cancel_check=cancelled,
            produced_directory=path,
            workflow_sources=workflow_sources,
            intent_hash=digest,
            sheet_name=source.get("sheet_name"),
            csv_encoding=source.get("csv_encoding"),
        )
    async with factory() as db:
        await accept_produced(db, token)
        await db.commit()
    # Business publication is separate and must prove domain/Artifact identity.
    return response
