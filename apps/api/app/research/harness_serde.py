"""Data-only public SDK serializer; no imports, pickle or permissive msgpack revival."""

import json
import math
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from langgraph.types import Command, Interrupt, Send

from app.research.contracts import parse_document
from app.research.egress import credentials
from app.services import research_service as ledger

MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 40
MAX_ITEMS = 20_000
_MESSAGES = {
    kind.__name__: kind
    for kind in (AIMessage, HumanMessage, SystemMessage, ToolMessage, RemoveMessage)
}


class SafeSerializer:
    """Encode the concrete whitelist as tagged JSON, via SerializerProtocol.

    Every dictionary is a tagged sequence of pairs, so untrusted dicts cannot
    impersonate a constructor envelope. Constructors are static safe containers.
    """

    def __init__(self, owned_secrets=()):
        self.owned_secrets = tuple(owned_secrets)

    def dumps_typed(self, value: Any) -> tuple[str, bytes]:
        counter = [0]
        encoded = self._encode(value, 0, counter)
        content = json.dumps(
            encoded, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(content) > MAX_BYTES:
            raise ledger.error("HARNESS_CONTENT_LIMIT")
        return "statforge-json-v1", content

    def loads_typed(self, value: tuple[str, bytes]) -> Any:
        kind, content = value
        if kind != "statforge-json-v1" or type(content) is not bytes or len(content) > MAX_BYTES:
            raise ledger.error("HARNESS_SERIALIZATION_REFUSED")
        try:
            # parse_document additionally rejects duplicate keys/non-finite values.
            result = self._decode(parse_document(content.decode("utf-8")), 0, [0])
            self.dumps_typed(result)  # Exact whitelist and secret validation also on read.
            return result
        except (UnicodeError, ValueError, TypeError, KeyError, IndexError):
            raise ledger.error("HARNESS_SERIALIZATION_REFUSED") from None

    def check(self, value: Any) -> None:
        self.dumps_typed(value)

    def _bound(self, depth, counter):
        counter[0] += 1
        if depth > MAX_DEPTH or counter[0] > MAX_ITEMS:
            raise ledger.error("HARNESS_CONTENT_LIMIT")

    def _encode(self, value, depth, counter):
        self._bound(depth, counter)
        if value is None or type(value) in {str, int, float, bool}:
            if (type(value) is float and not math.isfinite(value)) or credentials(
                value, self.owned_secrets
            ):
                raise ledger.error("HARNESS_SERIALIZATION_REFUSED")
            return ["p", value]
        if type(value) is dict:
            if any(type(key) is not str for key in value) or credentials(value, self.owned_secrets):
                raise ledger.error("HARNESS_SERIALIZATION_REFUSED")
            return [
                "d",
                [[key, self._encode(item, depth + 1, counter)] for key, item in value.items()],
            ]
        if type(value) is _DeltaSnapshot:
            return ["ds", self._encode(value.value, depth + 1, counter)]
        if type(value) in {list, tuple}:
            return [
                "l" if type(value) is list else "t",
                [self._encode(item, depth + 1, counter) for item in value],
            ]
        if type(value) in _MESSAGES.values():
            return [
                "m",
                type(value).__name__,
                self._encode(value.model_dump(mode="python"), depth + 1, counter),
            ]
        if type(value) is Interrupt:
            if value.response_schema is not None:
                raise ledger.error("HARNESS_SERIALIZATION_REFUSED")
            return ["i", self._encode({"value": value.value, "id": value.id}, depth + 1, counter)]
        if type(value) is Send:
            return ["s", self._encode({"node": value.node, "arg": value.arg}, depth + 1, counter)]
        if type(value) is Command:
            return [
                "c",
                self._encode(
                    {
                        "graph": value.graph,
                        "update": value.update,
                        "resume": value.resume,
                        "goto": value.goto,
                    },
                    depth + 1,
                    counter,
                ),
            ]
        raise ledger.error("HARNESS_SERIALIZATION_REFUSED")

    def _decode(self, value, depth, counter):
        self._bound(depth, counter)
        if type(value) is not list or len(value) not in {2, 3} or type(value[0]) is not str:
            raise ValueError("Unknown serializer envelope.")
        tag = value[0]
        if tag == "ds" and len(value) == 2:
            return _DeltaSnapshot(self._decode(value[1], depth + 1, counter))
        if tag == "p" and len(value) == 2:
            if value[1] is not None and type(value[1]) not in {str, int, float, bool}:
                raise ValueError("Unknown scalar.")
            return value[1]
        if tag in {"d", "l", "t"} and len(value) == 2 and type(value[1]) is list:
            if tag == "d":
                result = {}
                for pair in value[1]:
                    if (
                        type(pair) is not list
                        or len(pair) != 2
                        or type(pair[0]) is not str
                        or pair[0] in result
                    ):
                        raise ValueError("Unknown dictionary.")
                    result[pair[0]] = self._decode(pair[1], depth + 1, counter)
                return result
            items = [self._decode(item, depth + 1, counter) for item in value[1]]
            return items if tag == "l" else tuple(items)
        if tag == "m" and len(value) == 3 and value[1] in _MESSAGES:
            return _MESSAGES[value[1]].model_validate(self._decode(value[2], depth + 1, counter))
        if tag in {"i", "s", "c"} and len(value) == 2:
            fields = self._decode(value[1], depth + 1, counter)
            expected = {
                "i": {"value", "id"},
                "s": {"node", "arg"},
                "c": {"graph", "update", "resume", "goto"},
            }[tag]
            if type(fields) is not dict or set(fields) != expected:
                raise ValueError("Unknown SDK container.")
            constructor = {"i": Interrupt, "s": Send, "c": Command}[tag]
            return constructor(**fields)
        raise ValueError("Unknown serializer tag.")
