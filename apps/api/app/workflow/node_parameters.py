"""Pure typed parameters and UI schemas for the frozen 26-node Workflow catalog.

This layer validates configuration, not execution capability or authority. Data
binding, inherited ML inputs, estimator costs and actual column dtypes belong to
the coordinating services. Legacy conversion is explicit and never guesses a
column, changes an equality-filter literal, or drops unsupported parameters.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StrictBool,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

ColumnName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
Title = Annotated[str, StringConstraints(strict=True, max_length=160)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
UInt32 = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
Folds = Annotated[int, Field(strict=True, ge=2, le=10)]
TaskType = Literal["regression", "binary_classification", "multiclass_classification"]
SplitStrategy = Literal[
    "train_test", "train_val_test", "kfold", "stratified_kfold", "group_kfold", "time_series_split"
]
ModelId = Literal[
    "logistic_regression",
    "decision_tree",
    "random_forest",
    "gradient_boosting",
    "knn",
    "svm",
    "linear_regression",
    "ridge",
    "lasso",
    "elastic_net",
    "decision_tree_regressor",
    "random_forest_regressor",
    "gradient_boosting_regressor",
]
FilterScalar = (
    Annotated[str, StringConstraints(strict=True, max_length=255)]
    | StrictBool
    | Annotated[int, Field(strict=True)]
    | FiniteFloat
)
_REGRESSION_MODELS = {
    "linear_regression",
    "ridge",
    "lasso",
    "elastic_net",
    "decision_tree_regressor",
    "random_forest_regressor",
    "gradient_boosting_regressor",
}


def _column(kind: str = "any") -> dict[str, str]:
    return {"x-ui": "column", "x-column-kind": kind}


def _columns(kind: str = "any") -> dict[str, str]:
    return {"x-ui": "columns", "x-column-kind": kind}


AnyColumn = Annotated[ColumnName, Field(json_schema_extra=_column())]
NumericColumn = Annotated[ColumnName, Field(json_schema_extra=_column("numeric"))]
OptionalColumn = Annotated[ColumnName | None, Field(json_schema_extra=_column())]
RequiredColumns = Annotated[
    list[ColumnName], Field(strict=True, min_length=1, max_length=128, json_schema_extra=_columns())
]
NumericColumns = Annotated[
    list[ColumnName],
    Field(strict=True, min_length=1, max_length=128, json_schema_extra=_columns("numeric")),
]
OptionalColumns = Annotated[
    list[ColumnName], Field(strict=True, max_length=128, json_schema_extra=_columns())
]


class _NodeData(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)

    node_type: str
    label: Annotated[str, StringConstraints(strict=True, max_length=255)] | None = None

    @model_validator(mode="after")
    def _unique_columns(self):
        for name, information in type(self).model_fields.items():
            extra = information.json_schema_extra
            if isinstance(extra, dict) and extra.get("x-ui") == "columns":
                values = getattr(self, name)
                if len(values) != len(set(values)):
                    raise ValueError(f"Column list '{name}' contains duplicates.")
        return self


class DatasetData(_NodeData):
    node_type: Literal["dataset"]
    dataset_id: ColumnName | None = None


class ColumnSelectData(_NodeData):
    node_type: Literal["column_select"]
    columns: RequiredColumns


class FilterData(_NodeData):
    node_type: Literal["filter"]
    column: AnyColumn
    operator: Literal["eq", "ne", "gt", "lt"] = "eq"
    value: FilterScalar

    @model_validator(mode="after")
    def _ordered_value(self):
        if self.operator in {"gt", "lt"} and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError("Ordered filtering requires a finite numeric value.")
        return self


class SampleData(_NodeData):
    node_type: Literal["sample"]
    fraction: Annotated[FiniteFloat, Field(gt=0, le=1)] = 0.5
    seed: UInt32 = 42


class MissingValueData(_NodeData):
    node_type: Literal["missing_value"]
    strategy: Literal["drop", "mean"] = "drop"


class TypeConversionData(_NodeData):
    node_type: Literal["type_conversion"]
    column: AnyColumn
    to_type: Literal["float", "string", "integer"] = "float"


class DuplicateHandlingData(_NodeData):
    node_type: Literal["duplicate_handling"]
    keep: Literal["any", "first", "last", "none"] = "any"


class OutlierStrategyData(_NodeData):
    node_type: Literal["outlier_strategy"]
    method: Literal["winsorize"] = "winsorize"


class _StatisticalData(_NodeData):
    alternative: Literal["two_sided", "less", "greater"] = "two_sided"
    confidence_level: Annotated[FiniteFloat, Field(gt=0, lt=1)] = 0.95
    missing_value_policy: Literal["listwise_delete", "pairwise_delete", "error_on_missing"] = (
        "listwise_delete"
    )
    standardize: StrictBool = False
    alpha: Annotated[FiniteFloat, Field(ge=0)] = 1.0
    l1_ratio: Annotated[FiniteFloat, Field(ge=0, le=1)] = 0.5


class DescriptiveData(_StatisticalData):
    node_type: Literal["descriptive"]
    variables: RequiredColumns


class CorrelationData(_StatisticalData):
    node_type: Literal["correlation"]
    variables: NumericColumns
    method: Literal["pearson", "spearman", "kendall"] = "pearson"


class GroupComparisonData(_StatisticalData):
    node_type: Literal["group_comparison"]
    variable: NumericColumn
    group: AnyColumn
    method: Literal["welch_t", "mann_whitney"] = "welch_t"


class ChiSquareData(_StatisticalData):
    node_type: Literal["chi_square"]
    variables: Annotated[
        list[ColumnName],
        Field(strict=True, min_length=2, max_length=2, json_schema_extra=_columns()),
    ]


class AnovaData(_StatisticalData):
    node_type: Literal["anova"]
    variable: NumericColumn
    group: AnyColumn


class RegressionData(_StatisticalData):
    node_type: Literal["regression"]
    target: AnyColumn
    predictors: RequiredColumns
    categorical: OptionalColumns = Field(default_factory=list)
    kind: Literal["linear", "linear_regression", "logistic", "logistic_regression"] = (
        "linear_regression"
    )


class SplitData(_NodeData):
    node_type: Literal["split"]
    target: AnyColumn
    task_type: TaskType = "regression"
    test_size: Annotated[FiniteFloat, Field(gt=0, lt=0.9)] = 0.2
    val_size: Annotated[FiniteFloat, Field(ge=0, lt=0.8)] = 0.15
    seed: UInt32 = 42
    strategy: SplitStrategy = "train_test"
    n_splits: Folds = 5
    group_variable: OptionalColumn = None
    time_variable: OptionalColumn = None


class PreprocessingData(_NodeData):
    node_type: Literal["preprocessing"]
    standardize: StrictBool = True
    handle_outliers: StrictBool = False
    impute_numeric: Literal["median", "mean"] = "median"
    impute_categorical: Literal["most_frequent"] = "most_frequent"


class FeatureSelectionData(_NodeData):
    node_type: Literal["feature_selection"]
    k: Annotated[int, Field(strict=True, ge=1, le=512)] = 1


def _bounded_literal(value: Any, depth: int = 0) -> bool:
    """Mirror the existing A01 literal boundary, without estimator construction."""
    if depth > 2:
        return False
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, str):
        return len(value) <= 128
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return len(value) <= 32 and all(
            isinstance(key, (str, int))
            and (not isinstance(key, str) or len(key) <= 128)
            and _bounded_literal(item, depth + 1)
            for key, item in value.items()
        )
    return False


class _ModelData(_NodeData):
    target: OptionalColumn = None
    features: OptionalColumns = Field(default_factory=list)
    categorical: OptionalColumns = Field(default_factory=list)
    task_type: TaskType = "regression"
    model_id: ModelId = Field(
        default="ridge",
        description="Default to Ridge (regression) or Logistic Regression (classification).",
        json_schema_extra={
            "x-ui": "select",
            "x-default-by-task": {
                "regression": "ridge",
                "binary_classification": "logistic_regression",
                "multiclass_classification": "logistic_regression",
            },
        },
    )
    n_splits: Folds = 5
    split_strategy: SplitStrategy = "train_test"
    seed: UInt32 = 42
    test_size: Annotated[FiniteFloat, Field(gt=0, lt=0.9)] = 0.2
    val_size: Annotated[FiniteFloat, Field(ge=0, lt=0.8)] = 0.15
    group_variable: OptionalColumn = None
    time_variable: OptionalColumn = None
    model_params: dict[ModelId, dict[str, Any]] = Field(
        default_factory=dict, json_schema_extra={"maxProperties": 13, "x-ui": "json"}
    )
    standardize: StrictBool | None = None
    handle_outliers: StrictBool | None = None
    impute_numeric: Literal["median", "mean"] | None = None
    impute_categorical: Literal["most_frequent"] | None = None

    @model_validator(mode="wrap")
    @classmethod
    def _task_default_model(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        explicit_pair = isinstance(data, dict) and {"task_type", "model_id"} <= data.keys()
        if isinstance(data, dict) and "model_id" not in data:
            data = dict(data)
            data["model_id"] = (
                "ridge"
                if data.get("task_type", "regression") == "regression"
                else "logistic_regression"
            )
        model = handler(data)
        if explicit_pair and (model.model_id in _REGRESSION_MODELS) != (
            model.task_type == "regression"
        ):
            raise ValueError("The selected model is incompatible with the task type.")
        return model

    @field_validator("model_params", mode="before")
    @classmethod
    def _model_parameters(cls, value: Any) -> Any:
        if not isinstance(value, dict) or len(value) > 13:
            raise ValueError("Model parameters must be a bounded per-model mapping.")
        for parameters in value.values():
            if not isinstance(parameters, dict) or len(parameters) > 32:
                raise ValueError("Each model needs a bounded parameter mapping.")
            for key, item in parameters.items():
                if not isinstance(key, str) or len(key) > 128 or not _bounded_literal(item):
                    raise ValueError("Model parameter values must be bounded finite JSON literals.")
        return value


class ModelData(_ModelData):
    node_type: Literal["model"]


class CrossValidationData(_ModelData):
    node_type: Literal["cross_validation"]
    split_strategy: SplitStrategy = "kfold"


class EvaluationData(_NodeData):
    node_type: Literal["evaluation"]
    partition: Literal["test", "validation"] = "test"


class InterpretationData(_NodeData):
    node_type: Literal["interpretation"]


class TableData(_NodeData):
    node_type: Literal["table"]
    include_raw: StrictBool = False
    row_limit: Annotated[int, Field(strict=True, ge=1, le=50000)] = 1000


class ChartData(_NodeData):
    node_type: Literal["chart"]
    chart_type: Literal["auto", "bar", "coefficient", "roc", "pr", "calibration", "residuals"] = (
        "auto"
    )


class ReportData(_NodeData):
    node_type: Literal["report"]
    title: Title | None = None


class NotebookData(_NodeData):
    node_type: Literal["notebook"]
    title: Title | None = None


class ExportData(_NodeData):
    node_type: Literal["export"]
    format: Literal["zip", "html", "csv", "svg", "ipynb", "json"] = "zip"


NodeData = Annotated[
    DatasetData
    | ColumnSelectData
    | FilterData
    | SampleData
    | MissingValueData
    | TypeConversionData
    | DuplicateHandlingData
    | OutlierStrategyData
    | DescriptiveData
    | CorrelationData
    | GroupComparisonData
    | ChiSquareData
    | AnovaData
    | RegressionData
    | SplitData
    | PreprocessingData
    | FeatureSelectionData
    | ModelData
    | CrossValidationData
    | EvaluationData
    | InterpretationData
    | TableData
    | ChartData
    | ReportData
    | NotebookData
    | ExportData,
    Field(discriminator="node_type"),
]
NODE_DATA_ADAPTER: TypeAdapter[NodeData] = TypeAdapter(NodeData)
_NODE_MODELS: dict[str, type[_NodeData]] = {
    "dataset": DatasetData,
    "column_select": ColumnSelectData,
    "filter": FilterData,
    "sample": SampleData,
    "missing_value": MissingValueData,
    "type_conversion": TypeConversionData,
    "duplicate_handling": DuplicateHandlingData,
    "outlier_strategy": OutlierStrategyData,
    "descriptive": DescriptiveData,
    "correlation": CorrelationData,
    "group_comparison": GroupComparisonData,
    "chi_square": ChiSquareData,
    "anova": AnovaData,
    "regression": RegressionData,
    "split": SplitData,
    "preprocessing": PreprocessingData,
    "feature_selection": FeatureSelectionData,
    "model": ModelData,
    "cross_validation": CrossValidationData,
    "evaluation": EvaluationData,
    "interpretation": InterpretationData,
    "table": TableData,
    "chart": ChartData,
    "report": ReportData,
    "notebook": NotebookData,
    "export": ExportData,
}


def _inline_schema(value: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_inline_schema(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        if not reference.startswith("#/$defs/"):
            raise ValueError("Unexpected external parameter schema reference.")
        result = _inline_schema(
            deepcopy(definitions[reference.removeprefix("#/$defs/")]), definitions
        )
        result.update(
            {key: _inline_schema(item, definitions) for key, item in value.items() if key != "$ref"}
        )
        return result
    result = {
        key: _inline_schema(item, definitions) for key, item in value.items() if key != "$defs"
    }
    if "const" in result and "enum" not in result:
        result["enum"] = [result["const"]]
    return result


def parameter_schema(node_type: str) -> dict[str, Any]:
    """Return a fresh per-node JSON Schema without internal tags or UI labels."""
    model = _NODE_MODELS.get(node_type)
    if model is None:
        raise ValueError("Unknown Workflow node type.")
    schema = model.model_json_schema()
    schema = _inline_schema(schema, schema.get("$defs", {}))
    for name in ("node_type", "label"):
        schema["properties"].pop(name, None)
    if "required" in schema:
        schema["required"] = [
            name for name in schema["required"] if name not in {"node_type", "label"}
        ]
        if not schema["required"]:
            schema.pop("required")
    for information in schema["properties"].values():
        if "x-ui" not in information:
            scalar = next(
                (part for part in information.get("anyOf", []) if part.get("type") != "null"),
                information,
            )
            information["x-ui"] = (
                "select"
                if "enum" in scalar
                else {
                    "boolean": "boolean",
                    "integer": "number",
                    "number": "number",
                    "object": "json",
                    "string": "text",
                }.get(scalar.get("type"), "text")
            )
    return schema


def _copy_json(value: Any, depth: int = 0) -> Any:
    """Detach JSON-shaped inputs without invoking arbitrary deepcopy hooks."""
    if depth > 8:
        raise ValueError("Workflow parameter nesting exceeds the finite JSON boundary.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        return int.__int__(value)
    if isinstance(value, float):
        return float.__float__(value)
    if isinstance(value, list):
        return [_copy_json(item, depth + 1) for item in list.__iter__(value)]
    if isinstance(value, dict):
        result = {}
        for key, item in dict.items(value):
            if not isinstance(key, (str, int)):
                raise ValueError("Workflow parameters require JSON object keys.")
            result[_copy_json(key, depth + 1)] = _copy_json(item, depth + 1)
        return result
    raise ValueError("Workflow parameter values must be JSON literals.")


def _legacy_parameters(node_type: str, data: dict[str, Any]) -> dict[str, Any]:
    result = data
    for name, information in parameter_schema(node_type)["properties"].items():
        value = result.get(name)
        if not isinstance(value, str):
            continue
        scalar = next(
            (part for part in information.get("anyOf", []) if part.get("type") != "null"),
            information,
        )
        kind = scalar.get("type")
        if information.get("x-ui") == "columns":
            if value.lstrip().startswith("["):
                result[name] = json.loads(value)
            else:
                result[name] = [part.strip() for part in value.split(",")] if value else []
        elif kind == "boolean" and value.strip().lower() in {"true", "false"}:
            result[name] = value.strip().lower() == "true"
        elif kind == "integer" and re.fullmatch(r"[+-]?\d+", value.strip()):
            result[name] = int(value)
        elif kind == "number":
            try:
                result[name] = float(value)
            except ValueError:
                pass
    if node_type == "filter" and result.get("operator", "eq") in {"gt", "lt"}:
        value = result.get("value")
        if isinstance(value, str):
            try:
                result["value"] = float(value)
            except ValueError:
                pass
    return result


def normalize_node_data(
    node_type: str,
    data: dict,
    *,
    legacy: bool = False,
    known_columns: set[str] | None = None,
) -> dict[str, Any]:
    """Validate parameters and return a detached canonical dict with defaults.

    Empty inheritable ML inputs remain empty; resolution is the main layer's job.
    Known-column checks validate names only, never infer or coerce column dtypes.
    """
    if node_type not in _NODE_MODELS:
        raise ValueError("Unknown Workflow node type.")
    if not isinstance(data, dict):
        raise ValueError("Workflow node parameters must be a mapping.")
    values = _copy_json(data)
    if "node_type" in values and values["node_type"] != node_type:
        raise ValueError("Workflow node discriminator does not match its type.")
    if legacy:
        values = _legacy_parameters(node_type, values)
    values["node_type"] = node_type
    model = NODE_DATA_ADAPTER.validate_python(values)
    if known_columns is not None:
        for name, information in type(model).model_fields.items():
            extra = information.json_schema_extra
            if not isinstance(extra, dict) or extra.get("x-ui") not in {"column", "columns"}:
                continue
            value = getattr(model, name)
            references = value if isinstance(value, list) else [value] if value is not None else []
            missing = [column for column in references if column not in known_columns]
            if missing:
                raise ValueError(f"Unknown column reference in '{name}': {', '.join(missing)}.")
    result = model.model_dump(mode="python", exclude={"node_type", "label"})
    json.dumps(result, allow_nan=False)
    return result
