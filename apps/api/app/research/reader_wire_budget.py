"""Precommit Reader-v2 tool results against the original actual wire byte count.

No Provider/config/secret read or send. Fresh Gateway authorization still applies
to any next request. v2 wire carries history once, so its exact encoded delta
can be added to the byte count of the causally proved preceding request.
"""

from app.research.contracts import canonical_bytes
from app.research.egress import failure
from app.research.model_history import validate


def check(previous_bytes, history, reply, results):
    if type(previous_bytes) is not int or not 0 < previous_bytes <= 128000:
        raise failure("READER_WIRE_BUDGET")
    calls = reply.get("tool_calls", ())
    ids = [call["id"] for call in calls]
    if (
        reply.get("role") != "assistant"
        or not 1 <= len(calls) <= 8
        or len(set(ids)) != len(ids)
        or not set(results) <= set(ids)
    ):
        raise failure("READER_WIRE_BUDGET")
    future = [*history, reply]
    answer = False
    known = set(results)
    for call in calls:
        name, identifier = call["function"]["name"], call["id"]
        if name == "write_todos":
            content = '{"state":"reader_state_updated"}'
            known.add(identifier)
        elif name in {"result_facts", "explain_results"}:
            if identifier not in results:
                continue  # A later serial callback includes all committed sibling results.
            content = canonical_bytes(results[identifier]).decode()
            answer |= name == "explain_results"
        else:
            raise failure("READER_WIRE_BUDGET")
        if len(content.encode()) > 64 * 1024:
            raise failure("READER_WIRE_BUDGET")
        future.append(dict(role="tool", content=content, tool_call_id=identifier))
    encoded = canonical_bytes(future)
    if len(future) > 32 or len(encoded) > 128 * 1024:
        raise failure("READER_WIRE_BUDGET")
    if known == set(ids):
        validate(future, tool_profile="reader-v2")
    # The static system/user prefix contributes a comma before the first new
    # history item. An empty history array has no such comma of its own.
    next_bytes = (
        previous_bytes
        + len(encoded)
        - len(canonical_bytes(history))
        + (1 if not history and future else 0)
    )
    if not answer and next_bytes > 128000:
        raise failure("READER_WIRE_BUDGET")
    return {
        "projected_wire_bytes": next_bytes,
        "history_bytes": len(encoded),
        "comparison": "MATCHED",
        "status": "NOT_VERIFIED",
    }
