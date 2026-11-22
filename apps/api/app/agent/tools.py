"""Agent tool directory.

The P0 registry describes planned tool contracts only. No production adapter is
available yet, so callers must treat every entry as non-executable.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Tool output models ────────────────────────────────────────────────────────


class InspectDatasetOutput(BaseModel):
    columns: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    column_count: int = 0


class ProfileDatasetOutput(BaseModel):
    row_count: int = 0
    column_count: int = 0
    numeric_columns: int = 0
    categorical_columns: int = 0
    datetime_columns: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)


class QualityCheckOutput(BaseModel):
    overall_score: float | None = None
    total_rules: int = 0
    failed_count: int = 0
    warning_count: int = 0
    findings_summary: list[dict[str, Any]] = Field(default_factory=list)


class ColumnSelectOutput(BaseModel):
    selected_columns: list[str] = Field(default_factory=list)
    new_row_count: int = 0


class FilterRowsOutput(BaseModel):
    filtered_count: int = 0
    remaining_count: int = 0


class DescriptiveStatsOutput(BaseModel):
    variables: dict[str, dict[str, float | None]] = Field(default_factory=dict)


class CorrelationOutput(BaseModel):
    coefficient: float | None = None
    p_value: float | None = None
    method: str = ""


class HypothesisTestOutput(BaseModel):
    statistic: float | None = None
    p_value: float | None = None
    effect_size: float | None = None
    method: str = ""
    summary: str = ""


class RegressionOutput(BaseModel):
    r_squared: float | None = None
    adj_r_squared: float | None = None
    coefficients: list[dict[str, Any]] = Field(default_factory=list)
    n_observations: int = 0


class TrainMLModelOutput(BaseModel):
    model_id: str = ""
    cv_mean: float | None = None
    cv_std: float | None = None
    test_metrics: dict[str, Any] = Field(default_factory=dict)


class CompareModelsOutput(BaseModel):
    models: list[dict[str, Any]] = Field(default_factory=list)
    primary_metric: str = ""
    ranking: list[dict[str, Any]] = Field(default_factory=list)


class ChartOutput(BaseModel):
    chart_type: str = ""
    data: list[dict[str, Any]] = Field(default_factory=list)


class ReportOutput(BaseModel):
    report_md: str = ""
    section_count: int = 0


class SnapshotOutput(BaseModel):
    snapshot_id: str = ""
    frozen_at: str = ""


# ── Tool runner protocol ──────────────────────────────────────────────────────


class ToolSpec:
    def __init__(
        self,
        name: str,
        description: str,
        category: str,
        input_schema: type[BaseModel] | None = None,
        output_type: type[BaseModel] | None = None,
        requires_write: bool = False,
        high_cost: bool = False,
        sends_data_externally: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.category = category
        self.input_schema = input_schema
        self.output_type = output_type
        self.requires_write = requires_write
        self.high_cost = high_cost
        self.sends_data_externally = sends_data_externally

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "requires_write": self.requires_write,
            "high_cost": self.high_cost,
            "sends_data_externally": self.sends_data_externally,
            "available": False,
            "executable": False,
        }


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "inspect_dataset": ToolSpec(
        "inspect_dataset",
        "List columns, types, and row count.",
        "data",
        output_type=InspectDatasetOutput,
    ),
    "profile_dataset": ToolSpec(
        "profile_dataset",
        "Run full data profiling (distributions, missingness, correlations).",
        "data",
        output_type=ProfileDatasetOutput,
    ),
    "run_quality_check": ToolSpec(
        "run_quality_check",
        "Run 18+ deterministic data-quality rules.",
        "data",
        output_type=QualityCheckOutput,
    ),
    "select_columns": ToolSpec(
        "select_columns",
        "Keep only the chosen columns from a dataset.",
        "data",
        output_type=ColumnSelectOutput,
        requires_write=True,
    ),
    "filter_rows": ToolSpec(
        "filter_rows",
        "Filter rows by a column condition.",
        "data",
        output_type=FilterRowsOutput,
        requires_write=True,
    ),
    "derive_column": ToolSpec(
        "derive_column", "Create a new computed column.", "data", requires_write=True
    ),
    "handle_missing_values": ToolSpec(
        "handle_missing_values", "Drop or impute missing values.", "data", requires_write=True
    ),
    "detect_outliers": ToolSpec("detect_outliers", "Flag extreme outliers via IQR fences.", "data"),
    "run_descriptive_statistics": ToolSpec(
        "run_descriptive_statistics",
        "Compute mean, median, std, quartiles, skewness, kurtosis.",
        "statistics",
        output_type=DescriptiveStatsOutput,
    ),
    "run_correlation": ToolSpec(
        "run_correlation",
        "Pearson, Spearman or Kendall correlation.",
        "statistics",
        output_type=CorrelationOutput,
    ),
    "run_hypothesis_test": ToolSpec(
        "run_hypothesis_test",
        "t-test, Mann-Whitney, ANOVA, chi-square with effect size.",
        "statistics",
        output_type=HypothesisTestOutput,
    ),
    "fit_regression": ToolSpec(
        "fit_regression",
        "Linear or logistic regression with diagnostics.",
        "statistics",
        high_cost=True,
        output_type=RegressionOutput,
    ),
    "train_ml_model": ToolSpec(
        "train_ml_model",
        "Train and cross-validate an ML model.",
        "ml",
        high_cost=True,
        output_type=TrainMLModelOutput,
    ),
    "compare_models": ToolSpec(
        "compare_models",
        "Multi-metric model comparison (no single best).",
        "ml",
        output_type=CompareModelsOutput,
    ),
    "explain_model": ToolSpec(
        "explain_model", "Feature importance or permutation importance.", "ml"
    ),
    "create_chart": ToolSpec(
        "create_chart",
        "Generate a chart from statistical results.",
        "output",
        output_type=ChartOutput,
    ),
    "generate_report": ToolSpec(
        "generate_report",
        "Draft a markdown analysis report.",
        "output",
        output_type=ReportOutput,
        sends_data_externally=True,
    ),
    "export_notebook": ToolSpec(
        "export_notebook",
        "Export analysis steps as a Jupyter notebook.",
        "output",
        sends_data_externally=True,
    ),
    "create_project_snapshot": ToolSpec(
        "create_project_snapshot",
        "Freeze a project with all fingerprints and artifacts.",
        "project",
        requires_write=True,
        output_type=SnapshotOutput,
    ),
}


def describe_tools() -> list[dict[str, Any]]:
    return [spec.to_dict() for spec in TOOL_REGISTRY.values()]
