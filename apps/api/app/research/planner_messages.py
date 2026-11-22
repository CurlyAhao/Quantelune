"""Private data-only tool-reply permutations; never an authorization proof."""

from app.research.contracts import canonical_bytes

MAX_MESSAGES = 32
MAX_PROJECTION_BYTES = 128 * 1024


def _groups(messages):
    if type(messages) is not list or not 0 < len(messages) <= MAX_MESSAGES:
        raise ValueError("Invalid message snapshot")
    if len(canonical_bytes(messages)) > MAX_PROJECTION_BYTES:
        raise ValueError("Message snapshot limit")
    for item in messages:
        if type(item) is not dict or type(item.get("content")) is not str:
            raise ValueError("Invalid message projection")
        role = item.get("role")
        fields = {"role", "content"}
        if role == "tool":
            fields.add("tool_call_id")
            identity = item.get("tool_call_id")
            if type(identity) is not str or not 1 <= len(identity) <= 128:
                raise ValueError("Invalid tool identity")
        elif role == "assistant" and "tool_calls" in item:
            fields.add("tool_calls")
        elif role not in {"user", "system", "assistant"}:
            raise ValueError("Invalid message role")
        if set(item) != fields:
            raise ValueError("Invalid message fields")
    groups, index = [], 0
    while index < len(messages):
        item = messages[index]
        if item["role"] == "tool":
            raise ValueError("Orphan tool reply")
        calls = item.get("tool_calls")
        if calls is None:
            index += 1
            continue
        if type(calls) is not list or not 1 <= len(calls) <= 8:
            raise ValueError("Invalid tool calls")
        identifiers = []
        for call in calls:
            if type(call) is not dict or set(call) != {"id", "type", "function"}:
                raise ValueError("Invalid tool call")
            function = call["function"]
            if (
                call["type"] != "function"
                or type(function) is not dict
                or set(function) != {"name", "arguments"}
                or type(function["name"]) is not str
                or not 1 <= len(function["name"]) <= 64
                or function["arguments"] != "{}"
                or type(call["id"]) is not str
                or not 1 <= len(call["id"]) <= 128
            ):
                raise ValueError("Invalid tool call projection")
            identifiers.append(call["id"])
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate tool call identity")
        end = index + 1
        while end < len(messages) and messages[end]["role"] == "tool":
            end += 1
        replies = [messages[i]["tool_call_id"] for i in range(index + 1, end)]
        if len(replies) != len(identifiers) or set(replies) != set(identifiers):
            raise ValueError("Incomplete or duplicate tool replies")
        groups.append(tuple(range(index + 1, end)))
        index = end
    return groups


def restore_tool_order(current, recorded):
    """Return full-snapshot indices matching one exact saved non-system history.

    Only intact replies inside their existing adjacent AI tool group can move.
    This does not establish raw arguments equal to any historical checkpoint.
    """
    current_groups = _groups(current)
    _groups(recorded)
    if any(item["role"] == "system" for item in recorded):
        raise ValueError("Recorded history must omit system scaffolding")
    non_system = [i for i, item in enumerate(current) if item["role"] != "system"]
    if len(non_system) != len(recorded):
        raise ValueError("History length changed")
    positions = {source: target for target, source in enumerate(non_system)}
    order = list(range(len(current)))
    tool_positions = {i for group in current_groups for i in group}
    for source in non_system:
        if source not in tool_positions:
            if canonical_bytes(current[source]) != canonical_bytes(recorded[positions[source]]):
                raise ValueError("Non-tool history changed")
    for group in current_groups:
        available = {current[i]["tool_call_id"]: i for i in group}
        for target in group:
            expected = recorded[positions[target]]
            source = available.get(expected.get("tool_call_id"))
            if source is None or canonical_bytes(current[source]) != canonical_bytes(expected):
                raise ValueError("Tool reply changed or crossed group")
            order[target] = source
    reordered = [current[order[i]] for i in non_system]
    if canonical_bytes(reordered) != canonical_bytes(recorded):
        raise ValueError("Recorded history mismatch")
    return tuple(order)


def validate_snapshot(projection, before, after, order):
    """Validate a complete unique permutation and every raw serialized message.

    Callers supply full SafeSerializer bytes; lossy protocol hashes are
    insufficient for this check, especially for raw AI tool-call arguments.
    """
    groups = _groups(projection)
    length = len(projection)
    if (
        type(order) is not tuple
        or len(order) != length
        or any(type(index) is not int for index in order)
        or set(order) != set(range(length))
        or type(before) is not tuple
        or type(after) is not tuple
        or len(before) != length
        or len(after) != length
        or any(type(value) is not bytes for value in (*before, *after))
    ):
        raise ValueError("Invalid complete snapshot permutation")
    if sum(map(len, before)) > 2 * 1024 * 1024:
        raise ValueError("Raw snapshot limit")
    permitted = {index: set(group) for group in groups for index in group}
    for target, source in enumerate(order):
        if source not in permitted.get(target, {target}) or after[target] != before[source]:
            raise ValueError("Raw message changed or crossed group")
