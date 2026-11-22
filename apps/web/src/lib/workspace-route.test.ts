import { expect, it } from "vitest";
import { workspaceRouteTarget } from "./workspace-route";
import type { TaskRecord } from "./use-workspace-task";
const tasks = [{ id: "task-1", payload: { analysis_ids: ["a"], figure_ids: ["f"], plot_run_ids: ["p"] } }] as TaskRecord[];
it.each([["object=a", "analysisId", "a"], ["sourceId=f", "figureId", "f"], ["runId=p", "plotRunId", "p"]])("resolves %s to its owning task", (query, field, id) => {
  const result = workspaceRouteTarget(new URLSearchParams(query), tasks);
  expect(result.kind).toBe("task");
  if (result.kind === "task") { expect(result.taskId).toBe("task-1"); expect(result.navigation).toMatchObject({ [field]: id }); }
});
it("opens unlinked sources and explicit history in history", () => {
  expect(workspaceRouteTarget(new URLSearchParams("object=old"), tasks)).toEqual({ kind: "history", sourceId: "old" });
  expect(workspaceRouteTarget(new URLSearchParams("panel=history&object=f"), tasks)).toEqual({ kind: "history", sourceId: "f" });
});
it("preserves task and material links", () => {
  expect(workspaceRouteTarget(new URLSearchParams("task=task-1&analysis=ml"), tasks)).toMatchObject({ kind: "task", taskId: "task-1", navigation: { analysis: "ml" } });
  expect(workspaceRouteTarget(new URLSearchParams("materialId=m"), tasks)).toEqual({ kind: "file", id: "m" });
});
