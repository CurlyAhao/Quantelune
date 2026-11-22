"""Exactly two saved-result tools; no statistical execution capability."""

from typing import Annotated

from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import BaseMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, ConfigDict

from app.research.contracts import canonical_bytes
from app.research.egress import failure
from app.research.reader_saved_descriptive import ExplanationRequestV2
from app.schemas.preview_extensions import ExplanationRequest


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: Annotated[str, InjectedToolCallId]


class ExplanationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: ExplanationRequest
    tool_call_id: Annotated[str, InjectedToolCallId]


class StatefulNoArguments(NoArguments):
    messages: Annotated[list[BaseMessage], InjectedState("messages")]


class StatefulExplanationArguments(ExplanationArguments):
    messages: Annotated[list[BaseMessage], InjectedState("messages")]


class ExplanationArgumentsV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: ExplanationRequestV2
    tool_call_id: Annotated[str, InjectedToolCallId]


class StatefulExplanationArgumentsV2(ExplanationArgumentsV2):
    messages: Annotated[list[BaseMessage], InjectedState("messages")]


def build_tools(call, *, stateful=False, projection_version=1):
    from app.research.reader_contracts import view_operation

    view_operation(projection_version)

    def output(value):
        # v2 budgets the actual ToolMessage bytes, not SDK-specific dict encoding.
        return canonical_bytes(value).decode("utf-8") if projection_version == 2 else value

    async def result_facts(*, tool_call_id: Annotated[str, InjectedToolCallId]):
        return output(await call("result_facts", {}, tool_call_id))

    async def explain_results(
        spec: ExplanationRequest, *, tool_call_id: Annotated[str, InjectedToolCallId]
    ):
        return output(
            await call("explain_results", {"spec": spec.model_dump(mode="json")}, tool_call_id)
        )

    async def stateful_facts(
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
        messages: Annotated[list[BaseMessage], InjectedState("messages")],
    ):
        return output(await call("result_facts", {}, tool_call_id, messages))

    async def stateful_explanation(
        spec: ExplanationRequest,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
        messages: Annotated[list[BaseMessage], InjectedState("messages")],
    ):
        return output(
            await call(
                "explain_results", {"spec": spec.model_dump(mode="json")}, tool_call_id, messages
            )
        )

    explanation_schema = (
        (StatefulExplanationArguments if stateful else ExplanationArguments)
        if projection_version == 1
        else (StatefulExplanationArgumentsV2 if stateful else ExplanationArgumentsV2)
    )
    return [
        StructuredTool.from_function(
            coroutine=stateful_facts if stateful else result_facts,
            name="result_facts",
            description="Read only proven saved facts and citations from selected Artifacts.",
            args_schema=StatefulNoArguments if stateful else NoArguments,
        ),
        StructuredTool.from_function(
            coroutine=stateful_explanation if stateful else explain_results,
            name="explain_results",
            description="Explain explicitly selected saved result fields; no new analysis or fit.",
            args_schema=explanation_schema,
        ),
    ]


def schemas(*, sdk=True, projection_version=1):
    async def unavailable(*_args, **_kwargs):
        raise failure("INVOCATION_REQUIRED")

    tools = build_tools(unavailable, projection_version=projection_version)
    if sdk:
        tools += list(TodoListMiddleware().tools)
    return sorted(
        [convert_to_openai_tool(tool) for tool in tools], key=lambda x: x["function"]["name"]
    )


def assert_tools(tools, *, sdk=True, projection_version=1):
    if canonical_bytes(list(tools)) != canonical_bytes(
        schemas(sdk=sdk, projection_version=projection_version)
    ):
        raise failure("READER_TOOL_DENIED")
    return list(tools)
