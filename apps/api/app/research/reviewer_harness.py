"""Independent restricted Reviewer graph; callbacks own all business authority."""

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from importlib.metadata import entry_points, version
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware, hook_config
from langchain.agents.middleware.types import AgentState
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.errors import GraphBubbleUp
from langgraph.func import task
from langgraph.types import Command, RetryPolicy
from langsmith import tracing_context
from pydantic import Field

from app.exceptions import StatForgeError
from app.preview.harness import EXCLUDED_TOOLS, capability_error, planning_todos, protocol_messages
from app.research.contracts import canonical_bytes, logical_hash
from app.research.egress import REVIEWER_SYSTEM_TEXT
from app.research.harness_serde import SafeSerializer
from app.research.reviewer_question import reviewer_question_fits
from app.research.reviewer_tools import assert_tools

REVIEWER_TOOLS = frozenset({"read_review_evidence", "submit_review"})
SDK_TOOLS = frozenset({"write_todos"})
PAUSED_CODES = frozenset({"RESEARCH_MODEL_PAUSED", "RESEARCH_EGRESS_MODEL_PAUSED"})
PROFILE_KEY = "statforge:statistical-reviewer-v1"
SYSTEM_PROMPT = REVIEWER_SYSTEM_TEXT


def _schemas(tools: Sequence[Any], *, sdk: bool = False) -> list[dict[str, Any]]:
    """Pin the entire independent server schema, including internal SDK todos."""
    try:
        serialized = sorted(
            [convert_to_openai_tool(tool) for tool in tools],
            key=lambda item: item["function"]["name"],
        )
        accepted = assert_tools(serialized, sdk=sdk)
        SafeSerializer().check(accepted)
        return accepted
    except Exception:
        raise capability_error() from None


def _reply(reply: AIMessage) -> None:
    if type(reply) is not AIMessage or reply.invalid_tool_calls or len(reply.tool_calls) > 8:
        raise capability_error()
    seen = set()
    for call in reply.tool_calls:
        identity = call.get("id")
        if (
            call["name"] not in REVIEWER_TOOLS | SDK_TOOLS
            or not isinstance(identity, str)
            or not 1 <= len(identity) <= 128
            or identity in seen
        ):
            raise capability_error()
        seen.add(identity)
        if call["name"] in SDK_TOOLS:
            planning_todos(call["args"])
    SafeSerializer().check(reply)


class ReviewerBridge(BaseChatModel):
    model_name: str = "statistical-reviewer-v1"
    binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    gateway: Callable[[list[BaseMessage], list[dict[str, Any]]], Awaitable[AIMessage]] = Field(
        exclude=True, repr=False
    )
    authorize: Callable[[], Awaitable[None]] = Field(exclude=True, repr=False)
    verify_reply: Callable[[str, AIMessage], Awaitable[None]] = Field(exclude=True, repr=False)
    verify_state: Callable[[dict[str, Any]], Awaitable[None]] = Field(exclude=True, repr=False)
    receipted: Callable[[], Awaitable[bool]] = Field(exclude=True, repr=False)
    bound_tools: list[dict[str, Any]] = Field(default_factory=list, exclude=True, repr=False)
    domain_schemas: list[dict[str, Any]] = Field(default_factory=list, exclude=True, repr=False)

    @property
    def _llm_type(self) -> str:
        return "statforge-reviewer"

    def _get_ls_params(self, stop=None, **kwargs):
        return {
            "ls_provider": "statforge",
            "ls_model_name": "statistical-reviewer-v1",
            "ls_model_type": "chat",
        }

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        if (tool_choice is not None and tool_choice != "auto") or kwargs:
            raise capability_error()
        actual = _schemas(tools, sdk=True)
        domain = [item for item in actual if item["function"]["name"] in REVIEWER_TOOLS]
        if not self.domain_schemas or canonical_bytes(domain) != canonical_bytes(
            self.domain_schemas
        ):
            raise capability_error()
        return self.model_copy(update={"bound_tools": actual})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise capability_error()

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if (
            self.model_name != "statistical-reviewer-v1"
            or self.callbacks is not None
            or len(messages) > 32
        ):
            raise capability_error()
        actual = _schemas(self.bound_tools, sdk=True)
        domain = [item for item in actual if item["function"]["name"] in REVIEWER_TOOLS]
        if canonical_bytes(domain) != canonical_bytes(self.domain_schemas):
            raise capability_error()
        projection = protocol_messages(messages)
        SafeSerializer().check(projection)
        if len(canonical_bytes(projection)) > 64 * 1024 or any(
            len(item["content"].encode("utf-8")) > 8192 for item in projection
        ):
            raise capability_error()
        call_hash = logical_hash(
            {"binding_hash": self.binding_hash, "history": projection, "tools": actual}
        )
        reference = {"binding_hash": self.binding_hash, "call_hash": call_hash}
        await self.authorize()

        async def model_task_impl(ref):
            if ref != reference:
                raise capability_error()
            await self.authorize()
            reply = await self.gateway(messages, actual)
            _reply(reply)
            await self.verify_reply(call_hash, reply)
            return reply

        durable = cast(
            Callable[[dict[str, str]], Awaitable[AIMessage]],
            cast(Callable[..., Any], task)(
                model_task_impl, name="model", retry_policy=RetryPolicy(max_attempts=1)
            ),
        )
        reply = await durable(reference)
        await self.authorize()
        _reply(reply)
        await self.verify_reply(call_hash, reply)
        return ChatResult(generations=[ChatGeneration(message=reply)])


class ReviewerToolGuard(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        if (
            call["name"] not in REVIEWER_TOOLS | SDK_TOOLS
            or not isinstance(call.get("id"), str)
            or not 1 <= len(call["id"]) <= 128
        ):
            raise capability_error()
        try:
            SafeSerializer().check(call)
            todos = planning_todos(call["args"]) if call["name"] in SDK_TOOLS else None
            result = await handler(request)
            if todos is not None:
                if (
                    not isinstance(result, Command)
                    or not isinstance(result.update, dict)
                    or set(result.update) != {"todos", "messages"}
                    or result.update["todos"] != todos
                    or not isinstance(result.update["messages"], list)
                    or len(result.update["messages"]) != 1
                    or type(result.update["messages"][0]) is not ToolMessage
                    or result.update["messages"][0].status != "success"
                    or result.update["messages"][0].tool_call_id != call["id"]
                ):
                    raise capability_error()
                SafeSerializer().check(result)
                return Command(
                    update={
                        "todos": todos,
                        "messages": [
                            ToolMessage(
                                content='{"state":"reviewer_state_updated"}',
                                tool_call_id=call["id"],
                            )
                        ],
                    }
                )
            if (
                type(result) is not ToolMessage
                or not isinstance(result.content, str)
                or result.status != "success"
                or len(result.content.encode("utf-8")) > 8192
                or result.tool_call_id != call["id"]
            ):
                raise capability_error()
            SafeSerializer().check(result)
            return result
        except GraphBubbleUp:
            raise
        except Exception as exc:
            code = exc.error_code if isinstance(exc, StatForgeError) else "REVIEWER_TOOL_REJECTED"
            if isinstance(code, str) and code in PAUSED_CODES:
                raise  # Keep the SDK node pending, with no cached false tool error.
            if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) is None:
                code = "REVIEWER_TOOL_REJECTED"
            return ToolMessage(
                content=json.dumps({"state": "rejected", "error_code": code}),
                tool_call_id=call["id"],
                status="error",
            )


class ReceiptBoundary(AgentMiddleware):
    def __init__(self, receipted):
        self.receipted = receipted

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        receipted = await self.receipted()
        if type(receipted) is not bool:
            raise capability_error()
        return {"jump_to": "end"} if receipted else None


class ReviewerHarnessAdapter:
    async def abuild(self, model: ReviewerBridge, tools: list[BaseTool], saver):
        from app.preview.harness import prepare_sdk

        await prepare_sdk()
        return self.build(model, tools, saver)

    def build(self, model: ReviewerBridge, tools: list[BaseTool], saver):
        if (
            version("deepagents") != "0.7.21"
            or type(model) is not ReviewerBridge
            or model.model_name != "statistical-reviewer-v1"
            or model.callbacks is not None
            or saver is None
        ):
            raise capability_error()
        if any(
            entry_points(group=group)
            for group in ("deepagents.provider_profiles", "deepagents.harness_profiles")
        ):
            raise capability_error()
        schemas = _schemas(tools)
        model = model.model_copy(update={"domain_schemas": schemas, "bound_tools": []})
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from deepagents.profiles.harness import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )

        profile = HarnessProfile(
            base_system_prompt=SYSTEM_PROMPT,
            excluded_tools=EXCLUDED_TOOLS,
            excluded_middleware=frozenset({"SummarizationMiddleware"}),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        )
        register_harness_profile(PROFILE_KEY, profile)
        return create_deep_agent(
            model=model,
            tools=tools,
            middleware=[
                cast(
                    AgentMiddleware[AgentState, None, Any],
                    TodoListMiddleware(
                        system_prompt=(
                            "Use at most eight reviewer todos. Todos are not a ReviewReceipt "
                            "or evidence of business completion."
                        )
                    ),
                ),
                ReviewerToolGuard(),
                ReceiptBoundary(model.receipted),
            ],
            system_prompt=SYSTEM_PROMPT,
            backend=StateBackend(),
            subagents=[],
            checkpointer=saver,
            store=None,
            debug=False,
            name="statforge-reviewer",
        )

    async def invoke(
        self,
        model: ReviewerBridge,
        tools: list[BaseTool],
        saver: Any,
        binding: Any,
        *,
        question: str | None = None,
        resume: Any = None,
    ) -> dict[str, Any]:
        if (
            type(model) is not ReviewerBridge
            or (question is None) == (resume is None)
            or binding.purpose != "assistant"
            or model.binding_hash != binding.binding_hash
            or not isinstance(binding.thread_id, str)
            or not 1 <= len(binding.thread_id) <= 128
        ):
            raise capability_error()
        if question is not None and not reviewer_question_fits(question):
            raise capability_error()
        if resume is not None:
            SafeSerializer().check(resume)
        await model.authorize()
        config = {
            "configurable": {"thread_id": binding.thread_id},
            "callbacks": [],
            "recursion_limit": 12,
        }
        value: Any = (
            Command(resume=resume)
            if resume is not None
            else {"messages": [{"role": "user", "content": question}]}
        )
        with tracing_context(enabled=False):
            graph = await self.abuild(model, tools, saver)
            result = await graph.ainvoke(value, config=config, durability="sync")
        await model.authorize()
        await model.verify_state(result)
        return result
