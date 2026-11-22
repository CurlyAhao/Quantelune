"""Bounded server-constructed conversation, reviewable by its owner in approval."""

from collections.abc import Sequence
from typing import Any, Literal

from app.research.contracts import canonical_bytes, parse_document
from app.research.egress import REVIEWER_TOOL_NAMES, SAFE_TOOL_NAMES, credentials, failure

HistoryProfile = Literal["assistant", "planner", "reviewer", "reader-v2"]


def profile_for(run_kind, parameters) -> HistoryProfile:
    """Only server-loaded immutable Run inputs select richer Reader history."""
    if run_kind == "planner":
        return "planner"
    if run_kind == "reader":
        from app.research.reader_contracts import view_version

        return "reader-v2" if view_version(parameters) == 2 else "assistant"
    return "reviewer" if run_kind == "reviewer" else "assistant"


def validate(
    messages: Sequence[dict[str, Any]],
    owned_secrets=(),
    *,
    tool_profile: HistoryProfile = "assistant",
) -> list[dict[str, Any]]:
    if tool_profile not in {"assistant", "planner", "reviewer", "reader-v2"}:
        raise failure("HISTORY_PROTOCOL")
    allowed = REVIEWER_TOOL_NAMES if tool_profile == "reviewer" else SAFE_TOOL_NAMES
    if tool_profile == "planner":
        from app.research.planner_protocol import NAMES

        allowed = NAMES
    if tool_profile == "reader-v2":
        allowed = frozenset({"result_facts", "explain_results", "write_todos"})
    if not isinstance(messages, (list, tuple)) or len(messages) > 32:
        raise failure("HISTORY_LIMIT")
    if credentials(list(messages), owned_secrets):
        raise failure("CREDENTIAL_DISCLOSURE", 422)
    if len(canonical_bytes(list(messages))) > (
        128 * 1024 if tool_profile == "reader-v2" else 64 * 1024
    ):
        raise failure("HISTORY_LIMIT")
    outstanding: set[str] = set()
    seen: set[str] = set()
    for item in messages:
        if not isinstance(item, dict) or set(item) - {
            "role",
            "content",
            "tool_calls",
            "tool_call_id",
        }:
            raise failure("HISTORY_PROTOCOL")
        role, content = item.get("role"), item.get("content")
        if outstanding and role != "tool":
            raise failure("HISTORY_PROTOCOL")
        if role not in {"user", "assistant", "tool"} or not isinstance(content, str):
            raise failure("HISTORY_PROTOCOL")
        maximum = 64 * 1024 if tool_profile == "reader-v2" and role == "tool" else 8192
        if len(content.encode()) > maximum:
            raise failure("HISTORY_LIMIT")
        if role == "tool":
            if (
                set(item) != {"role", "content", "tool_call_id"}
                or not isinstance(item["tool_call_id"], str)
                or item["tool_call_id"] not in outstanding
            ):
                raise failure("HISTORY_PROTOCOL")
            outstanding.remove(item["tool_call_id"])
        elif "tool_call_id" in item:
            raise failure("HISTORY_PROTOCOL")
        if "tool_calls" in item:
            if (
                role != "assistant"
                or not isinstance(item["tool_calls"], list)
                or not 1 <= len(item["tool_calls"]) <= 8
            ):
                raise failure("HISTORY_PROTOCOL")
            if outstanding:
                raise failure("HISTORY_PROTOCOL")
            for call in item["tool_calls"]:
                if (
                    not isinstance(call, dict)
                    or set(call) != {"id", "type", "function"}
                    or call["type"] != "function"
                    or not isinstance(call["id"], str)
                    or not 1 <= len(call["id"]) <= 128
                    or call["id"] in seen
                    or not isinstance(call["function"], dict)
                    or set(call["function"]) != {"name", "arguments"}
                    or call["function"]["name"] not in allowed
                    or call["function"]["arguments"] != "{}"
                ):
                    raise failure("HISTORY_PROTOCOL")
                outstanding.add(call["id"])
                seen.add(call["id"])
    if outstanding:
        raise failure("HISTORY_PROTOCOL")
    # Data-only defensive copy; callers cannot mutate the prepared wire later.
    return parse_document(canonical_bytes(list(messages)).decode())
