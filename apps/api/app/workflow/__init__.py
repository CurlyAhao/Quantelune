"""Visual analysis workflow engine: catalog, DAG validation, topological execution.

Workflows are DAGs of typed nodes (data / cleaning / statistics / ML / output).
The graph is validated for cycles, sources, port compatibility, orphan and
unreachable nodes before execution; nodes run in topological order and reuse the
deterministic statistics and ML engines.
"""

from app.workflow.catalog import catalog_payload
from app.workflow.executor import execute_graph
from app.workflow.graph import validate_graph

__all__ = ["catalog_payload", "validate_graph", "execute_graph"]
