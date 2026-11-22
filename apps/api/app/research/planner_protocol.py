"""Planner-only tool schemas; original formal tool authority is unchanged."""

from app.research.contracts import canonical_bytes, parse_document
from app.research.egress import failure

NAMES = frozenset({"inspect", "profile", "propose_plan", "write_todos"})


def assert_tools(tools):
    try:
        value = parse_document(canonical_bytes(list(tools)).decode())
        if len(canonical_bytes(value)) > 64 * 1024 or len(value) != 4:
            raise ValueError("Bounded schema set.")
        names = []
        for tool in value:
            if set(tool) != {"type", "function"} or tool["type"] != "function":
                raise ValueError("Tool envelope.")
            function = tool["function"]
            if (
                not isinstance(function, dict)
                or set(function) - {"name", "description", "parameters", "strict"}
                or not isinstance(function.get("parameters"), dict)
                or function["parameters"].get("type") != "object"
            ):
                raise ValueError("Typed tool contract.")
            names.append(function["name"])
        if len(set(names)) != 4 or set(names) != NAMES:
            raise ValueError("Planner capabilities.")
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure("PLANNER_TOOL_DENIED") from None


def assert_tools_v2(tools):
    from app.research.planner_admission import schemas_v2

    value = assert_tools(tools)
    if value != schemas_v2():
        raise failure("PLANNER_TOOL_DENIED")
    return value
