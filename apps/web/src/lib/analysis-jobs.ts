import { apiFetch, ApiError } from "./api-client";
import { assertActorEpoch, getActorEpoch } from "./auth-session";
import type { MLRun, StatisticalRun, StatisticalRunRequest } from "./types";

export type RunKind =
  | "media_run"
  | "analysis_run"
  | "plot_run"
  | "research"
  | "statistics"
  | "ml"
  | "quality"
  | "workflow";
export type RunEvent = { phase: string; at: string };
export type ProjectRun = {
  kind: RunKind;
  id: string;
  title: string;
  status: string;
  phase: string;
  operation?: "statistics" | "ml";
  archived: boolean;
  recorded_status?: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  filename: string | null;
  dataset_id: string | null;
  revision: number;
  can_cancel: boolean;
  can_retry: boolean;
  events: RunEvent[];
  error_code: string | null;
  error_message: string | null;
  result_href: string;
  edit_href: string;
  configuration?: unknown;
  code?: string;
  dataset_sha256?: string;
};
export type RunPage = {
  items: ProjectRun[];
  total: number;
  active_total: number;
  offset: number;
  limit: number;
};
export type AnalysisJobRecord = {
  id: string;
  project_id: string;
  revision: number;
  payload: {
    kind: "analysis_run";
    operation: "statistics" | "ml";
    dataset_id: string;
    dataset_sha256: string;
    configuration: Record<string, unknown>;
    native_id: string | null;
    status: string;
    error_code: string | null;
    error_message: string | null;
  };
};
const active = new Set([
  "queued",
  "running",
  "cancel_requested",
  "cancel_pending",
]);
export const isActiveRun = (status: string) => active.has(status);

function pause(signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const finish = () => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(finish, 750);
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(new DOMException("Monitoring stopped.", "AbortError"));
    };
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}
function announce(job: AnalysisJobRecord) {
  if (typeof window !== "undefined")
    window.dispatchEvent(
      new CustomEvent("statforge:run-started", {
        detail: { projectId: job.project_id, id: job.id },
      }),
    );
}
async function wait(
  job: AnalysisJobRecord,
  epoch: number,
  signal?: AbortSignal,
) {
  let value = job;
  while (isActiveRun(value.payload.status)) {
    assertActorEpoch(epoch);
    await pause(signal);
    value = await apiFetch<AnalysisJobRecord>(
      `/api/v1/analysis-jobs/${job.id}`,
      { signal },
    );
  }
  assertActorEpoch(epoch);
  if (signal?.aborted)
    throw new DOMException("Monitoring stopped.", "AbortError");
  return value;
}
function stopped(job: AnalysisJobRecord): never {
  throw new ApiError(
    409,
    job.payload.error_message || "分析未完成，请查看运行记录。",
    job.payload.error_code || "ANALYSIS_JOB_FAILED",
  );
}
export const analysisJobApi = {
  read: (id: string, signal?: AbortSignal) =>
    apiFetch<AnalysisJobRecord>(`/api/v1/analysis-jobs/${id}`, { signal }),
  cancel: (id: string) =>
    apiFetch<AnalysisJobRecord>(`/api/v1/analysis-jobs/${id}/cancel`, {
      method: "POST",
    }),
  retry: (id: string) =>
    apiFetch<AnalysisJobRecord>(`/api/v1/analysis-jobs/${id}/retry`, {
      method: "POST",
      body: JSON.stringify({ request_id: crypto.randomUUID() }),
    }),
  async runStatistics(
    dataset: string,
    statistics: StatisticalRunRequest,
  ): Promise<StatisticalRun> {
    const epoch = getActorEpoch();
    const job = await apiFetch<AnalysisJobRecord>(
      `/api/v1/datasets/${dataset}/statistics/jobs`,
      {
        method: "POST",
        body: JSON.stringify({ request_id: crypto.randomUUID(), statistics }),
      },
    );
    announce(job);
    const result = await wait(job, epoch);
    if (typeof window !== "undefined") {
      sessionStorage.setItem(`statforge:analysis-update:${result.project_id}:${result.payload.dataset_id}`, result.id);
      window.dispatchEvent(new CustomEvent("statforge:analysis-finished", {
        detail: {project: result.project_id, dataset: result.payload.dataset_id, id: result.id, status: result.payload.status},
      }));
    }
    if (
      !result.payload.native_id ||
      !["completed", "not_applicable"].includes(result.payload.status)
    )
      return stopped(result);
    return apiFetch<StatisticalRun>(
      `/api/v1/statistics-runs/${result.payload.native_id}`,
    );
  },
  async runML(
    experiment: string,
    signal?: AbortSignal,
    onStarted?: (job: AnalysisJobRecord) => void | Promise<void>,
  ): Promise<MLRun> {
    if (signal?.aborted)
      throw new DOMException("Monitoring stopped.", "AbortError");
    const epoch = getActorEpoch();
    // The start acknowledgement remains observable; stopping a monitor never
    // silently sends a second logical execution or pretends to cancel a worker.
    const job = await apiFetch<AnalysisJobRecord>(
      `/api/v1/ml/experiments/${experiment}/jobs`,
      {
        method: "POST",
        body: JSON.stringify({ request_id: crypto.randomUUID() }),
      },
    );
    assertActorEpoch(epoch);
    announce(job);
    await onStarted?.(job);
    const result = await wait(job, epoch, signal);
    if (
      !result.payload.native_id ||
      !["completed", "failed"].includes(result.payload.status)
    )
      return stopped(result);
    return apiFetch<MLRun>(`/api/v1/ml/runs/${result.payload.native_id}`, {
      signal,
    });
  },
};
export const projectRunsApi = {
  list(
    project: string,
    options: {
      q?: string;
      kind?: string;
      state?: string;
      offset?: number;
      limit?: number;
    } = {},
    signal?: AbortSignal,
  ) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(options))
      if (value !== undefined && value !== "") params.set(key, String(value));
    return apiFetch<RunPage>(`/api/v1/projects/${project}/runs?${params}`, {
      signal,
    });
  },
  detail: (project: string, kind: RunKind, id: string, signal?: AbortSignal) =>
    apiFetch<ProjectRun>(`/api/v1/projects/${project}/runs/${kind}/${id}`, {
      signal,
    }),
  cancel: (project: string, kind: RunKind, id: string) =>
    apiFetch<ProjectRun>(
      `/api/v1/projects/${project}/runs/${kind}/${id}/cancel`,
      { method: "POST" },
    ),
};
export function runStatusLabel(status: string, locale: string) {
  const labels: Record<string, [string, string]> = {
    queued: ["排队中", "Queued"],
    running: ["运行中", "Running"],
    cancel_requested: ["取消中", "Cancelling"],
    cancel_pending: ["取消中", "Cancelling"],
    completed: ["已完成", "Completed"],
    completed_with_errors: ["已完成（有问题）", "Completed with issues"],
    failed: ["未完成", "Failed"],
    cancelled: ["已取消", "Cancelled"],
    interrupted: ["已中断", "Interrupted"],
    not_applicable: ["不适用", "Not applicable"],
    unconfirmed: ["待核对", "Unconfirmed"],
    snapshot: ["运行快照", "Saved snapshot"],
    waiting_approval: ["等待确认", "Awaiting confirmation"],
    paused: ["已暂停", "Paused"],
    needs_review: ["待处理", "Needs attention"],
    recovering: ["恢复中", "Recovering"],
    pending: ["待处理", "Pending"],
  };
  return labels[status]?.[locale === "zh-CN" ? 0 : 1] || status;
}
export function runPhaseLabel(phase: string, locale: string) {
  const labels: Record<string, [string, string]> = {
    queued: ["进入队列", "Queued"],
    prepare: ["检查数据", "Checking data"],
    compute: ["分析数据", "Analysing data"],
    publish: ["保存结果", "Saving results"],
  };
  return (
    labels[phase]?.[locale === "zh-CN" ? 0 : 1] || runStatusLabel(phase, locale)
  );
}
