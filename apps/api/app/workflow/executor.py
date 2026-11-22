"""Workflow execution: run nodes in topological order over a flowing dataframe.

Data/cleaning nodes transform a Polars frame; statistics/ML nodes invoke the
deterministic engines; output nodes capture results. Each node records its own
status, summary and (for failures) error — a failed node skips its dependents
but never crashes the whole run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import polars as pl

from app.schemas.statistics import StatisticalRunRequest
from app.statistics.context import MethodConfig
from app.statistics.service import run_statistical_analysis
from app.statistics.types import MethodId
from app.workflow.catalog import AVAILABLE_NODES, KIND_FRAME, KIND_RESULT, get_spec
from app.workflow.error_codes import node_error_code
from app.workflow.graph import validate_graph

# Maps workflow statistics node types → statistics MethodId.
STAT_NODE_METHODS = {
    "descriptive": MethodId.DESCRIPTIVE,
    "correlation": MethodId.PEARSON,
    "chi_square": MethodId.CHI_SQUARE_INDEPENDENCE,
    "anova": MethodId.ONE_WAY_ANOVA,
    "group_comparison": MethodId.WELCH_T,
}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def _run_stat_node(node_type: str, frame: pl.DataFrame, data: dict) -> dict:
    """Run a statistics-engine method on the flowing frame."""
    if node_type == "regression":
        kind = data.get("kind", "linear_regression")
        methods = {
            "logistic": MethodId.LOGISTIC_REGRESSION,
            "logistic_regression": MethodId.LOGISTIC_REGRESSION,
            "linear": MethodId.LINEAR_REGRESSION,
            "linear_regression": MethodId.LINEAR_REGRESSION,
        }
        method_id = methods[kind]
        variables = _as_list(data.get("predictors"))
    else:
        method_id = STAT_NODE_METHODS[node_type]
        choices = {
            "correlation": {"pearson", "spearman", "kendall"},
            "group_comparison": {"welch_t", "mann_whitney"},
        }
        if node_type in choices:
            selected = data.get("method", method_id.value)
            if selected not in choices[node_type]:
                raise ValueError("Unsupported method for this node.")
            method_id = MethodId(selected)
        variables = _as_list(data.get("variables") or data.get("variable"))
    request = StatisticalRunRequest(
        method_id=method_id,
        variables=variables,
        target=data.get("target"),
        categorical_variables=_as_list(data.get("categorical")),
        group_variable=data.get("group"),
        alternative=data.get("alternative", "two_sided"),
        confidence_level=data.get("confidence_level", 0.95),
        missing_value_policy=data.get("missing_value_policy", "listwise_delete"),
        standardize=data.get("standardize", False),
        alpha=data.get("alpha", 1.0),
        l1_ratio=data.get("l1_ratio", 0.5),
    )
    config = MethodConfig(
        method_id=request.method_id,
        variables=tuple(request.variables),
        group_variable=request.group_variable,
        target=request.target,
        categorical_variables=tuple(request.categorical_variables),
        alternative=request.alternative,
        confidence_level=request.confidence_level,
        missing_policy=request.missing_value_policy,
        standardize=request.standardize,
        alpha=request.alpha,
        l1_ratio=request.l1_ratio,
    )
    result = run_statistical_analysis(frame, config)
    output = result.to_dict()
    output["input_config"] = config.to_dict()
    return output


def _run_ml_node(node_type: str, frame: pl.DataFrame, data: dict) -> dict:
    raise ValueError("WORKFLOW_DOMAIN_RUNTIME_REQUIRED")


def _apply_cleaning(node_type: str, frame: pl.DataFrame, data: dict) -> pl.DataFrame:
    if node_type == "column_select":
        cols = _as_list(data.get("columns"))
        if not cols or not set(cols) <= set(frame.columns):
            raise ValueError("Select existing columns explicitly.")
        return frame.select(cols)
    if node_type == "duplicate_handling":
        return frame.unique(keep=data.get("keep", "any"))
    if node_type == "missing_value":
        strategy = data.get("strategy", "drop")
        if strategy == "drop":
            return frame.drop_nulls()
        if strategy != "mean":
            raise ValueError("Unsupported missing-value strategy.")
        # mean impute numeric columns
        exprs = [
            pl.col(c).fill_null(pl.col(c).mean()) if frame[c].dtype.is_numeric() else pl.col(c)
            for c in frame.columns
        ]
        return frame.with_columns(exprs)
    if node_type == "filter":
        col, op, val = data.get("column"), data.get("operator", "eq"), data.get("value")
        if col not in frame.columns:
            raise ValueError("Filter column unavailable.")
        if op not in {"eq", "ne", "gt", "lt"}:
            raise ValueError("Unsupported filter operator.")
        s = frame[col]
        try:
            if op == "gt":
                return frame.filter(s > float(data["value"]))
            if op == "lt":
                return frame.filter(s < float(data["value"]))
            if s.dtype.is_numeric() and isinstance(val, (int, float)) and not isinstance(val, bool):
                return frame.filter(s != val if op == "ne" else s == val)
            if s.dtype == pl.Boolean and isinstance(val, bool):
                return frame.filter(s != val if op == "ne" else s == val)
            if op == "ne":
                return frame.filter(s.cast(pl.String) != str(val))
            return frame.filter(s.cast(pl.String) == str(val))
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Filter input invalid.") from exc
    if node_type == "sample":
        frac = float(data.get("fraction", 0.5))
        if not 0 < frac <= 1:
            raise ValueError("Sample fraction must be in (0, 1].")
        return frame.sample(fraction=frac, seed=int(data.get("seed", 42)))
    if node_type == "type_conversion":
        col, to_type = data.get("column"), data.get("to_type", "float")
        if col not in frame.columns or to_type not in {"float", "string", "integer"}:
            raise ValueError("Conversion input unavailable.")
        if col in frame.columns:
            dtype = (
                pl.Float64 if to_type == "float" else pl.String if to_type == "string" else pl.Int64
            )
            return frame.with_columns(pl.col(col).cast(dtype, strict=False))
    if node_type == "outlier_strategy":
        if data.get("method", "winsorize") != "winsorize":
            raise ValueError("Unsupported outlier strategy.")
        # Winsorize numeric columns to [1st, 99th] percentile.
        exprs = []
        for c in frame.columns:
            if frame[c].dtype.is_numeric():
                lo = frame[c].quantile(0.01)
                hi = frame[c].quantile(0.99)
                exprs.append(pl.col(c).clip(lo, hi))
        return frame.with_columns(exprs) if exprs else frame
    raise ValueError("Transformation unavailable.")


def execute_graph(graph: dict, load_frame: Callable[[str], pl.DataFrame]) -> dict:
    """Execute a validated workflow graph. Returns per-node statuses + outputs."""
    validation = validate_graph(graph)
    if not validation["valid"]:
        return {
            "status": "failed",
            "error": "Graph validation failed",
            "issues": validation["issues"],
            "node_results": {
                node["id"]: {
                    "status": "failed"
                    if any(
                        issue.get("node_id") == node["id"] and issue["level"] == "error"
                        for issue in validation["issues"]
                    )
                    else "skipped",
                    "node_type": node.get("type"),
                    "error": "Graph validation blocked execution",
                    "error_code": "WORKFLOW_GRAPH_INVALID",
                    "summary": "",
                }
                for node in graph.get("nodes", [])
                if isinstance(node, dict) and isinstance(node.get("id"), str)
            },
        }

    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    node_by_id = {n["id"]: n for n in nodes}
    incoming: dict[str, list[dict]] = {n["id"]: [] for n in nodes}
    dependents: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        if e.get("target") in incoming:
            incoming[e["target"]].append(e)
        if e.get("source") in dependents:
            dependents[e["source"]].append(e["target"])

    topo_order = validation["topo_order"]
    outputs: dict[str, Any] = {}
    node_results: dict[str, dict] = {}
    skipped: set[str] = set()
    overall_ok = True
    started = time.time()

    for nid in topo_order:
        node = node_by_id[nid]
        spec = get_spec(node.get("type", ""))
        n_start = time.time()

        if any(src in skipped for src in [e["source"] for e in incoming.get(nid, [])]):
            skipped.add(nid)
            node_results[nid] = {
                "status": "skipped",
                "node_type": node.get("type"),
                "summary": "Skipped (upstream failed).",
                "seconds": 0.0,
            }
            continue

        try:
            data = node.get("data", {}) or {}
            # Resolve the primary frame input from predecessors.
            frame_in = None
            for e in incoming.get(nid, []):
                src_out = outputs.get(e["source"])
                src_spec = get_spec(node_by_id[e["source"]].get("type", ""))
                kind = src_spec.outputs[0] if src_spec and src_spec.outputs else None
                if kind == KIND_FRAME:
                    frame_in = src_out

            node_type = node.get("type", "")
            if node_type not in AVAILABLE_NODES:
                raise ValueError("WORKFLOW_CAPABILITY_UNAVAILABLE")
            allowed_params = set(spec.params if spec else ())
            if node_type in STAT_NODE_METHODS or node_type == "regression":
                allowed_params |= {
                    "alternative",
                    "confidence_level",
                    "missing_value_policy",
                    "standardize",
                    "alpha",
                    "l1_ratio",
                    "categorical",
                }
            if node_type == "cross_validation":
                allowed_params |= {"task_type", "categorical"}
            if not set(data) <= allowed_params:
                raise ValueError("WORKFLOW_PARAMETER_UNSUPPORTED")
            summary = ""
            out_kind = spec.outputs[0] if spec and spec.outputs else None

            if node_type == "dataset":
                ds_id = data.get("dataset_id", "")
                frame = load_frame(ds_id)
                outputs[nid] = frame
                summary = f"Loaded {frame.height} rows × {frame.width} cols"
            elif spec and KIND_FRAME in (spec.inputs or ()) and out_kind == KIND_FRAME:
                if frame_in is None:
                    raise ValueError("No input data frame.")
                frame = _apply_cleaning(node_type, frame_in, data)
                outputs[nid] = frame
                summary = f"{frame.height} rows × {frame.width} cols"
            elif node_type in STAT_NODE_METHODS or node_type == "regression":
                if frame_in is None:
                    raise ValueError("Statistics node needs an input data frame.")
                result = _run_stat_node(node_type, frame_in, data)
                if result.get("status") != "completed":
                    raise ValueError("WORKFLOW_DOMAIN_NOT_COMPLETED")
                outputs[nid] = result
                summary = f"{result.get('status')}: {node_type}"
            elif node_type == "cross_validation":
                if frame_in is None:
                    raise ValueError("ML node needs an input data frame.")
                result = _run_ml_node(node_type, frame_in, data)
                if result.get("status") != "completed":
                    raise ValueError("WORKFLOW_DOMAIN_NOT_COMPLETED")
                outputs[nid] = result
                summary = f"ML {node_type}: completed"
            else:
                raise ValueError("WORKFLOW_CAPABILITY_UNAVAILABLE")

            node_results[nid] = {
                "status": "completed",
                "node_type": node_type,
                "output_kind": out_kind,
                "summary": summary,
                "seconds": round(time.time() - n_start, 3),
                "output": outputs[nid] if out_kind == KIND_RESULT else None,
            }
        except Exception as exc:  # noqa: BLE001 - isolate per-node failures
            overall_ok = False
            skipped.add(nid)
            node_results[nid] = {
                "status": "failed",
                "node_type": node.get("type"),
                "summary": "",
                "error": f"{type(exc).__name__}: {exc}"[:1000],
                "error_code": node_error_code(exc),
                "seconds": round(time.time() - n_start, 3),
            }

    status = "completed" if overall_ok else "failed"
    return {
        "status": status,
        "node_results": node_results,
        "order": topo_order,
        "total_seconds": round(time.time() - started, 3),
    }
