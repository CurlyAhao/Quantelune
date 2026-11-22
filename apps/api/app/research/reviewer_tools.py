"""Independent Reviewer tools, with no IO, model calls or native execution."""

from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from typing import Annotated, Any

from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, ConfigDict

from app.research.contracts import canonical_bytes
from app.research.egress import failure
from app.research.reviewer_contracts import ReviewReport

ToolCallback = Callable[..., Awaitable[Any]]


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: Annotated[str, InjectedToolCallId]


class ReviewArguments(NoArguments):
    report: ReviewReport


class StatefulNoArguments(NoArguments):
    messages: Annotated[list[BaseMessage], InjectedState("messages")]


class StatefulReviewArguments(ReviewArguments):
    messages: Annotated[list[BaseMessage], InjectedState("messages")]


def build_tools(call: ToolCallback, *, stateful: bool = False) -> list[StructuredTool]:
    async def read_review_evidence(*, tool_call_id: Annotated[str, InjectedToolCallId]) -> Any:
        return await call("read_review_evidence", {}, tool_call_id)

    async def submit_review(
        report: ReviewReport, *, tool_call_id: Annotated[str, InjectedToolCallId]
    ) -> Any:
        checked = ReviewReport.model_validate(report.model_dump(mode="python", warnings="error"))
        return await call(
            "submit_review", {"report": checked.model_dump(mode="json")}, tool_call_id
        )

    async def stateful_evidence(
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
        messages: Annotated[list[BaseMessage], InjectedState("messages")],
    ) -> Any:
        return await call("read_review_evidence", {}, tool_call_id, messages)

    async def stateful_review(
        report: ReviewReport,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
        messages: Annotated[list[BaseMessage], InjectedState("messages")],
    ) -> Any:
        checked = ReviewReport.model_validate(report.model_dump(mode="python", warnings="error"))
        return await call(
            "submit_review", {"report": checked.model_dump(mode="json")}, tool_call_id, messages
        )

    return [
        StructuredTool.from_function(
            coroutine=stateful_evidence if stateful else read_review_evidence,
            name="read_review_evidence",
            description="Read only the captured Protocol and proved aggregate review evidence.",
            args_schema=StatefulNoArguments if stateful else NoArguments,
        ),
        StructuredTool.from_function(
            coroutine=stateful_review if stateful else submit_review,
            name="submit_review",
            description=(
                "Submit coded review issues with exact evidence references; no new analysis."
            ),
            args_schema=StatefulReviewArguments if stateful else ReviewArguments,
        ),
    ]


def schemas(*, sdk: bool = True) -> list[dict[str, Any]]:
    async def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise failure("INVOCATION_REQUIRED")

    tools: list[BaseTool] = list(build_tools(unavailable))
    if sdk:
        tools.extend(TodoListMiddleware().tools)
    return sorted(
        [convert_to_openai_tool(tool) for tool in tools], key=lambda item: item["function"]["name"]
    )


def assert_tools(tools: Sequence[dict[str, Any]], *, sdk: bool = True) -> list[dict[str, Any]]:
    try:
        accepted = canonical_bytes(list(tools)) == canonical_bytes(schemas(sdk=sdk))
    except (TypeError, ValueError):
        accepted = False
    if not accepted:
        raise failure("REVIEWER_TOOL_DENIED")
    return deepcopy(list(tools))
