/** Domain types shared across the StatForge frontend. Mirrors the FastAPI schemas. */

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  is_active: boolean;
  created_at: string;
}

export interface AuthResponse {
  user: User;
  message: string;
}

export type TaskType =
  | "classification_binary"
  | "classification_multiclass"
  | "regression"
  | "clustering"
  | "anomaly_detection"
  | "statistical_analysis";

export type ProjectStatus = "draft" | "active" | "archived";

export interface Project {
  workspace_path?: string | null;
  id: string;
  title: string;
  description: string | null;
  research_question: string | null;
  task_type: TaskType | null;
  status: ProjectStatus;
  current_revision: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  title: string;
  description?: string | null;
  research_question?: string | null;
  task_type?: TaskType | null;
}

export type ProjectUpdate = Partial<ProjectCreate> & {
  status?: ProjectStatus;
};

export type UploadStatus =
  | "pending"
  | "uploading"
  | "uploaded"
  | "validating"
  | "ready"
  | "failed"
  | "deleted";

export interface Dataset {
  multimodal?: {
    format: string;
    source_filename: string;
    source_sha256: string;
    source_bytes: number;
    node: string[];
    label_column: string;
    task: "classification" | "regression";
    label_count: number;
    split: string | null;
    modalities: { name: string; columns: string[]; sample_shape: number[]; pooling: string; input_dimensions: number; output_dimensions: number; projection: string | null }[];
  } | null;
  sheet_name?: string | null;
  csv_encoding?: string | null;
  raw_file?: boolean;
  signal_session?: boolean;
  media_case?: {kind: string; identity: string; title: string; purpose?:"legacy"|"temporary"|"case"; owner_case?:{collection_id:string;sample_id:string}|null; survey?:boolean; samples?: {sample_id:string; case_deleted?:boolean; case_title?:string; title?:string; display_title?:string; original_filename?:string; primary_id?:string; video_id?:string; audio_id?:string; text_id?:string; pitch_id?:string; pitch_alignment?:{offset_seconds:number}; audio_alignment?:{offset_seconds:number|null;content_reviewed:boolean}; files?:Record<string,{id?:string;sha256?:string}>; members?:{id:string;sha256:string;range?:{start:number;end:number}}[]}[]} | null;
  material_id?: string | null;
  visual_result?: {run_id:string;sources:Record<string,string>} | null;
  transcription?: {run_id:string;version?:number;source_id:string;method?:string} | null;
  preparation_operation?: string | null;
  transcript_pair?: {collection_id:string; sample_id:string; video_id:string; video_sha256:string; audio_id:string; audio_sha256:string; video_start:number; video_end:number; audio_start:number; audio_end:number} | null;
  media_result?: {run_id: string; collection_id: string; kind: string} | null;
  coding_summary?: { coding_revision?: number; excluded_transcription_segments?: number; sample_segments: { case_key?: string; sample_id: string; sample_collection_id: string; segment_id: string; material_id: string | null; material_sha256: string | null; material_version: number | null; locator: import("./text-coding").AnnotationLocation }[] } | null;
  coding_matrix?: {row_kind:"case"|"file";coding_revision:number;saved_at:string} | null;
  coding_agreement?: {modality:string;revision:number;saved_at:string} | null;
  local_file?: boolean;
  source_dataset_id?: string | null;
  prepared_source_sha256?: string | null;
  preparation_change?: string | null;
  source_path?: string | null;
  display_name?: string | null;
  preparation_parent_id?: string | null;
  preparation_steps?: number;
  source_version?: number;
  source_parent_id?: string | null;
  source_root_id?: string | null;
  id: string;
  project_id: string;
  original_filename: string;
  safe_filename: string;
  file_format: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  row_count: number | null;
  column_count: number | null;
  upload_status: UploadStatus;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  validated_at: string | null;
}

export interface ColumnProfile {
  name: string;
  inferred_type: string;
  polars_type: string;
  nullable: boolean;
  null_count: number;
  null_rate: number;
  unique_count: number;
  sample_values: unknown[];
}

export type PreviewRow = Record<string, unknown>;

export interface DatasetPreview {
  dataset_id: string;
  original_filename: string;
  file_format: string;
  row_count: number | null;
  column_count: number | null;
  upload_status: string;
  columns: ColumnProfile[];
  rows: PreviewRow[];
  showing_rows: number;
  total_rows: number;
}

export const TASK_TYPES: readonly TaskType[] = [
  "classification_binary",
  "classification_multiclass",
  "regression",
  "clustering",
  "anomaly_detection",
  "statistical_analysis",
];

/* ------------------------------------------------------------------ */
/* Data Profiling — /api/v1/datasets/{id}/profile                      */
/* ------------------------------------------------------------------ */

export type ProfileStatus =
  "pending" | "profiling" | "completed" | "failed" | "stale";

export interface NumericStats {
  mean: number;
  median: number;
  std: number;
  variance: number;
  min: number;
  max: number;
  range: number;
  q1: number;
  q3: number;
  iqr: number;
  p01: number;
  p05: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
  p99: number;
  skewness: number;
  kurtosis: number;
  zero_count: number;
  negative_count: number;
  outlier_count: number;
  outlier_rate: number;
  histogram: { bins: number[]; counts: number[] };
  boxplot: {
    min: number;
    q1: number;
    median: number;
    q3: number;
    max: number;
    outliers: number[];
  };
}

export interface CategoricalStats {
  top_values: { value: string; count: number; proportion: number }[];
  avg_length: number;
  min_length: number;
  max_length: number;
  empty_string_count: number;
}

export interface DatetimeStats {
  earliest: string;
  latest: string;
  span_days: number;
  invalid_count: number;
  duplicate_count: number;
  is_monotonic: boolean;
}

export interface ProfileColumn {
  name: string;
  physical_type: string;
  inferred_type: string;
  inference_confidence: number;
  nullable: boolean;
  count: number;
  null_count: number;
  null_rate: number;
  unique_count: number;
  unique_rate: number;
  sample_values: string[];
  numeric_stats: NumericStats | null;
  categorical_stats: CategoricalStats | null;
  datetime_stats: DatetimeStats | null;
  warnings: string[];
}

export interface ProfileOverview {
  row_count: number;
  column_count: number;
  numeric_columns: number;
  categorical_columns: number;
  datetime_columns: number;
  boolean_columns: number;
  string_columns: number;
  other_columns: number;
  total_nulls: number;
  completeness: number;
  duplicate_row_count: number;
  duplicate_row_rate: number;
  complete_row_count: number;
  complete_row_rate: number;
  sampled: boolean;
  sample_size: number | null;
}

export interface QualityDimension {
  score: number;
  weight: number;
  issues: string[];
}

export interface QualityAlert {
  severity: string;
  category: string;
  message: string;
}

export interface ProfileQuality {
  total_score: number;
  dimensions: Record<string, QualityDimension>;
  alerts: QualityAlert[];
}

export interface CorrelationMatrix {
  columns: string[];
  matrix: (number | null)[][];
}

export interface ProfileCorrelations {
  pearson: CorrelationMatrix;
  spearman: CorrelationMatrix;
  note: string | null;
}

export interface ProfileMissingness {
  per_column: { name: string; null_count: number; null_rate: number }[];
  complete_rows: number;
  complete_row_rate: number;
  all_null_columns: string[];
  high_missing_columns: string[];
  row_missing_distribution: { missing_count: number; row_count: number }[];
}

export interface ProfileResult {
  overview: ProfileOverview;
  columns: ProfileColumn[];
  missingness: ProfileMissingness;
  correlations: ProfileCorrelations;
  quality: ProfileQuality;
}

export interface Profile {
  id: string;
  dataset_id: string;
  status: ProfileStatus;
  profiling_version: number;
  algorithm_version: string;
  dataset_sha256: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  row_count: number | null;
  column_count: number | null;
  sampled: boolean;
  sample_size: number | null;
  created_at: string;
  updated_at: string;
  result: ProfileResult | null;
}

/* ------------------------------------------------------------------ */
/* Data Quality — /api/v1/datasets/{id}/quality                        */
/* ------------------------------------------------------------------ */

export type QualityRunStatus =
  "pending" | "running" | "completed" | "completed_with_errors" | "failed";

export type FindingStatus =
  "passed" | "warning" | "failed" | "skipped" | "error";

export type FindingSeverity = "info" | "low" | "medium" | "high" | "critical";

export type FindingCategory =
  | "structure"
  | "completeness"
  | "uniqueness"
  | "validity"
  | "consistency"
  | "temporal"
  | "distribution"
  | "domain";

export interface QualityFinding {
  rule_id: string;
  rule_name: string;
  description: string;
  status: FindingStatus;
  severity: FindingSeverity;
  category: string;
  title: string;
  summary: string | null;
  affected_columns: string[];
  affected_count: number;
  affected_ratio: number;
  threshold: Record<string, number> | null;
  observed_value: unknown;
  /** May include `samples: string[]` and `sample_count_shown: number`. */
  evidence: Record<string, unknown>;
  remediation: string;
  rule_version: string;
  limitations: string;
  created_at: string | null;
}

export interface QualityScoreDeduction {
  dimension: string;
  rule_id: string;
  severity: string;
  finding_count: number;
  coverage: number;
  penalty: number;
}

export interface QualityScoreDetail {
  total_score: number;
  dimensions: Record<string, QualityDimension>;
  deductions: QualityScoreDeduction[];
  ruleset_note: string;
}

/** Minimal shape consumed by the radial QualityScore gauge. */
export interface QualityGaugeData {
  total_score: number;
  dimensions: Record<string, QualityDimension>;
}

export interface QualityRun {
  id: string;
  dataset_id: string;
  profile_id: string | null;
  status: QualityRunStatus;
  ruleset_version: string;
  threshold_config: Record<string, number> | null;
  dataset_sha256: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  total_rules: number;
  passed_count: number;
  warning_count: number;
  failed_count: number;
  error_count: number;
  skipped_count: number;
  overall_score: number | null;
  score: QualityScoreDetail | null;
  ohlcv_enabled: boolean;
  sampled: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  findings: QualityFinding[] | null;
}

export interface QualityRunSummary {
  id: string;
  dataset_id: string;
  status: QualityRunStatus;
  ruleset_version: string;
  overall_score: number | null;
  total_rules: number;
  passed_count: number;
  warning_count: number;
  failed_count: number;
  error_count: number;
  skipped_count: number;
  ohlcv_enabled: boolean;
  sampled: boolean;
  created_by: string | null;
  created_at: string;
}

export interface QualityRunsResponse {
  runs: QualityRunSummary[];
  total: number;
}

export interface QualityFindingsResponse {
  findings: QualityFinding[];
  total: number;
  page: number;
  page_size: number;
}

export interface QualityRunRequest {
  thresholds?: Record<string, number>;
  enable_ohlcv?: boolean;
  ohlcv_mapping?: Record<string, string>;
  force?: boolean;
}

/* ------------------------------------------------------------------ */
/* Statistical analysis — /api/v1/statistics                           */
/* ------------------------------------------------------------------ */

export type StatisticalRunStatus =
  "pending" | "running" | "completed" | "not_applicable" | "failed";

export type MissingPolicy =
  "listwise_delete" | "pairwise_delete" | "error_on_missing";

export type HypothesisAlternative = "two_sided" | "less" | "greater";

export interface StatisticalMethod {
  method_id: string;
  name: string;
  description: string;
  category: string;
  version: string;
  min_variables: number;
  max_variables: number;
  needs_group: boolean;
  requires_numeric: boolean;
  manual_configuration_supported?: boolean;
}

export interface MethodsResponse {
  methodset_version: string;
  methods: StatisticalMethod[];
}

export interface StatisticalEvaluationReference {
  dataset_id: string;
  dataset_sha256: string;
  schema_sha256: string;
  name: string;
  variable_id?: string | null;
}

export type StoredPredictionEvaluation = {
  operation: "evaluate_existing_predictions";
  actual: StatisticalEvaluationReference;
  predicted: StatisticalEvaluationReference;
} & (
  | {
      version: "existing-predictions-regression-v1";
      primary_metric: "rmse" | "mae" | "r2";
      secondary_metrics: Array<"rmse" | "mae" | "r2">;
    }
  | {
      version: "existing-predictions-binary-v1";
      label_encoding: "integer_0_1";
      positive_label: 0 | 1;
      primary_metric: "recall" | "accuracy" | "precision" | "specificity";
      secondary_metrics: Array<
        "recall" | "accuracy" | "precision" | "specificity"
      >;
    }
);

export interface StatisticalRunRequest {
  method_id: string;
  variables: string[];
  group_variable?: string | null;
  paired?: boolean;
  mu?: number;
  expected_proportions?: Record<string, number> | null;
  alternative?: HypothesisAlternative;
  confidence_level?: number;
  missing_value_policy?: MissingPolicy;
  target?: string | null;
  categorical_variables?: string[];
  reference_categories?: Record<string, string> | null;
  standardize?: boolean;
  alpha?: number;
  l1_ratio?: number;
  evaluation?: StoredPredictionEvaluation;
}

export interface AssumptionCheck {
  name: string;
  passed: boolean;
  detail: string;
  value: unknown;
  threshold: unknown;
}

export interface SampleInfo {
  original_n: number;
  excluded_n: number;
  final_n: number;
  reason: string;
  per_variable: Record<string, number>;
}

export interface StatisticalResult {
  method: string;
  method_version: string;
  status: string;
  sample: SampleInfo | null;
  estimates: Record<string, unknown>;
  statistic: Record<string, unknown>;
  p_value: number | null;
  alternative: string;
  confidence_level: number;
  confidence_interval: Record<string, unknown>;
  effect_size: Record<string, unknown>;
  descriptive_statistics: Record<string, unknown>;
  assumption_checks: AssumptionCheck[];
  warnings: string[];
  limitations: string[];
  software_versions: Record<string, string>;
  diagnostics: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
}

export interface StatFinding {
  rule_id: string;
  category: string;
  severity: string;
  title: string;
  summary: string | null;
  evidence: Record<string, unknown>;
  remediation: string | null;
  created_at: string | null;
}

export interface StatisticalRun {
  id: string;
  project_id: string;
  dataset_id: string;
  profile_id: string | null;
  quality_run_id: string | null;
  method: string;
  status: StatisticalRunStatus;
  methodset_version: string;
  dataset_sha256: string;
  input_config: Record<string, unknown> | null;
  missing_value_policy: string;
  sample_size: number | null;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  result: StatisticalResult | null;
  findings: StatFinding[] | null;
}

export interface StatisticalRunSummary {
  id: string;
  dataset_id: string;
  method: string;
  status: StatisticalRunStatus;
  methodset_version: string;
  missing_value_policy: string;
  sample_size: number | null;
  statistic_value: number | null;
  p_value: number | null;
  error_code: string | null;
  created_by: string | null;
  created_at: string;
}

export interface StatisticalRunsResponse {
  runs: StatisticalRunSummary[];
  total: number;
}

export interface ValidationResult {
  applicable: boolean;
  method_id: string;
  checks: AssumptionCheck[];
  reasons: string[];
}

/* ------------------------------------------------------------------ */
/* Machine learning — /api/v1/ml                                       */
/* ------------------------------------------------------------------ */

export type MLTaskType =
  "binary_classification" | "multiclass_classification" | "regression";

export type MLSplitStrategy =
  | "train_test"
  | "train_val_test"
  | "kfold"
  | "stratified_kfold"
  | "group_kfold"
  | "time_series_split";

export type MLRunStatus = "pending" | "running" | "completed" | "failed";

export interface MLModelInfo {
  model_id: string;
  name: string;
  tasks: string[];
  interpretable: boolean;
}

export interface MLMetadata {
  tasks: string[];
  split_strategies: string[];
  models: MLModelInfo[];
}

/** Trusted full-data statistics for ML preflight / folds suggestions. */
export interface MLPreflightStats {
  dataset_sha256: string;
  target: string;
  task_type: string;
  split_strategy: string;
  group_variable: string | null;
  time_variable: string | null;
  n_splits: number;
  test_size: number;
  random_seed: number;
  n_rows: number;
  n_non_null_target: number;
  class_counts: Record<string, number>;
  n_classes: number | null;
  min_class_count: number | null;
  n_groups: number | null;
  time_is_sorted_ascending: boolean | null;
  suggested_n_splits: number | null;
  trusted: boolean;
  trust_reason: string | null;
  source: string;
}

export interface MLExperimentCreateRequest {
  model_params?: Record<string, Record<string, unknown>>;
  name?: string;
  task_type: MLTaskType;
  target: string;
  features: string[];
  categorical_features?: string[];
  group_variable?: string | null;
  time_variable?: string | null;
  split_strategy?: MLSplitStrategy;
  test_size?: number;
  val_size?: number;
  n_splits?: number;
  random_seed?: number;
  model_ids?: string[];
  standardize?: boolean;
  impute_numeric?: string;
  impute_categorical?: string;
  handle_outliers?: boolean;
  feature_selection_k?: number | null;
}

export type MLEditorState = Omit<Required<MLExperimentCreateRequest>, "model_params" | "n_splits" | "random_seed" | "test_size" | "feature_selection_k"> & {
  model_params: string; n_splits: string; random_seed: string; test_size: string; feature_selection_k: string;
};

export interface MLExperiment {
  id: string;
  project_id: string;
  dataset_id: string;
  name: string;
  task_type: string;
  target_variable: string;
  feature_variables: string[];
  categorical_variables: string[];
  config: Record<string, unknown> | null;
  dataset_sha256: string;
  created_by: string | null;
  created_at: string;
  run_count: number;
  latest_run_status: string | null;
}

export interface MLModelRunResult {
  evaluation_protocol_id?: string;
  prediction_diagnostics?: MLPredictionDiagnostics;
  model_id: string;
  status: string;
  cv?: {
    strategy: string;
    n_splits: number | null;
    per_fold: number[];
    mean: number | null;
    std: number | null;
  };
  validation_metrics?: Record<string, unknown> | null;
  test_metrics?: Record<string, unknown>;
  interpretation?: {
    method: string | null;
    features: Record<string, number>;
    original_features: Record<string, number>;
    permutation: Record<string, number>;
  };
  overfit_gap?: number | null;
  training_seconds?: number;
  error_message?: string;
}

export interface MLLeakageCheck {
  rule_id: string;
  severity: string;
  title: string;
  detail: string;
  passed: boolean;
}

export interface MLComparisonRow {
  evaluation_protocol_id?: string;
  comparable?: boolean;
  model_id: string;
  primary_metric: string;
  primary_value: number | null;
  metrics: Record<string, unknown>;
  cv_mean: number | null;
  cv_std: number | null;
  overfit_gap: number | null;
  training_seconds: number | null;
  rank_by_primary?: number;
}

export interface MLEvaluationProtocol {
  version: number;
  protocol_id: string;
  dataset_sha256: string;
  features: string[];
  target: string;
  preprocessing: Record<string, unknown>;
  scoring: { primary_metric: string; direction: string; task_type: string };
  split: {
    n_train: number;
    n_validation: number;
    n_test: number;
    n_kept: number;
    train_rows_sha256: string;
    test_rows_sha256: string;
    kept_rows_sha256: string;
    validation_rows_sha256: string;
    cv_folds: Array<Record<string, string | number>>;
  };
}

export interface MLDiagnosticCurve {
  status: string;
  reason?: string;
  fpr?: number[];
  tpr?: number[];
  precision?: number[];
  recall?: number[];
  n_points_original?: number;
  downsampled?: boolean;
}

export interface MLPredictionDiagnostics {
  version: number;
  kind: string;
  status: string;
  n_evaluated: number;
  roc?: MLDiagnosticCurve;
  pr?: MLDiagnosticCurve;
  calibration?: {
    status: string;
    mode?: string;
    reason?: string;
    bins?: Array<{
      lower: number;
      upper: number;
      n: number;
      mean_predicted_probability: number | null;
      positive_fraction: number | null;
    }>;
  };
  regression?: {
    histogram: { edges: number[]; counts: number[] };
    errors: Record<string, number>;
    quantiles: Record<string, number[]>;
  };
}

export interface MLRun {
  id: string;
  experiment_id: string;
  status: MLRunStatus;
  random_seed: number;
  validation_strategy: string | null;
  split: Record<string, unknown> | null;
  models: MLModelRunResult[];
  comparison: {
    protocol_verified?: boolean;
    evaluation_protocol_id?: string;
    baseline?: {
      status: string;
      strategy: string;
      evaluation_protocol_id: string;
      test_metrics?: Record<string, unknown>;
      error_type?: string;
    };
    primary_metric: string;
    rows: MLComparisonRow[];
    note: string;
  } | null;
  leakage_checks: MLLeakageCheck[];
  n_observations: number | null;
  artifact_key: string | null;
  artifact_meta: Record<string, unknown> | null;
  software_versions: Record<string, string>;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface MLRunSummary {
  id: string;
  experiment_id: string;
  status: MLRunStatus;
  validation_strategy: string | null;
  n_observations: number | null;
  primary_metric: string | null;
  best_model: string | null;
  best_score: number | null;
  error_code: string | null;
  created_at: string;
}

/* ------------------------------------------------------------------ */
/* Workflow — /api/v1/workflow                                          */
/* ------------------------------------------------------------------ */

export interface WorkflowCatalogNode {
  node_type: string;
  label: string;
  category: string;
  inputs: string[];
  outputs: string[];
  description: string;
  params: string[];
  parameter_schema?: WorkflowParameterSchema;
  available?: boolean;
  availability_reason?: string | null;
}

export type WorkflowParameterValue =
  | null
  | boolean
  | number
  | string
  | WorkflowParameterValue[]
  | { [key: string]: WorkflowParameterValue };

export interface WorkflowParameterSchema {
  type?: string;
  title?: string;
  description?: string;
  enum?: WorkflowParameterValue[];
  anyOf?: WorkflowParameterSchema[];
  properties?: Record<string, WorkflowParameterSchema>;
  required?: string[];
  default?: WorkflowParameterValue;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  maxLength?: number;
  items?: WorkflowParameterSchema;
  "x-ui"?: string;
  "x-column-kind"?: string;
}

export interface WorkflowCatalog {
  categories: Record<string, string>;
  nodes: WorkflowCatalogNode[];
}

export interface WorkflowGraph {
  schema_version?: 1 | 2;
  nodes: Array<{
    id: string;
    type: string;
    data: Record<string, WorkflowParameterValue>;
    position?: { x: number; y: number };
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    sourceHandle?: string | null;
    targetHandle?: string | null;
  }>;
}

export interface Workflow {
  id: string;
  project_id: string;
  dataset_id: string | null;
  name: string;
  version: number;
  status: string;
  graph: WorkflowGraph | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  run_count: number | null;
  latest_run_status: string | null;
}

export interface GraphIssue {
  level: string;
  code: string;
  message: string;
  node_id: string | null;
}

export interface GraphValidation {
  valid: boolean;
  issues: GraphIssue[];
  topo_order: string[];
}

export interface WorkflowNodeResult {
  status: string;
  output?: Record<string, unknown> | null;
  node_type: string | null;
  output_kind: string | null;
  summary: string;
  error: string | null;
  error_code?: string | null;
  reason_code?: string | null;
  seconds: number;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  dataset_id: string | null;
  graph_snapshot?: WorkflowGraph | null;
  workflow_version?: number | null;
  sources?: Array<{
    dataset_id: string;
    dataset_sha256: string;
    file_format: string;
  }>;
  software_versions?: Record<string, string>;
  status: string;
  node_results: Record<string, WorkflowNodeResult>;
  order: string[];
  total_seconds: number | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  created_by: string | null;
  created_at: string;
}

export type WorkflowRunSummary = Pick<
  WorkflowRun,
  | "id"
  | "workflow_id"
  | "status"
  | "total_seconds"
  | "error_message"
  | "created_at"
>;

export interface WorkflowArtifact {
  id: string;
  filename: string;
  media_type: string;
  bytes: number;
  sha256: string;
  includes_raw_rows: boolean;
}

export interface WorkflowDeletion {
  receipt_id: string;
  workflow_id: string;
  status: "completed" | "cleanup_pending";
  pending_artifacts: number;
  deleted_artifacts: number;
}

export interface WorkflowDeletionPage {
  receipts: WorkflowDeletion[];
  next_offset: number | null;
}

/* ------------------------------------------------------------------ */
/* AI assistant — /api/v1/ai                                            */
/* ------------------------------------------------------------------ */

export interface AIStatus {
  enabled: boolean;
  provider: string;
  model: string | null;
  ready: boolean;
  message: string;
}

export interface DirectoryDiagnostic {
  path: string | null;
  writable: boolean;
}

export interface DesktopDiagnostics {
  desktop_mode: boolean;
  api_version: string;
  db_backend: string;
  sqlite_path: string;
  storage_backend: string;
  storage_path: string;
  application_data: DirectoryDiagnostic;
  database_directory: DirectoryDiagnostic;
  storage_directory: DirectoryDiagnostic;
  log_directory: DirectoryDiagnostic;
  ai_configured: boolean;
  session_token_configured: boolean;
}

export interface AIReviewResult {
  prompt_id: string;
  provider: string;
  model: string;
  status: string;
  invocation_id: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  latency_ms: number | null;
  token_usage: Record<string, unknown> | null;
}
