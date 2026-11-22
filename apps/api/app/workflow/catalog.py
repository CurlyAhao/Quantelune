"""Workflow node catalog: node types, categories, and port (data-flow) types.

Each node declares its category, the parameters it accepts, and the kind of
value it consumes/produces. The graph validator uses the port kinds to reject
incompatible connections; the executor uses the catalog to run each node.
"""

from __future__ import annotations

from app.workflow.node_parameters import parameter_schema

# Port (data-flow) kinds. Edges must connect compatible kinds.
KIND_FRAME = "frame"  # a data table flowing through the pipeline
KIND_RESULT = "result"  # a statistical / ML result object
KIND_MODEL = "model"  # a trained model artifact
KIND_REPORT = "report"  # a rendered report / figure / export

AVAILABLE_NODES = frozenset(
    {
        "dataset",
        "column_select",
        "filter",
        "sample",
        "missing_value",
        "type_conversion",
        "duplicate_handling",
        "outlier_strategy",
        "descriptive",
        "correlation",
        "group_comparison",
        "chi_square",
        "anova",
        "regression",
        "cross_validation",
    }
)

CATEGORY_LABELS = {
    "data": "Data",
    "cleaning": "Cleaning",
    "statistics": "Statistics",
    "ml": "Machine Learning",
    "output": "Output",
}


class NodeSpec:
    def __init__(
        self,
        node_type: str,
        label: str,
        category: str,
        inputs: tuple[str, ...],
        outputs: tuple[str, ...],
        description: str = "",
        params: tuple[str, ...] = (),
    ) -> None:
        self.node_type = node_type
        self.label = label
        self.category = category
        self.inputs = inputs
        self.outputs = outputs
        self.description = description
        self.params = params

    def to_dict(self) -> dict:
        return {
            "node_type": self.node_type,
            "label": self.label,
            "category": self.category,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "description": self.description,
            "params": list(parameter_schema(self.node_type)["properties"]),
            "parameter_schema": parameter_schema(self.node_type),
            "available": self.node_type in AVAILABLE_NODES,
            "availability_reason": None
            if self.node_type in AVAILABLE_NODES
            else "No implemented output contract.",
        }


# The full node catalog. `inputs`/`outputs` use the KIND_* port kinds; an empty
# input tuple marks a source node (e.g. a dataset loader).
NODE_CATALOG: dict[str, NodeSpec] = {
    # Data
    "dataset": NodeSpec(
        "dataset",
        "Dataset",
        "data",
        (),
        (KIND_FRAME,),
        "Load a project dataset as the pipeline source.",
        ("dataset_id",),
    ),
    "column_select": NodeSpec(
        "column_select",
        "Select Columns",
        "data",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Keep only the chosen columns.",
        ("columns",),
    ),
    "filter": NodeSpec(
        "filter",
        "Filter Rows",
        "data",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Filter rows by a simple column condition.",
        ("column", "operator", "value"),
    ),
    "sample": NodeSpec(
        "sample",
        "Sample",
        "data",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Randomly sample a fraction of rows.",
        ("fraction", "seed"),
    ),
    # Cleaning
    "missing_value": NodeSpec(
        "missing_value",
        "Handle Missing",
        "cleaning",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Drop or impute missing values.",
        ("strategy",),
    ),
    "type_conversion": NodeSpec(
        "type_conversion",
        "Convert Type",
        "cleaning",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Coerce a column to a target type.",
        ("column", "to_type"),
    ),
    "duplicate_handling": NodeSpec(
        "duplicate_handling",
        "Drop Duplicates",
        "cleaning",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Remove duplicate rows.",
        ("keep",),
    ),
    "outlier_strategy": NodeSpec(
        "outlier_strategy",
        "Outlier Strategy",
        "cleaning",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Winsorize outliers; flag strategy is unavailable.",
        ("method",),
    ),
    # Statistics (reuse the deterministic statistics engine)
    "descriptive": NodeSpec(
        "descriptive",
        "Descriptive Stats",
        "statistics",
        (KIND_FRAME,),
        (KIND_RESULT,),
        "Descriptive statistics for selected columns.",
        ("variables",),
    ),
    "correlation": NodeSpec(
        "correlation",
        "Correlation",
        "statistics",
        (KIND_FRAME,),
        (KIND_RESULT,),
        "Pearson/Spearman/Kendall correlation.",
        ("variables", "method"),
    ),
    "group_comparison": NodeSpec(
        "group_comparison",
        "Group Comparison",
        "statistics",
        (KIND_FRAME,),
        (KIND_RESULT,),
        "Two-group test (Welch/Mann-Whitney).",
        ("variable", "group", "method"),
    ),
    "chi_square": NodeSpec(
        "chi_square",
        "Chi-Square",
        "statistics",
        (KIND_FRAME,),
        (KIND_RESULT,),
        "Chi-square test of independence.",
        ("variables",),
    ),
    "anova": NodeSpec(
        "anova",
        "ANOVA",
        "statistics",
        (KIND_FRAME,),
        (KIND_RESULT,),
        "One-way ANOVA across groups.",
        ("variable", "group"),
    ),
    "regression": NodeSpec(
        "regression",
        "Regression",
        "statistics",
        (KIND_FRAME,),
        (KIND_RESULT,),
        "Linear/logistic regression.",
        ("target", "predictors", "kind"),
    ),
    # Machine learning (reuse the ML engine)
    "split": NodeSpec(
        "split",
        "Split",
        "ml",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Save actual train/validation/test row allocation for bounded model training.",
        ("test_size", "seed"),
    ),
    "preprocessing": NodeSpec(
        "preprocessing",
        "Preprocessing",
        "ml",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Configure imputation and scaling learned inside each training fold.",
        ("standardize",),
    ),
    "feature_selection": NodeSpec(
        "feature_selection",
        "Feature Selection",
        "ml",
        (KIND_FRAME,),
        (KIND_FRAME,),
        "Select K best encoded features inside each training fold.",
        ("k",),
    ),
    "model": NodeSpec(
        "model",
        "Model",
        "ml",
        (KIND_FRAME,),
        (KIND_MODEL,),
        "Train a model.",
        ("task_type", "target", "features", "model_id"),
    ),
    "cross_validation": NodeSpec(
        "cross_validation",
        "Cross-Validation",
        "ml",
        (KIND_FRAME,),
        (KIND_RESULT,),
        "Cross-validated score.",
        ("target", "features", "model_id", "n_splits"),
    ),
    "evaluation": NodeSpec(
        "evaluation",
        "Evaluation",
        "ml",
        (KIND_MODEL, KIND_FRAME),
        (KIND_RESULT,),
        "Read actual saved holdout/validation evaluation for the connected model.",
        (),
    ),
    "interpretation": NodeSpec(
        "interpretation",
        "Interpretation",
        "ml",
        (KIND_MODEL,),
        (KIND_RESULT,),
        "Feature importance of a model.",
        (),
    ),
    # Output
    "table": NodeSpec(
        "table",
        "Table",
        "output",
        (KIND_FRAME, KIND_RESULT),
        (KIND_REPORT,),
        "Render an input as a table.",
        (),
    ),
    "chart": NodeSpec(
        "chart",
        "Chart",
        "output",
        (KIND_RESULT,),
        (KIND_REPORT,),
        "Render a chart from a result.",
        ("chart_type",),
    ),
    "report": NodeSpec(
        "report",
        "Report",
        "output",
        (KIND_RESULT,),
        (KIND_REPORT,),
        "Compile results into a report section.",
        (),
    ),
    "notebook": NodeSpec(
        "notebook",
        "Notebook",
        "output",
        (KIND_RESULT,),
        (KIND_REPORT,),
        "Export steps as a notebook.",
        (),
    ),
    "export": NodeSpec(
        "export",
        "Export",
        "output",
        (KIND_REPORT,),
        (KIND_REPORT,),
        "Export the workflow outputs.",
        ("format",),
    ),
}

# All catalogued nodes have a domain runtime; the pure unit executor requires
# domain orchestration for ML and persistent outputs.
AVAILABLE_NODES = frozenset(NODE_CATALOG)

# Which port kinds can feed into which (source kind -> allowed target kinds).
COMPATIBLE_KINDS: dict[str, set[str]] = {
    KIND_FRAME: {KIND_FRAME},
    KIND_RESULT: {KIND_RESULT, KIND_REPORT},
    KIND_MODEL: {KIND_MODEL, KIND_RESULT},
    KIND_REPORT: {KIND_REPORT},
}


def get_spec(node_type: str) -> NodeSpec | None:
    return NODE_CATALOG.get(node_type)


def catalog_payload() -> dict:
    return {
        "categories": CATEGORY_LABELS,
        "nodes": [spec.to_dict() for spec in NODE_CATALOG.values()],
    }
