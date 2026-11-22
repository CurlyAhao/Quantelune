"""Bounded read-only observation, without model, owner DB or finish authority."""

import hashlib
from typing import Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.research import reviewer_saved_messages, reviewer_tools, reviewer_writer
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.harness_serde import MAX_BYTES, SafeSerializer
from app.research.harness_snapshot import ReadOnlySaver
from app.research.reviewer_contracts import HashText
from app.services import research_service as ledger


def refused():
    return ledger.error("REVIEWER_CHECKPOINT_UNPROVEN")


class Observation(BaseModel):
    """Immutable internal data. Historical ledger proof is independently required."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: StrictInt
    source: Literal["loop"]
    writer: reviewer_writer.Stamp
    writer_hash: HashText
    checkpoint_hash: HashText
    metadata_hash: HashText
    state_hash: HashText
    message_storage: Literal["inline", "delta"]
    message_source_hash: HashText
    history_json: str = Field(max_length=65536)
    history_hash: HashText
    raw_messages_json: str = Field(max_length=MAX_BYTES)
    raw_messages_hash: HashText
    pending_writes: StrictInt
    next_tasks: StrictInt
    tasks: StrictInt
    interrupts: StrictInt

    @model_validator(mode="after")
    def bounded(self):
        _, history = reviewer_saved_messages.checked(self.raw_messages_json, self.raw_messages_hash)
        if (
            self.version != 2
            or self.writer.route.checkpoint_ns
            or self.writer_hash != logical_hash(self.writer.model_dump(mode="json"))
            or not history
            or history != parse_document(self.history_json)
            or self.history_json != canonical_bytes(history).decode()
            or self.history_hash != logical_hash(history)
            or any((self.pending_writes, self.next_tasks, self.tasks, self.interrupts))
        ):
            raise ValueError("Observation is not a fixed terminal root.")
        SafeSerializer().check(self.model_dump(mode="json"))
        return self


async def _state(binding, saver, config):
    """Approved graph structure, with every effect denied; expose only its SDK read."""
    from app.research.reviewer_harness import ReviewerBridge, ReviewerHarnessAdapter

    async def denied(*args, **kwargs):
        raise ledger.error("REVIEWER_SNAPSHOT_EFFECT_REFUSED")

    bridge = ReviewerBridge(
        binding_hash=binding.binding_hash,
        gateway=denied,
        authorize=denied,
        verify_reply=denied,
        verify_state=denied,
        receipted=denied,
    )
    tools: list[BaseTool] = list(reviewer_tools.build_tools(denied, stateful=True))
    with tracing_context(enabled=False):
        # No Provider, Gateway, ReviewerModel, runtime or executable graph is
        # supplied by the caller or escapes from this local structural reader.
        graph = await ReviewerHarnessAdapter().abuild(bridge, tools, saver)
        return await graph.aget_state(config)


def _hash(serde, value):
    return hashlib.sha256(serde.dumps_typed(value)[1]).hexdigest()


def _coordinates(config):
    if config is None:
        return None
    values = config["configurable"]
    return {
        "thread_id": values["thread_id"],
        "checkpoint_ns": values.get("checkpoint_ns", ""),
        "checkpoint_id": values["checkpoint_id"],
    }


def _material(checkpoint):
    return (
        _coordinates(checkpoint.config),
        checkpoint.checkpoint,
        checkpoint.metadata,
        _coordinates(checkpoint.parent_config),
        checkpoint.pending_writes,
    )


async def observe(saver, binding):
    """Use only the already excluded read capability; close it before owner work."""
    if type(saver) is not ReadOnlySaver:
        raise refused()
    serde = SafeSerializer()
    first = await saver.aget_tuple(
        {"configurable": {"thread_id": binding.thread_id, "checkpoint_ns": ""}}
    )
    stamp = reviewer_writer.from_tuple(first, binding)
    if (
        stamp.route.checkpoint_ns
        or first.metadata.get("source") != "loop"
        or type(first.pending_writes) not in {list, tuple}
        or first.pending_writes
    ):
        raise refused()
    initial = _hash(serde, _material(first))
    # The fixed coordinates contain no mutable framework callback/config.
    config = {
        "configurable": {
            "thread_id": binding.thread_id,
            "checkpoint_ns": "",
            "checkpoint_id": stamp.route.checkpoint_id,
        },
        "callbacks": [],
        "recursion_limit": 12,
    }
    state = await _state(binding, saver, config)
    if (
        state.next
        or state.tasks
        or state.interrupts
        or not isinstance(state.values, dict)
        or not state.values
        or state.values.get("__interrupt__")
        or _coordinates(state.config) != _coordinates(first.config)
        or _coordinates(state.parent_config) != _coordinates(first.parent_config)
        or state.metadata != first.metadata
        or type(state.values.get("messages")) is not list
        or not state.values["messages"]
        or any(
            isinstance(item, ToolMessage) and item.status != "success"
            for item in state.values["messages"]
        )
    ):
        raise refused()
    # Freeze hashes and message projection before the next backend await.
    state_hash = _hash(serde, state.values)
    raw_messages_json, raw_messages_hash = reviewer_saved_messages.freeze(state.values["messages"])
    _, history = reviewer_saved_messages.checked(raw_messages_json, raw_messages_hash)
    serde.check(history)
    history_json = canonical_bytes(history).decode()
    storage: Literal["inline", "delta"]
    if "messages" in first.checkpoint["channel_values"]:
        inline = first.checkpoint["channel_values"]["messages"]
        restored = inline.value if type(inline) is _DeltaSnapshot else inline
        if _hash(serde, restored) != _hash(serde, state.values["messages"]):
            raise refused()
        storage, source_hash = "inline", _hash(serde, inline)
    else:
        delta = await saver.aget_delta_channel_history(config=config, channels=["messages"])
        if "messages" not in delta or not (
            delta["messages"].get("seed") or delta["messages"]["writes"]
        ):
            raise refused()
        storage, source_hash = "delta", _hash(serde, delta)
    final = await saver.aget_tuple(config)
    if _hash(serde, _material(final)) != initial:
        raise refused()
    return Observation(
        version=2,
        source="loop",
        writer=stamp,
        writer_hash=logical_hash(stamp.model_dump(mode="json")),
        checkpoint_hash=_hash(serde, final.checkpoint),
        metadata_hash=_hash(serde, final.metadata),
        state_hash=state_hash,
        message_storage=storage,
        message_source_hash=source_hash,
        history_json=history_json,
        history_hash=logical_hash(history),
        raw_messages_json=raw_messages_json,
        raw_messages_hash=raw_messages_hash,
        pending_writes=0,
        next_tasks=0,
        tasks=0,
        interrupts=0,
    )
