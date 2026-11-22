"""Versioned canonical content and the sole formal Run/Attempt state graph."""

import hashlib
import json
from enum import StrEnum
from typing import Any

HASH_VERSION = "research-logical-v1"
ZERO_HASH = "0" * 64
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


class State(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL = frozenset({State.COMPLETED, State.FAILED, State.CANCELLED, State.INTERRUPTED})
RETRYABLE = frozenset({State.FAILED, State.CANCELLED, State.INTERRUPTED})
MAX_ATTEMPTS = 5
TRANSITIONS: dict[State, frozenset[State]] = {
    State.QUEUED: frozenset({State.RUNNING, State.CANCELLED, State.INTERRUPTED}),
    State.RUNNING: frozenset(
        {
            State.WAITING_APPROVAL,
            State.PAUSED,
            State.CANCEL_REQUESTED,
            State.COMPLETED,
            State.FAILED,
            State.INTERRUPTED,
        }
    ),
    State.WAITING_APPROVAL: frozenset(
        {
            State.RUNNING,
            State.CANCELLED,
            State.INTERRUPTED,
        }
    ),
    State.PAUSED: frozenset(
        {State.RUNNING, State.CANCEL_REQUESTED, State.CANCELLED, State.INTERRUPTED}
    ),
    State.CANCEL_REQUESTED: frozenset({State.CANCELLED, State.FAILED, State.INTERRUPTED}),
    **{state: frozenset() for state in TERMINAL},
}


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON only, with no coerced objects, NaN or ambiguous map keys.

    Callers construct a semantic document explicitly. Runtime timestamps, random
    record IDs and host paths belong to its envelope, never a global key filter
    that could accidentally erase a real method parameter.
    """

    def validate(item: Any) -> None:
        if item is None or type(item) in {str, int, float, bool}:
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise ValueError("Not a canonical JSON document.")

    validate(value)
    content = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Research document exceeds the bounded content limit.")
    return content


def logical_hash(value: Any) -> str:
    return hashlib.sha256(HASH_VERSION.encode() + b"\x00" + canonical_bytes(value)).hexdigest()


def parse_document(content: str) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("Research document exceeds the bounded content limit.")
    result = json.loads(content, object_pairs_hook=unique_pairs)
    canonical_bytes(result)
    return result


def require_transition(before: State, after: State, *, completion_proven: bool = False) -> None:
    """Used by the transactional service; no HTTP client supplies completion proof."""
    if after not in TRANSITIONS[before]:
        raise ValueError("Illegal research state transition.")
    if after is State.COMPLETED and not completion_proven:
        raise ValueError("A real domain result and verified Artifact are required.")


def event_hash(
    workspace_id: str,
    sequence: int,
    previous_hash: str,
    entity_kind: str,
    entity_id: str,
    event_type: str,
    payload_hash: str,
    actor_id: str,
    occurred_at: str,
) -> str:
    """Bind semantic payload to the physical ordered audit envelope."""
    return logical_hash(
        {
            "workspace_id": workspace_id,
            "sequence": sequence,
            "previous_hash": previous_hash,
            "entity_kind": entity_kind,
            "entity_id": entity_id,
            "event_type": event_type,
            "payload_hash": payload_hash,
            "actor_id": actor_id,
            "occurred_at": occurred_at,
        }
    )
