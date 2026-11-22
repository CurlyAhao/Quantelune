"""Verify the exact Harness event/row relation without executing SDK or transport."""

import uuid
from typing import Any

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent, ResearchRun
from app.models.research_harness import ResearchAdvisoryReceipt, ResearchHarnessThread
from app.models.research_planning import ResearchPlannerThread
from app.models.research_security import ResearchApproval, ResearchCharge
from app.research import (
    assistant_invocation,
    harness_binding,
    model_request,
    planner_invocation,
    reader_admission,
    reviewer_admission,
)
from app.research.contracts import logical_hash, parse_document
from app.research.harness_serde import SafeSerializer

KINDS = frozenset(
    {
        "harness.thread_bound",
        "harness.model_request",
        "security.reply_recorded",
        "security.reply_resumed",
        assistant_invocation.EVENT,
        planner_invocation.EVENT,
        reader_admission.EVENT,
        reviewer_admission.EVENT,
        "harness.wait_control",
        "harness.assistant_recovered",
        "harness.tool_result",
        "harness.assistant_finished",
        "harness.sdk_finished",
    }
)


async def verify(db, owner, runs, events):
    threads: dict[tuple[str, str], dict[str, Any]] = {}
    requests: dict[uuid.UUID, dict[str, Any]] = {}
    recorded: dict[uuid.UUID, dict[str, Any]] = {}
    resumed: dict[uuid.UUID, dict[str, Any]] = {}
    invocations: dict[uuid.UUID, dict[str, Any]] = {}
    controls: dict[uuid.UUID, dict[str, Any]] = {}
    tool_results = []
    finishes = {}
    recoveries: dict[uuid.UUID, dict[str, Any]] = {}
    for event in events:
        payload = parse_document(event.payload_json)
        identifier = event.entity_id
        if event.event_type in {"harness.sdk_finished", "harness.assistant_finished"}:
            payload_hash = payload["binding_hash"]
            if event.entity_kind != "run" or payload_hash in finishes:
                raise ValueError("Harness finish event route.")
            finishes[payload_hash] = (event, payload)
        elif event.event_type == "harness.tool_result":
            if event.entity_kind != "harness_tool":
                raise ValueError("Tool event route.")
            tool_results.append((event, payload))
        elif event.event_type == "harness.assistant_recovered":
            if event.entity_kind != "run" or set(payload) != {
                "attempt_id",
                "old_fence",
                "fence",
                "old_worker_id",
                "old_producer_host",
                "old_producer_pid",
                "old_producer_birth",
                "worker_id",
                "producer_host",
                "producer_pid",
                "producer_birth",
            }:
                raise ValueError("Assistant recovery event route.")
            attempt_id = uuid.UUID(payload["attempt_id"])
            attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
            previous = recoveries.get(attempt_id)
            if (
                attempt is None
                or attempt.run_id != identifier
                or identifier not in runs
                or type(payload["old_fence"]) is not int
                or payload["old_fence"] <= 0
                or type(payload["fence"]) is not int
                or payload["fence"] != payload["old_fence"] + 1
                or payload["producer_host"] != payload["old_producer_host"]
                or (previous is not None and payload["old_fence"] != previous["fence"])
            ):
                raise ValueError("Assistant recovery event mismatch.")
            recoveries[attempt_id] = payload
        elif event.event_type == "harness.wait_control":
            if event.entity_kind != "run" or set(payload) != {"attempt_id", "revision", "action"}:
                raise ValueError("Wait control event route.")
            attempt_id = uuid.UUID(payload["attempt_id"])
            attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
            if (
                attempt is None
                or attempt.run_id != identifier
                or identifier not in runs
                or payload["action"] not in {"pause", "resume"}
                or type(payload["revision"]) is not int
                or payload["revision"] < 0
                or payload["revision"] > runs[identifier]["revision"]
            ):
                raise ValueError("Wait control event mismatch.")
            controls[attempt_id] = payload
        elif event.event_type in {
            assistant_invocation.EVENT,
            planner_invocation.EVENT,
            reader_admission.EVENT,
            reviewer_admission.EVENT,
        }:
            expected_kind = (
                "reviewer_invocation"
                if event.event_type == reviewer_admission.EVENT
                else "reader_invocation"
                if event.event_type == reader_admission.EVENT
                else "planner_invocation"
                if event.event_type == planner_invocation.EVENT
                else "harness_invocation"
            )
            if event.entity_kind != expected_kind or identifier in invocations:
                raise ValueError("Invocation event route or duplicate.")
            invocations[identifier] = payload
        elif event.event_type == "harness.thread_bound":
            if event.entity_kind != "run" or uuid.UUID(payload["run_id"]) != identifier:
                raise ValueError("Harness thread event route.")
            key = (payload["attempt_id"], payload["purpose"])
            if key in threads:
                raise ValueError("Duplicate Harness thread.")
            threads[key] = payload
        else:
            target, kind = {
                "harness.model_request": (requests, "model_request"),
                "security.reply_recorded": (recorded, "security_reply"),
                "security.reply_resumed": (resumed, "security_reply"),
            }[event.event_type]
            if event.entity_kind != kind or identifier in target:
                raise ValueError("Harness event route or duplicate.")
            target[identifier] = payload
    for identifier, expected in invocations.items():
        attempt = await db.get(ResearchAttempt, identifier, populate_existing=True)
        if attempt is None or attempt.run_id not in runs:
            raise ValueError("Invocation attempt missing.")
        from app.services import research_service as ledger

        run, _ = await ledger.run(db, attempt.run_id, owner)
        if run.kind == "reader":
            actual = await reader_admission.load(db, owner, run, attempt)
        elif run.kind == "reviewer":
            actual = await reviewer_admission.load(db, owner, run, attempt)
        elif run.kind == "planner":
            actual = await planner_invocation.load(db, owner, run, attempt.id)
        else:
            actual = await assistant_invocation.load(db, owner, run, attempt)
        if actual != expected:
            raise ValueError("Invocation mismatch.")
    for identifier, expected in recoveries.items():
        from app.models.research_execution import ResearchExecutionLease

        lease = await db.get(ResearchExecutionLease, identifier, populate_existing=True)
        if identifier not in invocations or lease is None or lease.fence < expected["fence"]:
            raise ValueError("Assistant recovery lease mismatch.")
        if lease.fence == expected["fence"] and any(
            getattr(lease, key) != expected[key]
            for key in ("worker_id", "producer_host", "producer_pid", "producer_birth")
        ):
            raise ValueError("Assistant recovery producer mismatch.")
    for identifier, expected in controls.items():
        from app.models.research_execution import ResearchExecutionLease

        attempt = await db.get(ResearchAttempt, identifier, populate_existing=True)
        lease = await db.get(ResearchExecutionLease, identifier, populate_existing=True)
        if identifier not in invocations or lease is None:
            raise ValueError("Wait control invocation missing.")
        if runs[attempt.run_id]["state"] == "waiting_approval" and (
            lease.pause_requested != (expected["action"] == "pause")
        ):
            raise ValueError("Wait control state mismatch.")
    rows = (
        await db.scalars(
            select(ResearchHarnessThread).where(ResearchHarnessThread.run_id.in_(runs)),
            execution_options={"populate_existing": True},
        )
    ).all()
    rows = list(rows) + list(
        (
            await db.scalars(
                select(ResearchPlannerThread).where(ResearchPlannerThread.run_id.in_(runs)),
                execution_options={"populate_existing": True},
            )
        ).all()
    )
    if {(str(row.attempt_id), row.purpose) for row in rows} != set(threads):
        raise ValueError("Harness thread set mismatch.")
    for row in rows:
        attempt = await db.get(ResearchAttempt, row.attempt_id, populate_existing=True)
        profile_run = await db.get(ResearchRun, row.run_id, populate_existing=True)
        expected = harness_binding.document(row)
        if (
            row.owner_id != owner
            or row.run_id not in runs
            or attempt is None
            or profile_run is None
            or attempt.run_id != row.run_id
            or row.input_hash != runs[row.run_id]["input_hash"]
            or row.purpose not in harness_binding.PROFILES
            or row.profile_hash
            != logical_hash(
                harness_binding.profile(
                    row.purpose,
                    runs[row.run_id]["kind"],
                    parse_document(profile_run.input_json)["parameters"],
                )
            )
            or row.binding_hash != logical_hash(expected)
            or uuid.UUID(row.thread_id).hex != row.thread_id
            or threads[(str(row.attempt_id), row.purpose)] != expected
        ):
            raise ValueError("Harness thread mismatch.")
        if runs[row.run_id]["kind"] == "reader":
            from app.research import reader_sdk_finish

            sdk_events = (
                await db.scalars(
                    select(ResearchEvent)
                    .where(
                        ResearchEvent.entity_id == row.id,
                        ResearchEvent.event_type == reader_sdk_finish.EVENT,
                    )
                    .limit(2)
                )
            ).all()
            if len(sdk_events) > 1 or (row.finish_hash is not None) != bool(sdk_events):
                raise ValueError("Reader SDK finish field/event set mismatch.")
            if sdk_events:
                actual_run = await db.get(ResearchRun, row.run_id, populate_existing=True)
                await reader_sdk_finish.verify(db, owner, actual_run, row)
    from app.preview.harness import DOMAIN_TOOLS

    by_hash = {row.binding_hash: row for row in rows}
    for digest, (event, document) in finishes.items():
        thread = by_hash.get(digest)
        if (
            thread is None
            or thread.run_id != event.entity_id
            or thread.finish_hash != logical_hash(document)
            or thread.finish_checkpoint_id != document["checkpoint_id"]
            or event.event_type
            != (
                "harness.sdk_finished"
                if thread.purpose == "business"
                else "harness.assistant_finished"
            )
        ):
            raise ValueError("Harness finish mismatch.")
        SafeSerializer().check(document)
    cached_keys = set()
    for event, document in tool_results:
        SafeSerializer().check(document)
        thread = by_hash.get(document["binding_hash"])
        if (
            set(document)
            != {
                "version",
                "binding_hash",
                "run_id",
                "attempt_id",
                "key",
                "name",
                "args_hash",
                "result",
            }
            or document["version"] != 1
            or document["name"] not in DOMAIN_TOOLS
            or thread is None
            or thread.purpose != "assistant"
            or document["run_id"] != str(thread.run_id)
            or document["attempt_id"] != str(thread.attempt_id)
            or type(document["args_hash"]) is not str
            or len(document["args_hash"]) != 64
            or type(document["key"]) is not str
            or len(document["key"]) != 64
        ):
            raise ValueError("Tool result binding mismatch.")
        if runs[thread.run_id]["kind"] == "reader":
            # Reader tools are not activated in the foundation candidate.
            # Their future runtime must prove its exact admitted capabilities
            # and answer receipts before any tool result can be accepted.
            raise ValueError("Reader tool runtime is not activated.")
        if document["name"] in {
            "propose_statistics",
            "propose_plan",
            "execute_statistics",
            "execute_ml",
        }:
            expected_id = uuid.uuid5(thread.id, document["key"])
            if event.entity_id != expected_id or expected_id in cached_keys:
                raise ValueError("Tool result duplicate.")
            cached_keys.add(expected_id)
        if document["name"] in {"execute_statistics", "execute_ml"}:
            from app.models.research_execution import ResearchPublicationReceipt

            receipt = await db.get(
                ResearchPublicationReceipt, thread.attempt_id, populate_existing=True
            )
            if receipt is None or document["result"]["publication"] != {
                "run_id": str(thread.run_id),
                "attempt_id": str(thread.attempt_id),
                "state": receipt.state,
                "domain_id": str((await db.get(ResearchAttempt, thread.attempt_id)).domain_run_id),
                "snapshot_hash": receipt.snapshot_hash,
                "root_id": str(receipt.root_id),
            }:
                raise ValueError("Tool publication mismatch.")
    for identifier, expected in requests.items():
        approval = await db.get(ResearchApproval, identifier, populate_existing=True)
        if approval is None or approval.run_id not in runs:
            raise ValueError("Model request approval missing.")
        if await model_request.load(db, owner, approval) != expected:
            raise ValueError("Model request mismatch.")
    receipts = (
        await db.scalars(
            select(ResearchAdvisoryReceipt).where(ResearchAdvisoryReceipt.run_id.in_(runs)),
            execution_options={"populate_existing": True},
        )
    ).all()
    if {row.charge_id for row in receipts} != set(recorded):
        raise ValueError("Reply receipt set mismatch.")
    if {row.charge_id for row in receipts if row.resumed_revision is not None} != set(resumed):
        raise ValueError("Reply resume set mismatch.")
    for row in receipts:
        document = parse_document(row.document_json)
        SafeSerializer().check(document)
        charge = await db.get(ResearchCharge, row.charge_id, populate_existing=True)
        approval = (
            None
            if charge is None
            else await db.get(ResearchApproval, charge.approval_id, populate_existing=True)
        )
        attempt = await db.get(ResearchAttempt, row.attempt_id, populate_existing=True)
        if (
            row.owner_id != owner
            or row.input_hash != runs[row.run_id]["input_hash"]
            or attempt is None
            or attempt.run_id != row.run_id
            or charge is None
            or approval is None
            or charge.state != "settled"
            or charge.owner_id != owner
            or charge.run_id != row.run_id
            or approval.run_id != row.run_id
            or row.document_hash != logical_hash(document)
            or row.request_hash != charge.request_hash
            or document["version"] != 1
            or document["owner_id"] != str(owner)
            or document["run_id"] != str(row.run_id)
            or document["attempt_id"] != str(row.attempt_id)
            or document["input_hash"] != row.input_hash
            or document["charge_id"] != str(charge.id)
            or document["approval_id"] != str(approval.id)
            or document["request_hash"] != charge.request_hash
            or document["call_key"] != charge.call_key
            or document["binding"] != parse_document(approval.snapshot_json)["binding"]
            or document["binding"]["revision"] != row.approved_revision
            or document["settlement_hash"] != charge.settlement_hash
            or document["specification_hash"]
            != logical_hash(parse_document(charge.specification_json))
            or recorded[row.charge_id]
            != {
                "charge_id": str(row.charge_id),
                "attempt_id": str(row.attempt_id),
                "document_hash": row.document_hash,
            }
        ):
            raise ValueError("Reply receipt mismatch.")
        if row.resumed_revision is not None and (
            row.resumed_revision != row.approved_revision + 1
            or resumed[row.charge_id]
            != {
                "charge_id": str(row.charge_id),
                "document_hash": row.document_hash,
                "approved_revision": row.approved_revision,
                "resumed_revision": row.resumed_revision,
            }
        ):
            raise ValueError("Reply resume mismatch.")
