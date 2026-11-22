import type { AppTranslator, NoParameterMessageKey } from "./i18n-messages";
import { createAppTranslator } from "./i18n-messages";
import { ownedRecordValue } from "./owned-record-value";

const NODE_LABELS: Record<string, NoParameterMessageKey> = {
  dataset: "workflow.node.dataset.label",
  column_select: "workflow.node.column_select.label",
  filter: "workflow.node.filter.label",
  sample: "workflow.node.sample.label",
  missing_value: "workflow.node.missing_value.label",
  type_conversion: "workflow.node.type_conversion.label",
  duplicate_handling: "workflow.node.duplicate_handling.label",
  outlier_strategy: "workflow.node.outlier_strategy.label",
  descriptive: "workflow.node.descriptive.label",
  correlation: "workflow.node.correlation.label",
  group_comparison: "workflow.node.group_comparison.label",
  chi_square: "workflow.node.chi_square.label",
  anova: "workflow.node.anova.label",
  regression: "workflow.node.regression.label",
  split: "workflow.node.split.label",
  preprocessing: "workflow.node.preprocessing.label",
  feature_selection: "workflow.node.feature_selection.label",
  model: "workflow.node.model.label",
  cross_validation: "workflow.node.cross_validation.label",
  evaluation: "workflow.node.evaluation.label",
  interpretation: "workflow.node.interpretation.label",
  table: "workflow.node.table.label",
  chart: "workflow.node.chart.label",
  report: "workflow.node.report.label",
  notebook: "workflow.node.notebook.label",
  export: "workflow.node.export.label",
};
const NODE_DESCRIPTIONS: Record<string, NoParameterMessageKey> = {
  dataset: "workflow.node.dataset.description",
  column_select: "workflow.node.column_select.description",
  filter: "workflow.node.filter.description",
  sample: "workflow.node.sample.description",
  missing_value: "workflow.node.missing_value.description",
  type_conversion: "workflow.node.type_conversion.description",
  duplicate_handling: "workflow.node.duplicate_handling.description",
  outlier_strategy: "workflow.node.outlier_strategy.description",
  descriptive: "workflow.node.descriptive.description",
  correlation: "workflow.node.correlation.description",
  group_comparison: "workflow.node.group_comparison.description",
  chi_square: "workflow.node.chi_square.description",
  anova: "workflow.node.anova.description",
  regression: "workflow.node.regression.description",
  split: "workflow.node.split.description",
  preprocessing: "workflow.node.preprocessing.description",
  feature_selection: "workflow.node.feature_selection.description",
  model: "workflow.node.model.description",
  cross_validation: "workflow.node.cross_validation.description",
  evaluation: "workflow.node.evaluation.description",
  interpretation: "workflow.node.interpretation.description",
  table: "workflow.node.table.description",
  chart: "workflow.node.chart.description",
  report: "workflow.node.report.description",
  notebook: "workflow.node.notebook.description",
  export: "workflow.node.export.description",
};
const CATEGORIES: Record<string, NoParameterMessageKey> = {
  data: "ui.common.data",
  cleaning: "workflow.category.cleaning",
  statistics: "ui.common.statistics",
  ml: "workflow.category.ml",
  output: "workflow.category.output",
};
const PARAMETERS: Record<string, Record<string, NoParameterMessageKey>> = {
  dataset: {
    dataset_id: "workflow.parameter.dataset_id",
  },
  column_select: {
    columns: "workflow.parameter.columns",
  },
  filter: {
    column: "workflow.parameter.column",
    operator: "workflow.parameter.operator",
    value: "workflow.parameter.value",
  },
  sample: {
    fraction: "workflow.parameter.fraction",
    seed: "workflow.parameter.seed",
  },
  missing_value: {
    strategy: "workflow.parameter.strategy",
  },
  type_conversion: {
    column: "workflow.parameter.column",
    to_type: "workflow.parameter.to_type",
  },
  duplicate_handling: {
    keep: "workflow.parameter.keep",
  },
  outlier_strategy: {
    method: "workflow.parameter.method",
  },
  descriptive: {
    alternative: "workflow.parameter.alternative",
    confidence_level: "workflow.parameter.confidence_level",
    missing_value_policy: "workflow.parameter.missing_value_policy",
    standardize: "workflow.parameter.standardize",
    alpha: "workflow.parameter.alpha",
    l1_ratio: "workflow.parameter.l1_ratio",
    variables: "workflow.parameter.variables",
  },
  correlation: {
    alternative: "workflow.parameter.alternative",
    confidence_level: "workflow.parameter.confidence_level",
    missing_value_policy: "workflow.parameter.missing_value_policy",
    standardize: "workflow.parameter.standardize",
    alpha: "workflow.parameter.alpha",
    l1_ratio: "workflow.parameter.l1_ratio",
    variables: "workflow.parameter.variables",
    method: "workflow.parameter.method",
  },
  group_comparison: {
    alternative: "workflow.parameter.alternative",
    confidence_level: "workflow.parameter.confidence_level",
    missing_value_policy: "workflow.parameter.missing_value_policy",
    standardize: "workflow.parameter.standardize",
    alpha: "workflow.parameter.alpha",
    l1_ratio: "workflow.parameter.l1_ratio",
    variable: "workflow.parameter.variable",
    group: "workflow.parameter.group",
    method: "workflow.parameter.method",
  },
  chi_square: {
    alternative: "workflow.parameter.alternative",
    confidence_level: "workflow.parameter.confidence_level",
    missing_value_policy: "workflow.parameter.missing_value_policy",
    standardize: "workflow.parameter.standardize",
    alpha: "workflow.parameter.alpha",
    l1_ratio: "workflow.parameter.l1_ratio",
    variables: "workflow.parameter.variables",
  },
  anova: {
    alternative: "workflow.parameter.alternative",
    confidence_level: "workflow.parameter.confidence_level",
    missing_value_policy: "workflow.parameter.missing_value_policy",
    standardize: "workflow.parameter.standardize",
    alpha: "workflow.parameter.alpha",
    l1_ratio: "workflow.parameter.l1_ratio",
    variable: "workflow.parameter.variable",
    group: "workflow.parameter.group",
  },
  regression: {
    alternative: "workflow.parameter.alternative",
    confidence_level: "workflow.parameter.confidence_level",
    missing_value_policy: "workflow.parameter.missing_value_policy",
    standardize: "workflow.parameter.standardize",
    alpha: "workflow.parameter.alpha",
    l1_ratio: "workflow.parameter.l1_ratio",
    target: "workflow.parameter.target",
    predictors: "workflow.parameter.predictors",
    categorical: "workflow.parameter.categorical",
    kind: "workflow.parameter.kind",
  },
  split: {
    target: "workflow.parameter.target",
    task_type: "workflow.parameter.task_type",
    test_size: "workflow.parameter.test_size",
    val_size: "workflow.parameter.val_size",
    seed: "workflow.parameter.seed",
    strategy: "workflow.parameter.strategy",
    n_splits: "workflow.parameter.n_splits",
    group_variable: "workflow.parameter.group_variable",
    time_variable: "workflow.parameter.time_variable",
  },
  preprocessing: {
    standardize: "workflow.parameter.standardize",
    handle_outliers: "workflow.parameter.handle_outliers",
    impute_numeric: "workflow.parameter.impute_numeric",
    impute_categorical: "workflow.parameter.impute_categorical",
  },
  feature_selection: {
    k: "workflow.parameter.k",
  },
  model: {
    target: "workflow.parameter.target",
    features: "workflow.parameter.features",
    categorical: "workflow.parameter.categorical",
    task_type: "workflow.parameter.task_type",
    model_id: "workflow.parameter.model_id",
    n_splits: "workflow.parameter.n_splits",
    split_strategy: "workflow.parameter.split_strategy",
    seed: "workflow.parameter.seed",
    test_size: "workflow.parameter.test_size",
    val_size: "workflow.parameter.val_size",
    group_variable: "workflow.parameter.group_variable",
    time_variable: "workflow.parameter.time_variable",
    model_params: "workflow.parameter.model_params",
    standardize: "workflow.parameter.standardize",
    handle_outliers: "workflow.parameter.handle_outliers",
    impute_numeric: "workflow.parameter.impute_numeric",
    impute_categorical: "workflow.parameter.impute_categorical",
  },
  cross_validation: {
    target: "workflow.parameter.target",
    features: "workflow.parameter.features",
    categorical: "workflow.parameter.categorical",
    task_type: "workflow.parameter.task_type",
    model_id: "workflow.parameter.model_id",
    n_splits: "workflow.parameter.n_splits",
    split_strategy: "workflow.parameter.split_strategy",
    seed: "workflow.parameter.seed",
    test_size: "workflow.parameter.test_size",
    val_size: "workflow.parameter.val_size",
    group_variable: "workflow.parameter.group_variable",
    time_variable: "workflow.parameter.time_variable",
    model_params: "workflow.parameter.model_params",
    standardize: "workflow.parameter.standardize",
    handle_outliers: "workflow.parameter.handle_outliers",
    impute_numeric: "workflow.parameter.impute_numeric",
    impute_categorical: "workflow.parameter.impute_categorical",
  },
  evaluation: {
    partition: "workflow.parameter.partition",
  },
  interpretation: {},
  table: {
    include_raw: "workflow.parameter.include_raw",
    row_limit: "workflow.parameter.row_limit",
  },
  chart: {
    chart_type: "workflow.parameter.chart_type",
  },
  report: {
    title: "workflow.parameter.title",
  },
  notebook: {
    title: "workflow.parameter.title",
  },
  export: {
    format: "workflow.parameter.format",
  },
};

export function workflowNodeLabel(
  id: string,
  t: AppTranslator = createAppTranslator("en"),
): string {
  const key = ownedRecordValue(NODE_LABELS, id);
  return key ? t(key) : id;
}
export function workflowNodeDescription(id: string, t: AppTranslator): string {
  const key = ownedRecordValue(NODE_DESCRIPTIONS, id);
  return key ? t(key) : id;
}
export function workflowCategoryLabel(id: string, t: AppTranslator): string {
  const key = ownedRecordValue(CATEGORIES, id);
  return key ? t(key) : id;
}
export function workflowParameterLabel(
  node: string,
  id: string,
  t: AppTranslator,
  sourceTitle: string = id,
): string {
  const parameters = ownedRecordValue(PARAMETERS, node);
  const key = parameters ? ownedRecordValue(parameters, id) : undefined;
  return key ? t(key) : sourceTitle;
}

const ENUM_LABELS: Record<string, NoParameterMessageKey> = {
  eq: "workflow.enum.eq",
  ne: "workflow.enum.ne",
  gt: "workflow.enum.gt",
  lt: "workflow.enum.lt",
  drop: "workflow.enum.drop",
  mean: "workflow.enum.mean",
  median: "workflow.enum.median",
  most_frequent: "workflow.enum.most_frequent",
  float: "workflow.enum.float",
  integer: "workflow.enum.integer",
  string: "workflow.enum.string",
  any: "workflow.enum.any",
  first: "workflow.enum.first",
  last: "workflow.enum.last",
  none: "workflow.enum.none",
  winsorize: "workflow.enum.winsorize",
  two_sided: "workflow.enum.two_sided",
  less: "workflow.enum.less",
  greater: "workflow.enum.greater",
  listwise_delete: "workflow.enum.listwise_delete",
  pairwise_delete: "workflow.enum.pairwise_delete",
  error_on_missing: "workflow.enum.error_on_missing",
  pearson: "workflow.enum.pearson",
  spearman: "workflow.enum.spearman",
  kendall: "workflow.enum.kendall",
  welch_t: "workflow.enum.welch_t",
  mann_whitney: "workflow.enum.mann_whitney",
  linear: "workflow.enum.linear",
  linear_regression: "workflow.enum.linear_regression",
  logistic: "workflow.enum.logistic",
  logistic_regression: "workflow.enum.logistic_regression",
  regression: "workflow.enum.regression",
  binary_classification: "workflow.enum.binary_classification",
  multiclass_classification: "workflow.enum.multiclass_classification",
  decision_tree: "workflow.enum.decision_tree",
  random_forest: "workflow.enum.random_forest",
  gradient_boosting: "workflow.enum.gradient_boosting",
  knn: "workflow.enum.knn",
  svm: "workflow.enum.svm",
  ridge: "workflow.enum.ridge",
  lasso: "workflow.enum.lasso",
  elastic_net: "workflow.enum.elastic_net",
  decision_tree_regressor: "workflow.enum.decision_tree_regressor",
  random_forest_regressor: "workflow.enum.random_forest_regressor",
  gradient_boosting_regressor: "workflow.enum.gradient_boosting_regressor",
  __dummy__: "workflow.enum.__dummy__",
  auto: "workflow.enum.auto",
  bar: "workflow.enum.bar",
  coefficient: "workflow.enum.coefficient",
  roc: "workflow.enum.roc",
  pr: "workflow.enum.pr",
  calibration: "workflow.enum.calibration",
  residuals: "workflow.enum.residuals",
  validation: "workflow.enum.validation",
  test: "workflow.enum.test",
  zip: "workflow.enum.zip",
  html: "workflow.enum.html",
  csv: "workflow.enum.csv",
  svg: "workflow.enum.svg",
  ipynb: "workflow.enum.ipynb",
  json: "workflow.enum.json",
  yes: "workflow.enum.yes",
  no: "workflow.enum.no",
  train_test: "workflow.enum.train_test",
  train_val_test: "workflow.enum.train_val_test",
  kfold: "workflow.enum.kfold",
  stratified_kfold: "workflow.enum.stratified_kfold",
  group_kfold: "workflow.enum.group_kfold",
  time_series_split: "workflow.enum.time_series_split",
};
export function workflowEnumLabel(value: unknown, t: AppTranslator): string {
  const id =
    typeof value === "boolean" ? (value ? "yes" : "no") : String(value);
  const key = ownedRecordValue(ENUM_LABELS, id);
  return key ? t(key) : String(value);
}

const STATUS_LABELS: Record<string, NoParameterMessageKey> = {
  pending: "ui.common.pending",
  running: "ui.common.running",
  completed: "ui.report.completed",
  failed: "ui.projects.failed",
  skipped: "ui.quality.skipped.qualityOverview",
  cancelled: "workflow.status.cancelled",
  cleanup_pending: "workflow.status.cleanupPending",
  completed_with_errors: "ui.common.completedWithErrors",
  not_applicable: "ui.common.notApplicable",
};
export function workflowStatusLabel(
  status: string | null | undefined,
  t: AppTranslator,
): string {
  if (!status) return t("common.unavailable");
  const key = ownedRecordValue(STATUS_LABELS, status);
  return key ? t(key) : status;
}

const PARAMETER_DESCRIPTIONS: Record<
  string,
  { source: string; key: NoParameterMessageKey }
> = {
  "model.model_id": {
    source:
      "Default to Ridge (regression) or Logistic Regression (classification).",
    key: "workflow.parameterDescription.model.model_id",
  },
  "cross_validation.model_id": {
    source:
      "Default to Ridge (regression) or Logistic Regression (classification).",
    key: "workflow.parameterDescription.cross_validation.model_id",
  },
};
/** ID selects the message; exact frozen field text guards against a changed catalog. */
export function workflowParameterDescription(
  nodeType: string,
  parameter: string,
  source: string,
  t: AppTranslator,
): string {
  const known = ownedRecordValue(
    PARAMETER_DESCRIPTIONS,
    `${nodeType}.${parameter}`,
  );
  return known && known.source === source ? t(known.key) : source;
}
