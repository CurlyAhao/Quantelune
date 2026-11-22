"""Immutable necessary raw message facts; wire redaction is not raw authenticity."""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.preview.harness import protocol_messages
from app.research import model_history
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import REVIEWER_TOOL_NAMES
from app.research.harness_serde import MAX_BYTES, SafeSerializer
from app.services import research_service as ledger


def _refused():
    return ledger.error("REVIEWER_SAVED_MESSAGES_UNPROVEN")


def freeze(messages):
    """Preserve only comparison facts, before any await; never truncate raw args."""
    if type(messages) is not list or not 1 <= len(messages) <= 32:
        raise _refused()
    SafeSerializer().check(messages)
    raw: list[dict[str, Any]] = []
    value: dict[str, Any]
    for item in messages:
        if type(item) is HumanMessage:
            value = dict(type="human", content=item.content)
        elif type(item) is AIMessage and not item.invalid_tool_calls:
            value = dict(type="ai", content=item.content, tool_calls=item.tool_calls)
        elif type(item) is ToolMessage:
            value = dict(
                type="tool",
                content=item.content,
                tool_call_id=item.tool_call_id,
                status=item.status,
            )
        else:
            raise _refused()
        raw.append(value)
    SafeSerializer().check(raw)
    content = canonical_bytes(raw)
    if len(content) > MAX_BYTES:
        raise _refused()
    return content.decode(), logical_hash(raw)


def checked(document_json, document_hash):
    """Strict immutable raw data and its exact existing redacted wire projection."""
    try:
        if type(document_json) is not str or len(document_json.encode()) > MAX_BYTES:
            raise ValueError("Raw message bound changed.")
        raw = parse_document(document_json)
        if (
            type(raw) is not list
            or not 1 <= len(raw) <= 32
            or canonical_bytes(raw).decode() != document_json
            or logical_hash(raw) != document_hash
        ):
            raise ValueError("Raw message commitment changed.")
        SafeSerializer().check(raw)
        restored: list[BaseMessage] = []
        for item in raw:
            if type(item) is not dict or type(item.get("content")) not in {str, list}:
                raise ValueError("Raw content changed.")
            kind = item.get("type")
            if kind == "human" and set(item) == {"type", "content"}:
                restored.append(HumanMessage(content=item["content"]))
            elif kind == "ai" and set(item) == {"type", "content", "tool_calls"}:
                calls = item["tool_calls"]
                if type(calls) is not list or len(calls) > 8:
                    raise ValueError("Raw tool bound changed.")
                for call in calls:
                    if (
                        type(call) is not dict
                        or set(call) != {"id", "name", "args", "type"}
                        or call["type"] != "tool_call"
                        or call["name"] not in REVIEWER_TOOL_NAMES
                        or type(call["args"]) is not dict
                    ):
                        raise ValueError("Raw tool protocol changed.")
                reply = AIMessage(content=item["content"], tool_calls=calls)
                if reply.tool_calls != calls:
                    raise ValueError("Raw tool normalization changed.")
                restored.append(reply)
            elif (
                kind == "tool"
                and set(item) == {"type", "content", "tool_call_id", "status"}
                and type(item["content"]) is str
                and type(item["tool_call_id"]) is str
                and item["status"] == "success"
            ):
                restored.append(
                    ToolMessage(
                        content=item["content"], tool_call_id=item["tool_call_id"], status="success"
                    )
                )
            else:
                raise ValueError("Raw message shape or status changed.")
        SafeSerializer().check(restored)
        wire = model_history.validate(protocol_messages(restored), tool_profile="reviewer")
        return raw, wire
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _refused() from None
