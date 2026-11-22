"""Historical P4 facts from actual accepted steps and whole publication proof."""

import uuid

from app.models.research_execution import ResearchPublicationReceipt
from app.research import plan_acceptance, plan_execution, plan_versioning, result_contexts
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import logical_hash
from app.research.p4_result_facts import project
from app.research.plan_validation import failure


async def read(db, owner: uuid.UUID, session_id: uuid.UUID, acceptance_id: uuid.UUID, step_id: str):
    return await _read(db, owner, session_id, acceptance_id, step_id, replay=True)


async def read_in_verified_replay(db, owner, session_id, acceptance_id, step_id):
    """Outer business replay must first prove the prior accepted step event.

    This avoids recursive whole-ledger verification; independent saved Plan,
    actual step, domain output and whole publication byte proofs still run.
    Never use this helper to authorize a new live review or statistical launch.
    """
    return await _read(db, owner, session_id, acceptance_id, step_id, replay=False)


async def _read(db, owner, session_id, acceptance_id, step_id, *, replay):
    acceptance, _, plan, steps = await plan_execution.load(db, owner, acceptance_id, replay=replay)
    if acceptance.session_id != session_id:
        raise failure("RESULT_FACTS_UNAVAILABLE")
    if not plan_versioning.is_v2(plan) or step_id not in steps:
        raise failure("RESULT_FACTS_UNAVAILABLE")
    step = steps[step_id]
    if step.result_hash is None or step.result_attempt_id is None:
        raise failure("RESULT_FACTS_UNAVAILABLE")
    proved = await plan_execution.prove_step(db, owner, acceptance, step)
    citation = proved["citation"]
    if citation.get("kind") != "native_result" or citation["domain_kind"] != "statistics":
        raise failure("RESULT_FACTS_UNAVAILABLE")
    publication = await db.get(
        ResearchPublicationReceipt, step.result_attempt_id, populate_existing=True
    )
    if publication is None:
        raise failure("RESULT_FACTS_UNPROVEN")
    snapshot, root = await result_contexts.prove_result(db, owner, publication.root_id)
    same = {
        "research_run_id": "run_id",
        "attempt_id": "attempt_id",
        "attempt_number": "attempt_number",
        "completion_hash": "completion_hash",
        "input_hash": "input_hash",
        "domain_kind": "domain_kind",
        "domain_run_id": "domain_run_id",
    }
    if any(root[key] != citation[other] for key, other in same.items()):
        raise failure("RESULT_FACTS_UNPROVEN")
    root_ref = next(
        (item for item in citation["artifacts"] if item["id"] == root["root_artifact_id"]), None
    )
    if (
        root_ref is None
        or root_ref["sha256"] != root["root_sha256"]
        or root_ref["logical_hash"] != root["root_logical_hash"]
    ):
        raise failure("RESULT_FACTS_UNPROVEN")
    results = snapshot.document.get("results")
    if (
        snapshot.kind != "statistics"
        or type(results) is not list
        or len(results) != 1
        or type(results[0].get("result")) is not dict
    ):
        raise failure("RESULT_FACTS_UNPROVEN")
    _, _, saved = await plan_acceptance.version(db, owner, session_id, acceptance.plan_version_id)
    protocol = plan_versioning.captured_protocol(
        plan, ContextSnapshot.model_validate(saved["context"]), saved["protocol"]
    )
    binding = {
        "acceptance_id": str(acceptance.id),
        "acceptance_hash": acceptance.document_hash,
        "plan_version_id": str(acceptance.plan_version_id),
        "plan_hash": saved["plan_hash"],
        "step_id": step_id,
        "step_document_hash": step.document_hash,
        "step_result_hash": step.result_hash,
        "full_result_hash": logical_hash(results[0]["result"]),
        "protocol": plan_versioning.binding(plan),
        "citation": citation,
        "root": root,
    }
    return project(plan, step_id, protocol, results[0]["result"], binding)
