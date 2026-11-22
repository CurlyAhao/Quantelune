"""A finished official graph is recorded only alongside real business publication."""

import hashlib

from app.models.research_harness import ResearchHarnessThread
from app.research import harness_binding, publication
from app.research.assistant_model import AssistantModel
from app.research.contracts import logical_hash
from app.research.egress import DisclosureSelection, failure
from app.research.execution_lease import _boundary
from app.research.formal_harness import FormalHarnessAdapter
from app.research.harness_runtime import open_saver
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger


async def finish(factory, token, gateway, tools, invocation):
    binding = await harness_binding.existing(factory, token, "assistant")
    actual = await publication._confirmed(factory, token)
    if binding is None or actual is None:
        raise failure("HARNESS_BUSINESS_UNPROVEN")
    runtime = AssistantModel(
        factory,
        token,
        binding,
        gateway,
        DisclosureSelection.model_validate(invocation["selection"]),
    )
    config = {
        "configurable": {"thread_id": binding.thread_id},
        "callbacks": [],
        "recursion_limit": 12,
    }
    async with open_saver(factory, binding, token) as saver:
        graph = await FormalHarnessAdapter().abuild(runtime.bridge(), tools, saver)
        state = await graph.aget_state(config)
        if (
            state.next
            or not state.values
            or any(task.error or task.interrupts for task in state.tasks)
        ):
            raise failure("HARNESS_CHECKPOINT_UNPROVEN")
        await runtime.verify_state(state.values)
        checkpoint = await saver.aget_tuple(state.config)
        if checkpoint is None:
            raise failure("HARNESS_CHECKPOINT_UNPROVEN")
        checkpoint_id = checkpoint.config["configurable"]["checkpoint_id"]
        proof = {
            "binding_hash": binding.binding_hash,
            "checkpoint_id": checkpoint_id,
            "state_hash": hashlib.sha256(SafeSerializer().dumps_typed(state.values)[1]).hexdigest(),
            "publication": actual,
        }
        digest = logical_hash(proof)
        await harness_binding.authorize(factory, binding, token)
        async with factory() as db:
            await _boundary(db, token.owner_id)
            run, scope = await ledger.run(db, token.run_id, token.owner_id)
            row = await db.get(ResearchHarnessThread, binding.id, populate_existing=True)
            if row.binding_hash != binding.binding_hash or row.input_hash != run.input_hash:
                raise failure("MODEL_BINDING_CHANGED")
            if row.finish_hash is not None:
                if row.finish_hash != digest or row.finish_checkpoint_id != checkpoint_id:
                    raise failure("HARNESS_CHECKPOINT_UNPROVEN")
                return actual
            row.finish_hash, row.finish_checkpoint_id = digest, checkpoint_id
            await ledger._append(
                db, scope, token.owner_id, "run", run.id, "harness.assistant_finished", proof
            )
            await db.commit()
    return actual
