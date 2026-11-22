/**
 * Typed endpoint functions for the StatForge backend API.
 * All calls go through apiFetch / apiUpload (cookie auth, refresh handling).
 */

import { apiFetch, apiUpload, type UploadOptions } from "@/lib/api-client";
import { analysisJobApi, type AnalysisJobRecord } from "./analysis-jobs";
import { downloadApiFile, type SaveOutcome } from "@/lib/api-download";
import type {
  AIReviewResult,
  AIStatus,
  AuthResponse,
  Dataset,
  DatasetPreview,
  DesktopDiagnostics,
  GraphValidation,
  MethodsResponse,
  MLExperiment,
  MLExperimentCreateRequest,
  MLMetadata,
  MLPreflightStats,
  MLRun,
  MLRunSummary,
  Profile,
  Workflow,
  WorkflowCatalog,
  WorkflowGraph,
  WorkflowRun,
  WorkflowRunSummary,
  WorkflowDeletion,
  WorkflowDeletionPage,
  ProfileColumn,
  ProfileCorrelations,
  ProfileMissingness,
  Project,
  ProjectCreate,
  ProjectUpdate,
  QualityFindingsResponse,
  QualityRun,
  QualityRunRequest,
  QualityRunsResponse,
  StatisticalRun,
  StatisticalRunRequest,
  StatisticalRunsResponse,
  User,
  ValidationResult,
} from "@/lib/types";

export const desktopApi = {
  diagnostics(signal?: AbortSignal): Promise<DesktopDiagnostics> {
    return apiFetch<DesktopDiagnostics>("/api/v1/desktop/diagnostics", {
      signal,
    });
  },
};

/* ------------------------------------------------------------------ */
/* Auth — /api/v1/auth                                                 */
/* ------------------------------------------------------------------ */

export const authApi = {
  login(email: string, password: string): Promise<AuthResponse> {
    return apiFetch<AuthResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  register(
    email: string,
    password: string,
    displayName?: string,
  ): Promise<AuthResponse> {
    return apiFetch<AuthResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        ...(displayName ? { display_name: displayName } : {}),
      }),
    });
  },

  /**
   * GET /auth/me. With `silent: true` a failed session check throws instead of
   * redirecting to /login — required for probes on public pages.
   */
  me(signal?: AbortSignal, options?: { silent?: boolean }): Promise<User> {
    return apiFetch<User>("/api/v1/auth/me", {
      signal,
      skipAuthRedirect: options?.silent,
    });
  },

  refresh(): Promise<AuthResponse> {
    return apiFetch<AuthResponse>("/api/v1/auth/refresh", { method: "POST" });
  },

  logout(): Promise<void> {
    return apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
  },
};

/* ------------------------------------------------------------------ */
/* Projects — /api/v1/projects                                         */
/* ------------------------------------------------------------------ */

export const projectsApi = {
  openFile(file: File, signal?: AbortSignal, asCopy = false): Promise<Project> {
    const form = new FormData();
    form.append("file", file);
    form.append("as_copy", String(asCopy));
    return apiUpload<Project>("/api/v1/projects/open-file", form, { signal });
  },
  list(signal?: AbortSignal): Promise<Project[]> {
    return apiFetch<Project[]>("/api/v1/projects", { signal });
  },

  create(data: ProjectCreate): Promise<Project> {
    return apiFetch<Project>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  get(id: string, signal?: AbortSignal): Promise<Project> {
    return apiFetch<Project>(`/api/v1/projects/${id}`, { signal });
  },

  update(id: string, data: ProjectUpdate): Promise<Project> {
    return apiFetch<Project>(`/api/v1/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  remove(id: string): Promise<void> {
    return apiFetch<void>(`/api/v1/projects/${id}`, { method: "DELETE" });
  },
};

/* ------------------------------------------------------------------ */
/* Datasets — /api/v1/projects/{id}/datasets + /api/v1/datasets        */
/* ------------------------------------------------------------------ */

export interface DatasetUploadOptions extends UploadOptions {
  sheetName?: string;
  csvEncoding?: string;
  sourcePath?: string;
}

export const datasetsApi = {
  sheets(file: File, signal?: AbortSignal): Promise<{ sheet_names: string[] }> {
    const form = new FormData();
    form.append("file", file);
    return apiUpload("/api/v1/datasets/excel-sheets", form, { signal });
  },
  list(projectId: string, signal?: AbortSignal): Promise<Dataset[]> {
    return apiFetch<Dataset[]>(`/api/v1/projects/${projectId}/datasets`, {
      signal,
    });
  },

  upload(
    projectId: string,
    file: File,
    options?: DatasetUploadOptions,
  ): Promise<Dataset> {
    const formData = new FormData();
    formData.append("file", file, file.name.split(/[\\/]/).pop() || file.name);
    if (options?.sourcePath) formData.append("source_path", options.sourcePath);
    if (options?.sheetName) {
      formData.append("sheet_name", options.sheetName);
    }
    if (options?.csvEncoding)
      formData.append("csv_encoding", options.csvEncoding);
    return apiUpload<Dataset>(
      `/api/v1/projects/${projectId}/datasets/upload`,
      formData,
      options,
    );
  },

  get(id: string, signal?: AbortSignal): Promise<Dataset> {
    return apiFetch<Dataset>(`/api/v1/datasets/${id}`, { signal });
  },

  preview(
    id: string,
    rows = 20,
    signal?: AbortSignal,
  ): Promise<DatasetPreview> {
    return apiFetch<DatasetPreview>(
      `/api/v1/datasets/${id}/preview?n=${rows}`,
      {
        signal,
      },
    );
  },

  remove(id: string): Promise<void> {
    return apiFetch<void>(`/api/v1/datasets/${id}`, { method: "DELETE" });
  },

  download(
    id: string,
    filename: string,
    signal?: AbortSignal,
  ): Promise<SaveOutcome> {
    return downloadApiFile(
      `/api/v1/datasets/${encodeURIComponent(id)}/download`,
      filename,
      signal,
    );
  },
};

/* ------------------------------------------------------------------ */
/* Profiling — /api/v1/datasets/{id}/profile                           */
/* ------------------------------------------------------------------ */

export const profilingApi = {
  getRun(datasetId: string, profileId: string, signal?: AbortSignal): Promise<Profile> {
    return apiFetch<Profile>(`/api/v1/datasets/${datasetId}/profiles/${profileId}`, { signal });
  },
  /** Compute a profile (may take a few seconds). Cached by SHA unless force. */
  compute(datasetId: string, force = false): Promise<Profile> {
    return apiFetch<Profile>(
      `/api/v1/datasets/${datasetId}/profile${force ? "?force=true" : ""}`,
      { method: "POST" },
    );
  },

  get(datasetId: string, signal?: AbortSignal): Promise<Profile> {
    return apiFetch<Profile>(`/api/v1/datasets/${datasetId}/profile`, {
      signal,
    });
  },

  getColumns(
    datasetId: string,
    signal?: AbortSignal,
  ): Promise<{ columns: ProfileColumn[] }> {
    return apiFetch<{ columns: ProfileColumn[] }>(
      `/api/v1/datasets/${datasetId}/profile/columns`,
      { signal },
    );
  },

  getColumn(
    datasetId: string,
    columnName: string,
    signal?: AbortSignal,
  ): Promise<ProfileColumn> {
    return apiFetch<ProfileColumn>(
      `/api/v1/datasets/${datasetId}/profile/columns/${encodeURIComponent(columnName)}`,
      { signal },
    );
  },

  getCorrelations(
    datasetId: string,
    signal?: AbortSignal,
  ): Promise<ProfileCorrelations> {
    return apiFetch<ProfileCorrelations>(
      `/api/v1/datasets/${datasetId}/profile/correlations`,
      { signal },
    );
  },

  getMissingness(
    datasetId: string,
    signal?: AbortSignal,
  ): Promise<ProfileMissingness> {
    return apiFetch<ProfileMissingness>(
      `/api/v1/datasets/${datasetId}/profile/missingness`,
      { signal },
    );
  },
};

/* ------------------------------------------------------------------ */
/* Data quality — /api/v1/datasets/{id}/quality + /api/v1/quality-runs */
/* ------------------------------------------------------------------ */

export interface QualityFindingsParams {
  category?: string;
  severity?: string;
  status?: string;
  column?: string;
  page?: number;
  pageSize?: number;
}

export const qualityApi = {
  /** Start a quality run. 422 INVALID_THRESHOLD when a threshold is bad. */
  run(datasetId: string, request?: QualityRunRequest): Promise<QualityRun> {
    return apiFetch<QualityRun>(`/api/v1/datasets/${datasetId}/quality/run`, {
      method: "POST",
      body: JSON.stringify(request ?? {}),
    });
  },

  /** Force a fresh quality run (bypasses any caching). */
  recompute(
    datasetId: string,
    request?: QualityRunRequest,
  ): Promise<QualityRun> {
    return apiFetch<QualityRun>(
      `/api/v1/datasets/${datasetId}/quality/recompute`,
      {
        method: "POST",
        body: JSON.stringify({ force: true, ...(request ?? {}) }),
      },
    );
  },

  /** Latest run for a dataset; 404 QUALITY_RUN_NOT_FOUND when never run. */
  getLatest(datasetId: string, signal?: AbortSignal): Promise<QualityRun> {
    return apiFetch<QualityRun>(`/api/v1/datasets/${datasetId}/quality`, {
      signal,
    });
  },

  listRuns(
    datasetId: string,
    signal?: AbortSignal,
  ): Promise<QualityRunsResponse> {
    return apiFetch<QualityRunsResponse>(
      `/api/v1/datasets/${datasetId}/quality/runs`,
      { signal },
    );
  },

  getRun(runId: string, signal?: AbortSignal): Promise<QualityRun> {
    return apiFetch<QualityRun>(`/api/v1/quality-runs/${runId}`, { signal });
  },

  listFindings(
    runId: string,
    params?: QualityFindingsParams,
    signal?: AbortSignal,
  ): Promise<QualityFindingsResponse> {
    const search = new URLSearchParams();
    if (params?.category) search.set("category", params.category);
    if (params?.severity) search.set("severity", params.severity);
    if (params?.status) search.set("status", params.status);
    if (params?.column) search.set("column", params.column);
    search.set("page", String(params?.page ?? 1));
    search.set("page_size", String(params?.pageSize ?? 50));
    return apiFetch<QualityFindingsResponse>(
      `/api/v1/quality-runs/${runId}/findings?${search.toString()}`,
      { signal },
    );
  },
};

/* ------------------------------------------------------------------ */
/* Statistics — /api/v1/statistics & /api/v1/statistics-runs           */
/* ------------------------------------------------------------------ */

export const statisticsApi = {
  listMethods(signal?: AbortSignal): Promise<MethodsResponse> {
    return apiFetch<MethodsResponse>("/api/v1/statistics/methods", { signal });
  },

  validate(request: StatisticalRunRequest): Promise<ValidationResult> {
    return apiFetch<ValidationResult>("/api/v1/statistics/validate", {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  /** Start a statistical run. 422 INVALID_METHOD_INPUT on bad configuration. */
  run(
    datasetId: string,
    request: StatisticalRunRequest,
  ): Promise<StatisticalRun> {
    return analysisJobApi.runStatistics(datasetId,request);
  },

  /** Latest run for a dataset; 404 STATISTICAL_RUN_NOT_FOUND when never run. */
  getLatest(
    datasetId: string,
    method: string | null,
    signal?: AbortSignal,
  ): Promise<StatisticalRun> {
    const search = method ? `?method=${encodeURIComponent(method)}` : "";
    return apiFetch<StatisticalRun>(
      `/api/v1/datasets/${datasetId}/statistics${search}`,
      {
        signal,
      },
    );
  },

  listRuns(
    datasetId: string,
    params?: { method?: string | null; page?: number; pageSize?: number },
    signal?: AbortSignal,
  ): Promise<StatisticalRunsResponse> {
    const search = new URLSearchParams();
    if (params?.method) search.set("method", params.method);
    search.set("page", String(params?.page ?? 1));
    search.set("page_size", String(params?.pageSize ?? 50));
    return apiFetch<StatisticalRunsResponse>(
      `/api/v1/datasets/${datasetId}/statistics/runs?${search.toString()}`,
      { signal },
    );
  },

  getRun(runId: string, signal?: AbortSignal): Promise<StatisticalRun> {
    return apiFetch<StatisticalRun>(`/api/v1/statistics-runs/${runId}`, {
      signal,
    });
  },
};

/* ------------------------------------------------------------------ */
/* Machine learning — /api/v1/ml                                       */
/* ------------------------------------------------------------------ */

export const mlApi = {
  metadata(signal?: AbortSignal): Promise<MLMetadata> {
    return apiFetch<MLMetadata>("/api/v1/ml/metadata", { signal });
  },

  /**
   * Trusted full-data target/group/time statistics for folds suggestions.
   * Never derived from the 5-row preview or sampled profile summaries.
   */
  preflightStats(
    params: {
      datasetId: string;
      target: string;
      taskType?: string;
      splitStrategy?: string;
      groupVariable?: string | null;
      timeVariable?: string | null;
      nSplits?: number;
      testSize?: number;
      randomSeed?: number;
    },
    signal?: AbortSignal,
  ): Promise<MLPreflightStats> {
    const q = new URLSearchParams({
      dataset_id: params.datasetId,
      target: params.target,
    });
    if (params.taskType) q.set("task_type", params.taskType);
    if (params.splitStrategy) q.set("split_strategy", params.splitStrategy);
    if (params.groupVariable) q.set("group_variable", params.groupVariable);
    if (params.timeVariable) q.set("time_variable", params.timeVariable);
    if (params.nSplits != null) q.set("n_splits", String(params.nSplits));
    if (params.testSize != null) q.set("test_size", String(params.testSize));
    if (params.randomSeed != null)
      q.set("random_seed", String(params.randomSeed));
    return apiFetch<MLPreflightStats>(
      `/api/v1/ml/preflight/stats?${q.toString()}`,
      {
        signal,
      },
    );
  },

  createExperiment(
    datasetId: string,
    request: MLExperimentCreateRequest,
    signal?: AbortSignal,
  ): Promise<MLExperiment> {
    return apiFetch<MLExperiment>(
      `/api/v1/datasets/${datasetId}/ml/experiments`,
      {
        method: "POST",
        body: JSON.stringify(request),
        signal,
      },
    );
  },

  listExperiments(
    datasetId: string,
    signal?: AbortSignal,
  ): Promise<MLExperiment[]> {
    return apiFetch<MLExperiment[]>(
      `/api/v1/datasets/${datasetId}/ml/experiments`,
      {
        signal,
      },
    );
  },

  getExperiment(
    experimentId: string,
    signal?: AbortSignal,
  ): Promise<MLExperiment> {
    return apiFetch<MLExperiment>(`/api/v1/ml/experiments/${experimentId}`, {
      signal,
    });
  },

  runExperiment(experimentId: string, signal?: AbortSignal, onStarted?: (job: AnalysisJobRecord)=>void | Promise<void>): Promise<MLRun> {
    return analysisJobApi.runML(experimentId,signal,onStarted);
  },

  listRuns(
    experimentId: string,
    signal?: AbortSignal,
  ): Promise<{ runs: MLRunSummary[]; total: number }> {
    return apiFetch<{ runs: MLRunSummary[]; total: number }>(
      `/api/v1/ml/experiments/${experimentId}/runs`,
      { signal },
    );
  },

  getRun(runId: string, signal?: AbortSignal): Promise<MLRun> {
    return apiFetch<MLRun>(`/api/v1/ml/runs/${runId}`, { signal });
  },

  downloadArtifact(runId: string, signal?: AbortSignal): Promise<SaveOutcome> {
    return downloadApiFile(
      `/api/v1/ml/runs/${encodeURIComponent(runId)}/artifacts`,
      `statforge-ml-run-${runId}.joblib`,
      signal,
    );
  },
};

/* ------------------------------------------------------------------ */
/* Workflow — /api/v1/workflow                                          */
/* ------------------------------------------------------------------ */

export const workflowApi = {
  catalog(signal?: AbortSignal): Promise<WorkflowCatalog> {
    return apiFetch<WorkflowCatalog>("/api/v1/workflow/catalog", { signal });
  },

  create(
    projectId: string,
    body: { name: string; dataset_id?: string | null; graph: WorkflowGraph },
    signal?: AbortSignal,
  ): Promise<Workflow> {
    return apiFetch<Workflow>(`/api/v1/projects/${projectId}/workflows`, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    });
  },

  list(projectId: string, signal?: AbortSignal): Promise<Workflow[]> {
    return apiFetch<Workflow[]>(`/api/v1/projects/${projectId}/workflows`, {
      signal,
    });
  },

  get(workflowId: string, signal?: AbortSignal): Promise<Workflow> {
    return apiFetch<Workflow>(`/api/v1/workflows/${workflowId}`, { signal });
  },

  update(
    workflowId: string,
    body: {
      name?: string;
      dataset_id?: string | null;
      graph?: WorkflowGraph;
      expected_version?: number;
    },
    signal?: AbortSignal,
  ): Promise<Workflow> {
    return apiFetch<Workflow>(`/api/v1/workflows/${workflowId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
      signal,
    });
  },

  validate(workflowId: string, signal?: AbortSignal): Promise<GraphValidation> {
    return apiFetch<GraphValidation>(
      `/api/v1/workflows/${workflowId}/validate`,
      {
        method: "POST",
        signal,
      },
    );
  },

  run(workflowId: string, signal?: AbortSignal): Promise<WorkflowRun> {
    return apiFetch<WorkflowRun>(`/api/v1/workflows/${workflowId}/run`, {
      method: "POST",
      signal,
    });
  },

  getRun(runId: string, signal?: AbortSignal): Promise<WorkflowRun> {
    return apiFetch<WorkflowRun>(`/api/v1/workflow-runs/${runId}`, { signal });
  },

  listRuns(
    workflowId: string,
    signal?: AbortSignal,
  ): Promise<{ runs: WorkflowRunSummary[]; total: number }> {
    return apiFetch(`/api/v1/workflows/${workflowId}/runs`, { signal });
  },

  remove(workflowId: string, signal?: AbortSignal): Promise<WorkflowDeletion> {
    return apiFetch(`/api/v1/workflows/${workflowId}`, {
      method: "DELETE",
      signal,
    });
  },

  pendingDeletions(
    projectId: string,
    signal?: AbortSignal,
    offset = 0,
  ): Promise<WorkflowDeletionPage> {
    return apiFetch(
      `/api/v1/projects/${projectId}/workflow-output-deletions?offset=${offset}`,
      {
        signal,
      },
    );
  },

  retryDeletion(
    receiptId: string,
    signal?: AbortSignal,
  ): Promise<WorkflowDeletion> {
    return apiFetch(`/api/v1/workflow-output-deletions/${receiptId}/cleanup`, {
      method: "POST",
      signal,
    });
  },

  downloadArtifact(
    runId: string,
    artifactId: string,
    filename: string,
    signal?: AbortSignal,
  ): Promise<SaveOutcome> {
    return downloadApiFile(
      `/api/v1/workflow/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`,
      filename,
      signal,
    );
  },
};

/* ------------------------------------------------------------------ */
/* AI assistant — /api/v1/ai                                            */
/* ------------------------------------------------------------------ */

export const aiApi = {
  status(signal?: AbortSignal): Promise<AIStatus> {
    return apiFetch<AIStatus>("/api/v1/ai/status", { signal });
  },

  reviewQuality(data: {
    project_id?: string | null;
    quality_data: Record<string, unknown>;
  }): Promise<AIReviewResult> {
    return apiFetch<AIReviewResult>("/api/v1/ai/review/quality", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  recommendMethod(data: Record<string, unknown>): Promise<AIReviewResult> {
    return apiFetch<AIReviewResult>("/api/v1/ai/recommend/method", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  explainResult(data: {
    project_id?: string | null;
    result: Record<string, unknown>;
  }): Promise<AIReviewResult> {
    return apiFetch<AIReviewResult>("/api/v1/ai/review/statistical-result", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  reviewRegression(data: {
    project_id?: string | null;
    regression_data: Record<string, unknown>;
  }): Promise<AIReviewResult> {
    return apiFetch<AIReviewResult>("/api/v1/ai/review/regression", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  reviewMLEvaluation(data: {
    project_id?: string | null;
    ml_data: Record<string, unknown>;
  }): Promise<AIReviewResult> {
    return apiFetch<AIReviewResult>("/api/v1/ai/review/ml-evaluation", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  reviewLeakage(data: {
    project_id?: string | null;
    leakage_data: Record<string, unknown>;
  }): Promise<AIReviewResult> {
    return apiFetch<AIReviewResult>("/api/v1/ai/review/leakage", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },
};
