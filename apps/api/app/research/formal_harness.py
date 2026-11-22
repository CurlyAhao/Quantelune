"""Restricted official Deep Agents graph with owner-bound durable model tasks."""

from collections.abc import Awaitable, Callable
from importlib.metadata import entry_points, version
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware, hook_config
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langgraph.func import task
from langgraph.types import Command, RetryPolicy
from langsmith import tracing_context
from pydantic import Field

from app.preview.harness import (
    DOMAIN_TOOLS,
    EXCLUDED_TOOLS,
    SDK_TOOLS,
    SYSTEM_PROMPT,
    StatForgeBridge,
    ToolGuard,
    assert_tools,
    capability_error,
    planning_todos,
    protocol_messages,
)
from app.research.contracts import logical_hash
from app.research.harness_serde import SafeSerializer

PROFILE_KEY = "statforge:formal-v1"


class FormalBridge(StatForgeBridge):
    model_name: str = "formal-v1"
    binding_hash: str
    authorize: Callable[[], Awaitable[None]] = Field(exclude=True, repr=False)
    # This runtime-only callback must load/verify the actual business reply
    # receipt even when the SDK returns its cached model task result.
    verify_reply: Callable[[str, AIMessage], Awaitable[None]] = Field(exclude=True, repr=False)
    verify_state: Callable[[dict[str, Any]], Awaitable[None]] = Field(exclude=True, repr=False)
    published: Callable[[], Awaitable[bool]] = Field(exclude=True, repr=False)

    def _get_ls_params(self, stop=None, **kwargs):
        return {"ls_provider": "statforge", "ls_model_name": "formal-v1", "ls_model_type": "chat"}

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.model_name != "formal-v1" or self.callbacks is not None:
            raise capability_error()
        assert_tools(self.bound_tools, sdk=True)
        if len(messages) > 32:
            raise capability_error()
        projection = protocol_messages(messages)
        serde = SafeSerializer()
        serde.check(projection)
        call_hash = logical_hash(
            {"binding_hash": self.binding_hash, "history": projection, "tools": self.bound_tools}
        )
        reference = {"binding_hash": self.binding_hash, "call_hash": call_hash}
        await self.authorize()

        async def model_task_impl(ref):
            if ref != reference:
                raise capability_error()
            await self.authorize()
            # The callback owns approval/receipt lookup and once-only transport;
            # request/handler/Gateway/credentials never become task arguments.
            reply = await self.gateway(messages, self.bound_tools)
            if (
                not isinstance(reply, AIMessage)
                or reply.invalid_tool_calls
                or any(item["name"] not in DOMAIN_TOOLS | SDK_TOOLS for item in reply.tool_calls)
            ):
                raise capability_error()
            for item in reply.tool_calls:
                if item["name"] in SDK_TOOLS:
                    planning_todos(item["args"])
            if (
                any(
                    item["name"] in {"execute_statistics", "execute_ml"}
                    for item in reply.tool_calls
                )
                and len(reply.tool_calls) != 1
            ):
                raise capability_error()
            serde.check(reply)
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
        await self.verify_reply(call_hash, reply)
        return ChatResult(generations=[ChatGeneration(message=reply)])


class PublicationBoundary(AgentMiddleware):
    def __init__(self, published):
        self.published = published

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        if await self.published():
            return {"jump_to": "end"}
        return None


class FormalHarnessAdapter:
    async def abuild(self, model: FormalBridge, tools: list[BaseTool], saver):
        from app.preview.harness import prepare_sdk

        await prepare_sdk()
        return self.build(model, tools, saver)

    def build(self, model: FormalBridge, tools: list[BaseTool], saver):
        if (
            version("deepagents") != "0.7.21"
            or model.model_name != "formal-v1"
            or model.callbacks is not None
        ):
            raise capability_error()
        if any(
            entry_points(group=name)
            for name in ("deepagents.provider_profiles", "deepagents.harness_profiles")
        ):
            raise capability_error()
        assert_tools(tools)
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
                            "Use up to eight planning items. "
                            "Todo status is not business completion."
                        )
                    ),
                ),
                ToolGuard(),
                PublicationBoundary(model.published),
            ],
            system_prompt=SYSTEM_PROMPT,
            backend=StateBackend(),
            subagents=[],
            checkpointer=saver,
            store=None,
            debug=False,
            name="statforge-formal",
        )

    async def invoke(self, model, tools, saver, binding, *, question=None, resume=None):
        if (question is None) == (resume is None) or binding.purpose != "assistant":
            raise capability_error()
        if model.binding_hash != binding.binding_hash:
            raise capability_error()
        if question is not None and (
            not isinstance(question, str) or not 1 <= len(question) <= 4000
        ):
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
            agent = await self.abuild(model, tools, saver)
            result = await agent.ainvoke(value, config=config, durability="sync")
        await model.authorize()
        await model.verify_state(result)
        return result
