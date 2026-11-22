"""Server-owned SDK conversation binding tied to the real approval event chain."""

from sqlalchemy import select

from app.models.research import ResearchEvent
from app.models.research_harness import ResearchHarnessThread
from app.models.research_planning import ResearchPlannerThread
from app.preview.harness import assert_tools
from app.research.contracts import logical_hash, parse_document
from app.research.egress import failure
from app.research.harness_serde import SafeSerializer
from app.research.model_history import profile_for, validate
from app.services import research_service as ledger


async def record(db, owner, approval, binding, call_hash, history, selection, tools):
    run, scope = await ledger.run(db, approval.run_id, owner)
    planner = run.kind == "planner"
    model = ResearchPlannerThread if planner else ResearchHarnessThread
    purpose = "planner" if planner else "assistant"
    thread = await db.get(model, binding.id, populate_existing=True)
    if (
        thread is None
        or binding.purpose != purpose
        or binding.owner_id != owner
        or binding.run_id != run.id
        or any(
            getattr(thread, name) != getattr(binding, name) for name in binding.__dataclass_fields__
        )
        or thread.input_hash != run.input_hash
    ):
        raise failure("MODEL_BINDING_CHANGED")
    from app.research import planner_v2

    snapshot = planner_v2.captured(run) if planner else None
    reviewer = run.kind == "reviewer"
    document = dict(
        version=4 if reviewer else 3 if snapshot is not None else 2,
        binding_id=str(binding.id),
        binding_hash=binding.binding_hash,
        call_hash=call_hash,
        approval_id=str(approval.id),
        request_hash=approval.request_hash,
        input_hash=run.input_hash,
        owner_id=str(owner),
        run_id=str(run.id),
        attempt_id=str(binding.attempt_id),
        selection=selection.model_dump(mode="json"),
        conversation=validate(
            history,
            tool_profile=profile_for(run.kind, parse_document(run.input_json)["parameters"]),
        ),
        tools=_tools(tools, run.kind, parse_document(run.input_json)["parameters"]),
    )
    if snapshot is not None:
        planner_v2.selected(snapshot, selection)
        document.update(
            research_protocol=planner_v2.metadata(snapshot),
            profile_hash=logical_hash(planner_v2.PROFILE),
        )
    if reviewer:
        from app.research import reviewer_contracts, reviewer_history, reviewer_sources

        saved = reviewer_sources.captured(run)
        metadata = reviewer_sources.disclosure(saved)
        reviewer_sources.selected_metadata(metadata, selection)
        if saved["owner_id"] != str(owner) or thread.profile_hash != logical_hash(
            reviewer_contracts.PROFILE
        ):
            raise failure("MODEL_BINDING_CHANGED")
        document.update(
            review_context=metadata,
            source_proof_hash=logical_hash(saved),
            profile_hash=logical_hash(reviewer_contracts.PROFILE),
            tools_hash=logical_hash(document["tools"]),
        )
        proof = await reviewer_history.prove(
            db, owner, run, document["conversation"], thread_id=thread.id
        )
        reviewer_history.before(proof, scope.event_sequence + 1)
        document["review_history_proof"] = reviewer_history.document(proof) if proof else None
    SafeSerializer().check(document)
    current = await load(db, owner, approval)
    if current is not None:
        if current != document:
            raise failure("MODEL_REQUEST_CONFLICT")
        return
    await ledger._append(
        db, scope, owner, "model_request", approval.id, "harness.model_request", document
    )


async def load(db, owner, approval):
    run, scope = await ledger.run(db, approval.run_id, owner)
    events = (
        await db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.workspace_id == scope.id,
                ResearchEvent.entity_id == approval.id,
                ResearchEvent.event_type == "harness.model_request",
            )
            .limit(2)
        )
    ).all()
    if not events:
        return None
    if len(events) != 1:
        raise failure("MODEL_REQUEST_CONFLICT")
    item = events[0]
    document = parse_document(item.payload_json)
    from app.research import planner_v2

    snapshot = planner_v2.captured(run) if run.kind == "planner" else None
    reviewer = run.kind == "reviewer"
    if (
        not isinstance(document, dict)
        or document.get("version") != (4 if reviewer else 3 if snapshot is not None else 2)
        or item.logical_hash != logical_hash(document)
        or item.entity_kind != "model_request"
        or document["owner_id"] != str(owner)
        or document["run_id"] != str(run.id)
        or document["approval_id"] != str(approval.id)
        or document["request_hash"] != approval.request_hash
        or document["input_hash"] != run.input_hash
    ):
        raise failure("MODEL_BINDING_CHANGED")
    import uuid

    planner = run.kind == "planner"
    thread = await db.get(
        ResearchPlannerThread if planner else ResearchHarnessThread,
        uuid.UUID(document["binding_id"]),
        populate_existing=True,
    )
    if (
        thread is None
        or thread.owner_id != owner
        or thread.run_id != run.id
        or thread.purpose != ("planner" if planner else "assistant")
        or thread.binding_hash != document["binding_hash"]
        or str(thread.attempt_id) != document["attempt_id"]
        or thread.input_hash != run.input_hash
    ):
        raise failure("MODEL_BINDING_CHANGED")
    validate(
        document["conversation"],
        tool_profile=profile_for(run.kind, parse_document(run.input_json)["parameters"]),
    )
    if (
        _tools(document["tools"], run.kind, parse_document(run.input_json)["parameters"])
        != document["tools"]
    ):
        raise failure("MODEL_BINDING_CHANGED")
    if snapshot is not None:
        fields = {
            "version",
            "binding_id",
            "binding_hash",
            "call_hash",
            "approval_id",
            "request_hash",
            "input_hash",
            "owner_id",
            "run_id",
            "attempt_id",
            "selection",
            "conversation",
            "tools",
            "research_protocol",
            "profile_hash",
        }
        if (
            set(document) != fields
            or type(document["version"]) is not int
            or document["research_protocol"] != planner_v2.metadata(snapshot)
            or document["profile_hash"] != logical_hash(planner_v2.PROFILE)
            or thread.profile_hash != document["profile_hash"]
        ):
            raise failure("MODEL_BINDING_CHANGED")
    if reviewer:
        from app.research import reviewer_contracts, reviewer_history, reviewer_sources
        from app.research.egress import DisclosureSelection

        saved = reviewer_sources.captured(run)
        expected = reviewer_sources.disclosure(saved)
        fields = {
            "version",
            "binding_id",
            "binding_hash",
            "call_hash",
            "approval_id",
            "request_hash",
            "input_hash",
            "owner_id",
            "run_id",
            "attempt_id",
            "selection",
            "conversation",
            "tools",
            "review_context",
            "source_proof_hash",
            "profile_hash",
            "tools_hash",
            "review_history_proof",
        }
        if (
            set(document) != fields
            or type(document["version"]) is not int
            or saved["owner_id"] != str(owner)
            or document["source_proof_hash"] != logical_hash(saved)
            or document["review_context"] != expected
            or document["profile_hash"] != logical_hash(reviewer_contracts.PROFILE)
            or thread.profile_hash != document["profile_hash"]
            or document["tools_hash"] != logical_hash(document["tools"])
        ):
            raise failure("MODEL_BINDING_CHANGED")
        reviewer_sources.selected_metadata(
            expected, DisclosureSelection.model_validate(document["selection"])
        )
        proof = await reviewer_history.prove(
            db, owner, run, document["conversation"], thread_id=thread.id
        )
        if document["review_history_proof"] != (
            reviewer_history.document(proof) if proof else None
        ):
            raise failure("MODEL_BINDING_CHANGED")
        reviewer_history.before(proof, item.sequence)
    SafeSerializer().check(document)
    return document


def _tools(value, run_kind, parameters=None):
    if run_kind == "reviewer":
        from app.research.reviewer_tools import assert_tools as reviewer_tools

        return reviewer_tools(value)
    if run_kind == "reader":
        from app.research.reader_contracts import view_version
        from app.research.reader_tools import assert_tools as reader_tools

        return reader_tools(value, projection_version=view_version(parameters))
    if run_kind == "planner":
        from app.research import planner_v2
        from app.research.planner_protocol import assert_tools as planner_tools

        if parameters is not None and planner_v2.from_parameters(parameters) is not None:
            from app.research.planner_protocol import assert_tools_v2

            return assert_tools_v2(value)

        return planner_tools(value)
    return assert_tools(value, sdk=True)
