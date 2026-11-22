import type { PlotDraft, PlotRun } from "./workspace-api";

export function samePlotInput(run: PlotRun, draft: PlotDraft) {
  return (
    run.code === draft.code &&
    run.dpi === draft.dpi &&
    run.scope.project_id === draft.scope.project_id &&
    run.scope.dataset_id === draft.scope.dataset_id &&
    run.scope.dataset_sha256 === draft.scope.dataset_sha256 &&
    run.scope.schema_sha256 === draft.scope.schema_sha256 &&
    JSON.stringify(run.scope.columns ?? []) ===
      JSON.stringify(draft.scope.columns ?? [])
  );
}
