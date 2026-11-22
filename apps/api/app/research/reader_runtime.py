"""Two real saved-result callbacks, each bound to its original SDK model history."""

import uuid

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchEvent
from app.models.research_harness import ResearchAdvisoryReceipt, ResearchHarnessThread
from app.models.research_security import ResearchCharge
from app.preview.harness import protocol_messages
from app.research import (
    advisory_receipt,
    harness_binding,
    historical_reply,
    reader_admission,
    reader_answer,
    reader_answer_fields,
    reader_contracts,
    reader_lease,
    reader_sources,
    reader_tools,
    reader_wire_budget,
)
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import failure
from app.research.model_history import validate
from app.research.security_contracts import CallSpecification
from app.schemas.preview_extensions import ExplanationField, ExplanationRequest
from app.services import research_gateway_service as gateways
from app.services import research_security_service as security
from app.services import research_service as ledger

EVENT = "reader.tool_recorded"
NAMES = frozenset({"result_facts", "explain_results"})


def normalize(name, args, *, projection_version=1):
    if name == "result_facts" and args == {}:
        return {}
    if name == "explain_results" and isinstance(args, dict) and set(args) == {"spec"}:
        return {
            "spec": reader_contracts.explanation(args["spec"], projection_version).model_dump(
                mode="json"
            )
        }
    raise failure("READER_TOOL_DENIED")


def _history(messages, tool_call_id, *, projection_version=1):
    matches = [
        (i, m)
        for i, m in enumerate(messages)
        if isinstance(m, AIMessage) and any(c.get("id") == tool_call_id for c in m.tool_calls)
    ]
    if not matches:
        raise failure("READER_TOOL_UNPROVEN")
    index, message = matches[-1]  # Same ID in an earlier reply has a different exact history.
    calls = [c for c in message.tool_calls if c.get("id") == tool_call_id]
    if len(calls) != 1:
        raise failure("READER_TOOL_UNPROVEN")
    history = validate(
        [x for x in protocol_messages(messages[:index]) if x["role"] != "system"],
        tool_profile="reader-v2" if projection_version == 2 else "assistant",
    )
    return history, message, calls[0]


async def resolve(db, token, binding, name, args, tool_call_id, messages):
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    projection_version = reader_contracts.view_version(parse_document(run.input_json)["parameters"])
    history, actual_message, call = _history(
        messages, tool_call_id, projection_version=projection_version
    )
    if (
        call["name"] != name
        or normalize(name, call["args"], projection_version=projection_version) != args
    ):
        raise failure("READER_TOOL_UNPROVEN")
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.event_type == "harness.model_request",
            )
            .order_by(ResearchEvent.sequence)
            .limit(10_001)
        )
    ).all()
    if len(events) > 10_000:
        raise failure("HISTORY_LIMIT")
    matches = []
    for event in events:
        request = parse_document(event.payload_json)
        if (
            request.get("binding_hash"),
            request.get("attempt_id"),
            request.get("conversation"),
        ) != (binding.binding_hash, str(token.attempt_id), history):
            continue
        key = gateways.private_key(
            "reader-model:" + request["call_hash"], "call", token.owner_id, run.id
        )
        charge = await db.scalar(
            select(ResearchCharge).where(
                ResearchCharge.run_id == run.id, ResearchCharge.call_key == key
            )
        )
        reply = (
            None
            if charge is None
            else await db.get(ResearchAdvisoryReceipt, charge.id, populate_existing=True)
        )
        if reply is None:
            raise failure("READER_TOOL_UNPROVEN")
        proof = await historical_reply.prove(
            db,
            token.owner_id,
            run.id,
            token.attempt_id,
            charge.id,
            reply_hash=reply.document_hash,
            binding_hash=binding.binding_hash,
            call_hash=request["call_hash"],
        )
        original = parse_document(proof.document_json)["advisory"]
        if (
            actual_message.content != (original["text"] if original["text"] is not None else "")
            or [{k: c[k] for k in ("id", "name", "args")} for c in actual_message.tool_calls]
            != original["tool_calls"]
        ):
            raise failure("READER_TOOL_UNPROVEN")
        matches.append(
            dict(
                charge_id=str(charge.id),
                reply_hash=reply.document_hash,
                call_hash=request["call_hash"],
                tool_call_id=tool_call_id,
            )
        )
    if len(matches) != 1:
        raise failure("READER_TOOL_UNPROVEN")
    return matches[0]


def identity(binding, reference):
    return uuid.uuid5(
        binding.id,
        logical_hash(
            dict(charge_id=reference["charge_id"], tool_call_id=reference["tool_call_id"])
        ),
    )


def facts_result(source):
    value = reader_sources.document(source)
    return dict(kind="selected_saved_results", computed=False, results=value["results"])


def answer_result(receipt):
    return dict(
        kind="reader_answer_receipt",
        answer_id=str(receipt.id),
        answer_hash=receipt.document_hash,
        computed=False,
    )


async def prove_event(db, owner, event):
    value = parse_document(event.payload_json)
    if (
        set(value)
        != {
            "version",
            "run_id",
            "attempt_id",
            "binding_id",
            "binding_hash",
            "name",
            "args",
            "result",
            "source",
            "charge_id",
            "reply_hash",
            "call_hash",
            "tool_call_id",
        }
        or value["version"] != 1
        or value["name"] not in NAMES
    ):
        raise failure("READER_TOOL_UNPROVEN")
    run, scope = await ledger.run(db, uuid.UUID(value["run_id"]), owner)
    projection_version = reader_contracts.view_version(parse_document(run.input_json)["parameters"])
    attempt = await db.get(ResearchAttempt, uuid.UUID(value["attempt_id"]), populate_existing=True)
    thread = await db.get(
        ResearchHarnessThread, uuid.UUID(value["binding_id"]), populate_existing=True
    )
    if (
        run.kind != "reader"
        or attempt is None
        or attempt.run_id != run.id
        or thread is None
        or thread.owner_id != owner
        or thread.run_id != run.id
        or thread.attempt_id != attempt.id
        or thread.binding_hash != value["binding_hash"]
        or thread.binding_hash != logical_hash(harness_binding.document(thread))
        or event.actor_id != owner
        or event.workspace_id != scope.id
        or event.entity_kind != "reader_tool"
        or event.entity_id != identity(thread, value)
        or event.logical_hash != logical_hash(value)
        or normalize(value["name"], value["args"], projection_version=projection_version)
        != value["args"]
    ):
        raise failure("READER_TOOL_UNPROVEN")
    admission = await reader_admission.load(db, owner, run, attempt)
    proof = await historical_reply.prove(
        db,
        owner,
        run.id,
        attempt.id,
        uuid.UUID(value["charge_id"]),
        reply_hash=value["reply_hash"],
        binding_hash=thread.binding_hash,
        call_hash=value["call_hash"],
    )
    request = parse_document(proof.request_json)
    calls = [
        c
        for c in parse_document(proof.document_json)["advisory"]["tool_calls"]
        if c.get("id") == value["tool_call_id"]
    ]
    if (
        admission is None
        or request["selection"] != admission["selection"]
        or request["tools"] != admission["tools"]
        or len(calls) != 1
        or calls[0]["name"] != value["name"]
        or normalize(value["name"], calls[0]["args"], projection_version=projection_version)
        != value["args"]
        or proof.resumed_sequence >= event.sequence
    ):
        raise failure("READER_TOOL_UNPROVEN")
    if value["name"] == "explain_results":
        receipt = await reader_answer.prove(db, owner, run, attempt)
        answer = parse_document(receipt.document_json)
        if (
            any(
                answer[k] != value[k]
                for k in ("charge_id", "reply_hash", "call_hash", "tool_call_id", "binding_hash")
            )
            or receipt.sequence >= event.sequence
            or value["source"] is not None
            or value["result"] != answer_result(receipt)
        ):
            raise failure("READER_TOOL_UNPROVEN")
    else:
        source = reader_sources.ReaderSourceProof(
            canonical_bytes(value["source"]).decode(), admission["source_proof_hash"]
        )
        document = reader_sources.document(source)
        ids = [c["artifact_id"] for result in document["results"] for c in result["citations"]]
        if (
            document["session_id"] != str(run.session_id)
            or reader_contracts.source_view(document) != projection_version
            or document["selection_hash"] != logical_hash(admission["selection"])
            or set(ids) != set(admission["selection"]["artifact_ids"])
            or len(ids) != len(set(ids))
            or value["result"] != facts_result(source)
        ):
            raise failure("READER_TOOL_UNPROVEN")
        fields = ExplanationRequest(
            fields=[
                ExplanationField(
                    kind=x["domain_kind"], run_id=uuid.UUID(x["domain_run_id"]), field="status"
                )
                for x in document["results"]
            ]
        )
        await reader_answer_fields.project(db, owner, source, fields)
    if len(canonical_bytes(value["result"])) > reader_contracts.tool_limit(projection_version):
        raise failure("READER_TOOL_UNPROVEN")
    return value


async def wire_budget(db, token, binding, charge, messages, tool_call_id, result):
    history, _, _ = _history(messages, tool_call_id, projection_version=2)
    projected = [m for m in protocol_messages(messages) if m["role"] != "system"]
    if projected[: len(history)] != history or len(projected) != len(history) + 1:
        raise failure("READER_WIRE_BUDGET")
    reply = projected[-1]
    results = {tool_call_id: result}
    run, scope = await ledger.run(db, token.run_id, token.owner_id)
    # reader_lease.validate already acquired the owner transaction fence. Each
    # sibling callback includes committed results of the same exact model reply.
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.event_type == EVENT,
            )
            .limit(10001)
        )
    ).all()
    if len(events) > 10000:
        raise failure("READER_WIRE_BUDGET")
    for event in events:
        value = parse_document(event.payload_json)
        if value["charge_id"] != str(charge.id):
            continue
        identifier = value["tool_call_id"]
        if (
            identifier in results
            or value["run_id"] != str(run.id)
            or value["attempt_id"] != str(token.attempt_id)
            or value["binding_hash"] != binding.binding_hash
            or event.actor_id != token.owner_id
        ):
            raise failure("READER_WIRE_BUDGET")
        results[identifier] = value["result"]
    specification = CallSpecification.model_validate(parse_document(charge.specification_json))
    reader_wire_budget.check(specification.input_bytes, history, reply, results)


class ReaderRuntime:
    def __init__(self, factory, token, binding):
        self.factory, self.token, self.binding = factory, token, binding

    def tools(self):
        return reader_tools.build_tools(
            self.call,
            stateful=True,
            projection_version=reader_contracts.view_from_profile(self.binding.profile_hash),
        )

    async def call(self, name, args, tool_call_id, messages):
        projection_version = reader_contracts.view_from_profile(self.binding.profile_hash)
        args = normalize(name, args, projection_version=projection_version)
        await harness_binding.authorize(self.factory, self.binding, self.token)
        async with self.factory() as db:
            run, scope, lease, _ = await reader_lease.validate(db, self.token, current_source=False)
            from app.research import reader_control

            attempt = await db.get(ResearchAttempt, self.token.attempt_id, populate_existing=True)
            if await reader_control.cancelled(db, self.token.owner_id, run, attempt):
                raise failure("MODEL_CANCELLED")
            if lease.pause_requested:
                raise failure("MODEL_PAUSED")
            reference = await resolve(
                db, self.token, self.binding, name, args, tool_call_id, messages
            )
            key = identity(self.binding, reference)
            old = (
                await db.scalars(
                    select(ResearchEvent)
                    .where(
                        ResearchEvent.workspace_id == scope.id,
                        ResearchEvent.entity_id == key,
                        ResearchEvent.event_type == EVENT,
                    )
                    .limit(2)
                )
            ).all()
            if len(old) > 1:
                raise failure("READER_TOOL_UNPROVEN")
            if old:
                value = await prove_event(db, self.token.owner_id, old[0])
                if value["name"] != name or value["args"] != args:
                    raise failure("READER_TOOL_UNPROVEN")
                return value["result"]
            run, scope, _, _ = await reader_lease.validate(db, self.token)
            charge = await security.charge(
                db, self.token.owner_id, uuid.UUID(reference["charge_id"])
            )
            approval = await security.approval(db, self.token.owner_id, charge.approval_id)
            if await advisory_receipt.read(db, self.token.owner_id, approval, charge) is None:
                raise failure("READER_TOOL_UNPROVEN")
            source = None
            if name == "explain_results":
                receipt = await reader_answer.record(
                    db,
                    self.token,
                    self.binding,
                    charge.id,
                    reference["reply_hash"],
                    reference["call_hash"],
                    tool_call_id,
                    reader_contracts.explanation(args["spec"], projection_version),
                )
                result = answer_result(receipt)
            else:
                source = await reader_lease.source(db, self.token.owner_id, run)
                result = facts_result(source)
            if len(canonical_bytes(result)) > reader_contracts.tool_limit(projection_version):
                raise failure("READER_TOOL_LIMIT")
            if projection_version == 2:
                await wire_budget(
                    db, self.token, self.binding, charge, messages, tool_call_id, result
                )
            value = dict(
                version=1,
                run_id=str(run.id),
                attempt_id=str(self.token.attempt_id),
                binding_id=str(self.binding.id),
                binding_hash=self.binding.binding_hash,
                name=name,
                args=args,
                result=result,
                source=reader_sources.document(source) if source else None,
                **reference,
            )
            await ledger._append(db, scope, self.token.owner_id, "reader_tool", key, EVENT, value)
            await db.commit()
            return result


async def verify_messages(db, token, binding, messages):
    """Every cached domain ToolMessage must equal a durable causally proved result."""
    for index, item in enumerate(messages):
        if not isinstance(item, ToolMessage):
            continue
        _, _, call = _history(
            messages[:index],
            item.tool_call_id,
            projection_version=reader_contracts.view_from_profile(binding.profile_hash),
        )
        if call["name"] == "write_todos":
            from app.preview.harness import planning_todos

            planning_todos(call["args"])
            if item.content != '{"state":"reader_state_updated"}' or item.status == "error":
                raise failure("READER_TOOL_UNPROVEN")
            continue
        reference = await resolve(
            db,
            token,
            binding,
            call["name"],
            normalize(
                call["name"],
                call["args"],
                projection_version=reader_contracts.view_from_profile(binding.profile_hash),
            ),
            item.tool_call_id,
            messages[:index],
        )
        event = await db.scalar(
            select(ResearchEvent).where(
                ResearchEvent.entity_id == identity(binding, reference),
                ResearchEvent.event_type == EVENT,
            )
        )
        if event is None:
            raise failure("READER_TOOL_UNPROVEN")
        value = await prove_event(db, token.owner_id, event)
        if (
            item.status == "error"
            or not isinstance(item.content, str)
            or parse_document(item.content) != value["result"]
            or (
                reader_contracts.view_from_profile(binding.profile_hash) == 2
                and item.content.encode("utf-8") != canonical_bytes(value["result"])
            )
        ):
            raise failure("READER_TOOL_UNPROVEN")
