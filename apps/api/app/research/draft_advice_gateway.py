"""Closed draft-advice wire profile; legacy research profiles stay unchanged."""

import uuid

from langchain_core.utils.function_calling import convert_to_openai_tool

from app.models.user import User
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.draft_advice import (
    PROFILE,
    AdviceContext,
    build_tools,
    load_coding_context,
    load_plot_context,
)
from app.research.egress import DisclosureSelection, failure
from app.schemas.draft_advice import CodingAdviceContextRequest

KIND = "draft_advice"
VERSION = "draft-advice-request-v1"


def parameters(run):
    document = parse_document(run.input_json)
    value = document["parameters"]
    if (
        run.kind != KIND
        or document["kind"] != KIND
        or logical_hash(document) != run.input_hash
        or set(value)
        != {
            "version",
            "project_id",
            "request_id",
            "question",
            "target",
            "context",
            "source_version",
            "profile_hash",
        }
        or value["version"] != VERSION
        or value["profile_hash"] != logical_hash(PROFILE)
        or value["context"].get("kind") not in {"plot", "coding", "selection"}
        or value["context"].get("project_id") != value["project_id"]
        or not isinstance(value["question"], str)
        or not 1 <= len(value["question"]) <= 4000
        or logical_hash(value["context"]) != value["source_version"]
    ):
        raise failure("ADVICE_BINDING_CHANGED")
    uuid.UUID(value["project_id"])
    uuid.UUID(value["request_id"])
    return value


def selection(value):
    # The exact current code/selected quotes are separately visible in approval.
    # They are never smuggled into a schema-only call or interpreted as table rows.
    return DisclosureSelection(
        question=value["question"],
        purpose="analysis",
        scope="raw_data_approved",
        rows=1,
        acknowledge_raw=True,
    )


def schemas(context):
    async def unavailable(*_args):
        raise failure("ADVICE_BINDING_CHANGED")

    return [convert_to_openai_tool(tool) for tool in build_tools(unavailable, context["kind"])]


def assert_tools(context, tools):
    expected = schemas(context)
    if canonical_bytes(list(tools)) != canonical_bytes(expected):
        raise failure("TOOL_DENIED")
    return expected


async def fresh(db, owner, run, chosen):
    from app.services import research_service as ledger

    value = parameters(run)
    if chosen.model_dump(mode="json") != selection(value).model_dump(mode="json"):
        raise failure("ADVICE_DISCLOSURE_CHANGED")
    _, workspace = await ledger.session(db, run.session_id, owner, mode="live")
    if str(workspace.project_id) != value["project_id"]:
        raise failure("ADVICE_BINDING_CHANGED")
    user = await db.get(User, owner, populate_existing=True)
    if user is None or not user.is_active:
        raise failure("ADVICE_BINDING_CHANGED")
    project = uuid.UUID(value["project_id"])
    if value["context"]["kind"] == "plot":
        target = value["target"]
        if set(target) != {"draft_id", "revision"}:
            raise failure("ADVICE_BINDING_CHANGED")
        context = await load_plot_context(
            db, project, uuid.UUID(target["draft_id"]), user, target["revision"]
        )
    elif value["context"]["kind"] == "selection":
        from app.schemas.draft_advice import SelectionTarget
        from app.services.coding_suggestions import selection_context

        context = await selection_context(
            db, project, user, SelectionTarget.model_validate(value["target"])
        )
    else:
        context = await load_coding_context(
            db, project, user, CodingAdviceContextRequest.model_validate(value["target"])
        )
    if context.source_version != value["source_version"] or canonical_bytes(
        context.document
    ) != canonical_bytes(value["context"]):
        raise failure("ADVICE_SOURCE_CHANGED")
    return context


def wire_context(value):
    context = AdviceContext(canonical_bytes(value["context"]).decode())
    if context.source_version != value["source_version"]:
        raise failure("ADVICE_BINDING_CHANGED")
    return {
        "source_version": context.source_version,
        "profile_hash": logical_hash(PROFILE),
        "draft": context.document,
    }
