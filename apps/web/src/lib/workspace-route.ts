import type { TaskRecord } from "@/lib/use-workspace-task";

/** Resolve saved links against the current task inventory; unmatched sources remain in history. */
export function workspaceRouteTarget(params: Pick<URLSearchParams, "get">, tasks: TaskRecord[]) {
  const source = params.get("object") || params.get("sourceId") || params.get("analysisId") || params.get("plotId") || params.get("runId");
  const taskId = params.get("task") || tasks.find(row => source && [...row.payload.analysis_ids, ...row.payload.figure_ids, ...(row.payload.plot_run_ids ?? [])].includes(source))?.id;
  if (params.get("panel") === "history" || (source && !taskId)) return { kind: "history" as const, sourceId: source || "" };
  if (taskId) {
    const task = tasks.find(row => row.id === taskId);
    const analysis = params.get("analysis");
    return { kind: "task" as const, taskId, navigation: {
      analysis: analysis === "ml" ? "ml" as const : analysis === "statistics" ? "statistics" as const : undefined,
      analysisId: source && task?.payload.analysis_ids.includes(source) ? source : undefined,
      plotRunId: source && task?.payload.plot_run_ids?.includes(source) ? source : undefined,
      figureId: source && task?.payload.figure_ids.includes(source) ? source : undefined,
    } };
  }
  const materialId = params.get("materialId");
  if (materialId) return { kind: "file" as const, id: materialId };
  if (params.get("analysis")) return { kind: "history" as const, sourceId: "" };
  return { kind: "none" as const };
}
