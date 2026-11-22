"""Workflow graph parsing, DAG validation and topological ordering.

The graph uses the React Flow shape: ``{"nodes": [...], "edges": [...]}``.
Validation produces structured issues (errors block execution; warnings inform).
"""

from __future__ import annotations

from collections import deque

from pydantic import ValidationError

from app.workflow.catalog import (
    COMPATIBLE_KINDS,
    KIND_FRAME,
    KIND_MODEL,
    get_spec,
)
from app.workflow.node_parameters import normalize_node_data


def parse_graph(graph: dict) -> tuple[list[dict], list[dict]]:
    nodes = graph.get("nodes", []) or []
    edges = graph.get("edges", []) or []
    return nodes, edges


def validate_graph(
    graph: dict, known_columns: list[str] | dict[str, list[str]] | None = None
) -> dict:
    """Validate a workflow graph. Returns {valid, issues, topo_order}."""
    nodes, edges = parse_graph(graph)
    issues: list[dict] = []

    def error(code: str, message: str, node_id: str | None = None) -> None:
        issues.append({"level": "error", "code": code, "message": message, "node_id": node_id})

    def warning(code: str, message: str, node_id: str | None = None) -> None:
        issues.append({"level": "warning", "code": code, "message": message, "node_id": node_id})

    if not nodes:
        error("EMPTY_GRAPH", "The workflow has no nodes.")
        return {"valid": False, "issues": issues, "topo_order": []}

    if graph.get("schema_version", 1) not in {1, 2}:
        error("SCHEMA_VERSION_UNSUPPORTED", "Workflow schema version is unsupported.")
    if len(nodes) > 100 or len(edges) > 300:
        error("GRAPH_RESOURCE_LIMIT", "Workflow permits at most 100 nodes and 300 connections.")
    if any(
        not isinstance(node, dict)
        or not isinstance(node.get("id"), str)
        or not node.get("id")
        or not isinstance(node.get("type"), str)
        or not isinstance(node.get("data", {}), dict)
        for node in nodes
    ):
        error(
            "NODE_SHAPE_INVALID", "Each node needs a unique string ID, type and parameter object."
        )
        return {"valid": False, "issues": issues, "topo_order": []}
    if any(
        not isinstance(edge, dict)
        or not isinstance(edge.get("source"), str)
        or not isinstance(edge.get("target"), str)
        for edge in edges
    ):
        error("EDGE_SHAPE_INVALID", "Each connection needs source and target IDs.")
        return {"valid": False, "issues": issues, "topo_order": []}
    for node in nodes:
        if get_spec(node["type"]) is None:
            continue
        try:
            normalize_node_data(
                node["type"], node.get("data", {}), legacy=graph.get("schema_version", 1) == 1
            )
        except (ValueError, ValidationError) as exc:
            # Do not echo arbitrary values (e.g. pasted code or credentials).
            detail = (
                "; ".join(
                    ".".join(str(part) for part in item["loc"]) + ": " + item["type"]
                    for item in exc.errors(include_input=False)
                )
                if isinstance(exc, ValidationError)
                else "Invalid node parameters."
            )
            error("NODE_PARAMETERS_INVALID", detail[:1000], node["id"])

    # Duplicate node ids.
    ids = [n["id"] for n in nodes]
    seen: set[str] = set()
    for nid in ids:
        if nid in seen:
            error("DUPLICATE_NODE_ID", f"Duplicate node id '{nid}'.", nid)
        seen.add(nid)

    node_by_id = {n["id"]: n for n in nodes}

    # Unknown node types.
    for n in nodes:
        if get_spec(n.get("type", "")) is None:
            error("UNKNOWN_NODE_TYPE", f"Unknown node type '{n.get('type')}'.", n.get("id"))

    # Must have at least one data source (dataset node).
    sources = [n for n in nodes if n.get("type") == "dataset"]
    if not sources:
        error("NO_SOURCE", "The workflow needs at least one Dataset source node.")

    # Build adjacency + in-degree (only between known nodes).
    adjacency: dict[str, list[str]] = {nid: [] for nid in ids}
    in_degree: dict[str, int] = {nid: 0 for nid in ids}
    incoming: dict[str, list[dict]] = {nid: [] for nid in ids}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s not in seen or t not in seen:
            error("DANGLING_EDGE", f"Edge references missing node ({s} -> {t}).")
            continue
        adjacency[s].append(t)
        in_degree[t] += 1
        incoming[t].append(e)

    # Cycle detection + topological order (Kahn).
    queue = deque([nid for nid, d in in_degree.items() if d == 0])
    topo_order: list[str] = []
    while queue:
        nid = queue.popleft()
        topo_order.append(nid)
        for nxt in adjacency[nid]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    if len(topo_order) != len(seen):
        cyclic = [nid for nid in seen if nid not in set(topo_order)]
        error("CYCLE_DETECTED", f"The workflow contains a cycle involving: {', '.join(cyclic)}.")

    # Reachability from sources.
    reachable: set[str] = set()
    dq = deque([n["id"] for n in sources])
    while dq:
        nid = dq.popleft()
        if nid in reachable:
            continue
        reachable.add(nid)
        for nxt in adjacency.get(nid, []):
            dq.append(nxt)
    for n in nodes:
        nid = n["id"]
        if nid not in reachable and n.get("type") != "dataset":
            warning(
                "UNREACHABLE_NODE",
                f"Node '{n.get('type')}' is not reachable from any Dataset source.",
                nid,
            )

    # Orphan nodes: non-source node with no incoming edges.
    for n in nodes:
        nid = n["id"]
        spec = get_spec(n.get("type", ""))
        if spec and spec.inputs and len(incoming.get(nid, [])) == 0:
            warning("ORPHAN_NODE", f"Node '{n.get('type')}' has no input connection.", nid)

    # Edge kind compatibility + required-input satisfaction.
    for e in edges:
        s, t = e.get("source"), e.get("target")
        s_spec = get_spec(node_by_id.get(s, {}).get("type", "")) if s in node_by_id else None
        t_spec = get_spec(node_by_id.get(t, {}).get("type", "")) if t in node_by_id else None
        if not s_spec or not t_spec:
            continue
        out_kind = s_spec.outputs[0] if s_spec.outputs else None
        in_kinds = set(t_spec.inputs)
        if out_kind and in_kinds and out_kind not in in_kinds:
            allowed = COMPATIBLE_KINDS.get(out_kind, set())
            if not (allowed & in_kinds):
                error(
                    "INCOMPATIBLE_CONNECTION",
                    f"Cannot connect '{s_spec.label}' ({out_kind}) to '{t_spec.label}' "
                    f"(expects {sorted(in_kinds)}).",
                    t,
                )

    for node in nodes:
        spec = get_spec(node["type"])
        links = incoming.get(node["id"], [])
        if spec and spec.inputs and not links:
            error("INPUT_REQUIRED", "Connect this node to its required input.", node["id"])
        if node["type"] == "dataset" and links:
            error(
                "SOURCE_HAS_INPUT", "Dataset source cannot have incoming connections.", node["id"]
            )
        predecessor_specs = [get_spec(node_by_id[link["source"]]["type"]) for link in links]
        kinds = [set(item.outputs) for item in predecessor_specs if item is not None]
        if sum(KIND_FRAME in outputs for outputs in kinds) > 1:
            error(
                "AMBIGUOUS_FRAME_INPUT",
                "A node can consume one data frame; join is not declared.",
                node["id"],
            )
        if node["type"] in {"evaluation", "interpretation"} and not any(
            KIND_MODEL in outputs for outputs in kinds
        ):
            error(
                "EVALUATION_NEEDS_MODEL"
                if node["type"] == "evaluation"
                else "INTERPRETATION_NEEDS_MODEL",
                "Connect this step to a saved Model output.",
                node["id"],
            )
        if node["type"] == "model" and not any(KIND_FRAME in outputs for outputs in kinds):
            error("MODEL_NEEDS_FRAME", "Model needs a data/preprocessing source.", node["id"])

    # Column references exist in the dataset schema (when known).
    if known_columns is not None:
        for n in nodes:
            columns = (
                known_columns.get(n["id"], []) if isinstance(known_columns, dict) else known_columns
            )
            cols = set(columns)
            data = n.get("data", {}) or {}
            refs: list[str] = []
            for key in ("columns", "variables", "predictors", "features", "categorical"):
                val = data.get(key)
                if isinstance(val, list):
                    refs.extend(val)
            for key in ("column", "variable", "group", "target", "group_variable", "time_variable"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    refs.append(val)
            for ref in refs:
                if ref not in cols:
                    error(
                        "UNKNOWN_COLUMN",
                        f"Node '{n.get('type')}' references unknown column '{ref}'.",
                        n["id"],
                    )

    valid = not any(i["level"] == "error" for i in issues)
    return {"valid": valid, "issues": issues, "topo_order": topo_order}
