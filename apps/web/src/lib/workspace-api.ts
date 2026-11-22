import { apiFetch } from "./api-client";
import type { PreviewScope } from "./preview-types";
import type { MLEditorState, MLExperimentCreateRequest, StatisticalRunRequest } from "./types";

export type ObjectRef = {
  kind: "data" | "analysis" | "figure" | "material";
  id: string;
};
type Base = { schema_version: 1; title: string };
export type PlotDraft = Base & {
  ai_question?: string;
  kind: "plot";
  scope: PreviewScope;
  code: string;
  dpi: number;
};
export type PlotRun = Base & {
  kind: "plot_run";
  draft_id: string;
  draft_revision: number;
  request_id: string;
  instance: string;
  scope: PreviewScope;
  code: string;
  dpi: number;
  status: "queued" | "running" | "cancel_requested" | "cancelled" | "failed" | "interrupted" | "completed";
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  artifacts: Partial<Record<"svg" | "png" | "pdf" | "renderer", { object_key: string; sha256: string; bytes: number }>>;
};
export type DataWorkspaceState = Base & {
  kind: "data_workspace";
  dataset_id: string;
};
export type ResearchInputState = Base & {
  kind: "research_input";
  context_key?: string;
  question: string;
  workspace_id: string | null;
  session_id: string | null;
  dataset_id: string | null;
  columns: Record<string, string[]>;
};
export type ResearchTask = Base & {
  kind: "research_task";
  question: string;
  dataset_id: string | null;
  dataset_ids?: string[];
  analysis_ids: string[];
  figure_ids: string[];
  plot_run_ids?: string[];
  plot_version_order?: Record<string, string[]>;
  archived_analysis_ids?: string[];
  archived_figure_ids?: string[];
  adopted_id: string | null;
  statistics_draft: StatisticalRunRequest | null;
  ml_draft?: MLExperimentCreateRequest | null;
  ml_editor?: MLEditorState | null;
  exploration_context: string;
};
export type ReportSourceKind =
  "plot_run" | "statistics" | "ml" | "quality" | "workflow" | "figure" | "citation";
export type HistoryKind =
  | "media_run"
  | ReportSourceKind
  | "analysis_run"
  | "plot"
  | "analysis"
  | "report"
  | "research_input"
  | "research"
  | "plan"
  | "template"
  | "session";
export type ReportSource = {
  kind: ReportSourceKind;
  id: string;
  revision: number;
  sha256: string;
};
export type ReportBlock = {
  id: string;
  title: string;
  body: string;
  caption: string;
  source: ReportSource | null;
  source_snapshot: Record<string, unknown> | null;
};
export type ReportDocument = Base & {
  kind: "report";
  subtitle: string;
  blocks: ReportBlock[];
};
export type HistoryLabel = Base & {
  kind: "history_label";
  target_kind: HistoryKind;
  target_id: string;
};
export type WorkspaceState = Base & {
  kind: "workspace";
  selected: ObjectRef | null;
  tabs: ObjectRef[];
  view: "table" | "figure" | "diagnostics" | "record";
  inspector: "closed" | "parameters" | "style" | "sources" | "ai";
  explorer_width: number;
  inspector_width: number;
  scroll_top: number;
};
export type AnalysisDraft = Base & {
  kind: "analysis";
  scope: PreviewScope;
  statistics?: StatisticalRunRequest | null;
  ml?: MLExperimentCreateRequest | null;
  ml_editor?: MLEditorState | null;
  research_question: string;
  observation_unit: string;
  measurement_scales: Record<
    string,
    "nominal" | "ordinal" | "continuous" | "unknown"
  >;
};
export type FigureSpec = Omit<Base, "schema_version"> & {
  schema_version: 1 | 2;
  kind: "figure";
  scope: PreviewScope;
  chart_type:
    | "welch_groups_difference"
    | "distribution"
    | "correlation_scatter"
    | "ols_coefficients";
  renderer_version: "workspace-figure-v1" | "workspace-figure-v2";
  fields: string[];
  layers: (
    | "group_means"
    | "difference_ci"
    | "histogram"
    | "box"
    | "points"
    | "coefficients"
    | "coefficient_ci"
  )[];
  interval: "mean_difference_ci" | "none" | "coefficient_ci";
  snapshot_id?: string | null;
  content_sha256?: string | null;
  accent_color?: string | null;
  tick_digits?: number | null;
  line_weight?: number;
  margin_scale?: number;
  axis_label?: string;
  group_order: string[];
  unit: string;
  theme: "research" | "paper";
  width: number;
  height: number;
  dpi: number;
  font_size: number;
  caption: string;
};
export type FigureData = Base & {
  kind: "figure_data";
  scope: PreviewScope;
  chart_type: "distribution" | "correlation_scatter" | "ols_coefficients";
  fields: string[];
  input_config: Record<string, unknown>;
  source_result_sha256: string;
  sample_rule: string;
  selection_sha256: string | null;
  params: Record<string, unknown>;
  engine_version: string;
  n: number;
  displayed_n: number;
  seed: number | null;
  data: Record<string, unknown>;
  content_sha256: string;
  request_id: string;
  input_hash: string;
};
export type PlanStep = {
  id: string;
  capability: "statistics" | "ridge";
  capability_version: "1";
  depends_on: string[];
  statistics?: StatisticalRunRequest | null;
  ml?: MLExperimentCreateRequest | null;
  expected_output: "statistical_run" | "ml_run";
  approval: "explicit_local_execution";
};
export type PlanBody = Base & {
  kind: "plan";
  scope: PreviewScope;
  research_question: string;
  steps: PlanStep[];
};
export type TemplateBody = Base & {
  kind: "template";
  research_question: string;
  steps: PlanStep[];
  required_columns: Record<string, string>;
};
export type SessionBody = Base & {
  kind: "session";
  scope: PreviewScope;
  messages: {
    request_id: string;
    question: string;
    scope: PreviewScope;
    turn_id: string;
  }[];
  confirmed_information: string;
  personal_preferences: string;
  project_rules: string;
  task_requirements: string;
};
export type WorkspacePayload =
  | WorkspaceState
  | PlotDraft
  | PlotRun
  | DataWorkspaceState
  | ResearchInputState
  | ResearchTask
  | ReportDocument
  | HistoryLabel
  | AnalysisDraft
  | FigureSpec
  | FigureData
  | PlanBody
  | TemplateBody
  | SessionBody;
export type WorkspaceRecord<T extends WorkspacePayload = WorkspacePayload> = {
  id: string;
  owner_id: string;
  archived?: boolean;
  project_id: string;
  revision: number;
  payload: T;
};
export type WorkspaceSummary = {
  id: string;
  kind: WorkspacePayload["kind"];
  revision: number;
  title?: string;
  available: boolean;
};
export type Execution = {
  id: string;
  request_id: string;
  plan_id: string;
  plan_revision: number;
  status: string;
  error_code: string | null;
  results: {
    step_id: string;
    status: string;
    statistical_run_id?: string;
    ml_run_id?: string;
    experiment_id?: string;
  }[];
};

export const workspaceApi = {
  generateFigure: (
    scope: PreviewScope,
    chart_type: FigureData["chart_type"],
    fields: string[],
    bins: number,
    request_id: string,
    signal?: AbortSignal,
  ) =>
    apiFetch<WorkspaceRecord<FigureData>>("/api/v1/workspace/figure-data", {
      method: "POST",
      signal,
      body: JSON.stringify({ scope, chart_type, fields, bins, request_id }),
    }),
  list: async (
    project: string,
    kind?: string,
    signal?: AbortSignal,
  ): Promise<WorkspaceSummary[]> => {
    const value = await apiFetch<{
      records: (WorkspaceRecord | { id: string; state: "unavailable" })[];
    }>(
      `/api/v1/workspace/records?project_id=${project}${kind ? `&kind=${kind}` : ""}`,
      { signal },
    );
    return value.records.map((record) =>
      "payload" in record
        ? {
            id: record.id,
            kind: record.payload.kind,
            revision: record.revision,
            title: record.payload.title,
            available: true,
          }
        : { id: record.id, kind: "workspace", revision: 0, available: false },
    );
  },
  read: (id: string, signal?: AbortSignal, revision?: number) =>
    apiFetch<WorkspaceRecord>(
      `/api/v1/workspace/records/${id}${revision ? `?revision=${revision}` : ""}`,
      { signal },
    ),
  save: (
    project: string,
    payload: WorkspacePayload,
    prior?: Pick<WorkspaceRecord, "id" | "revision">,
    signal?: AbortSignal,
  ) =>
    apiFetch<WorkspaceRecord>(
      `/api/v1/workspace/records${prior ? `/${prior.id}` : ""}`,
      {
        method: prior ? "PATCH" : "POST",
        signal,
        body: JSON.stringify({
          project_id: project,
          expected_revision: prior?.revision ?? 0,
          payload: payload.kind === "report" ? {
            ...payload,
            blocks: payload.blocks.map(block => ({...block, source_snapshot:
              block.source_snapshot?.storage === "statforge-report-source-v1" ? block.source_snapshot : null})),
          } : payload,
        }),
      },
    ),
  execute: (plan: WorkspaceRecord<PlanBody>, requestId: string) =>
    apiFetch<Execution>(`/api/v1/workspace/plans/${plan.id}/execute`, {
      method: "POST",
      body: JSON.stringify({
        revision: plan.revision,
        request_id: requestId,
        approve_local_execution: true,
      }),
    }),
  bind: (
    template: WorkspaceRecord<TemplateBody>,
    scope: PreviewScope,
    title: string,
  ) =>
    apiFetch<WorkspaceRecord<PlanBody>>(
      `/api/v1/workspace/templates/${template.id}/instantiate`,
      {
        method: "POST",
        body: JSON.stringify({ revision: template.revision, scope, title }),
      },
    ),
  executions: (planId: string, signal?: AbortSignal) =>
    apiFetch<{ executions: Execution[] }>(
      `/api/v1/workspace/executions?plan_id=${planId}`,
      { signal },
    ),
};
