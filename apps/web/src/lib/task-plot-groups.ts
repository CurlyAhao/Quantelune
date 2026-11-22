import type { PlotRun, WorkspaceRecord } from "./workspace-api";
export type TaskPlotRun = WorkspaceRecord<PlotRun>;
export function plotGroupId(run: TaskPlotRun, runs: TaskPlotRun[], history: Record<string, string[]> = {}, seen = new Set<string>()): string {
  if (typeof run.payload.metadata.style_root_draft_id === "string") return run.payload.metadata.style_root_draft_id;
  const savedGroup = Object.keys(history).find(key => history[key].includes(run.id));
  if (savedGroup) return savedGroup;
  if (seen.has(run.id)) return run.payload.draft_id;
  seen.add(run.id);
  const parent = runs.find(item => item.id === run.payload.metadata.style_parent_id);
  return parent ? plotGroupId(parent, runs, history, seen) : run.payload.draft_id;
}
export function groupTaskPlots(runs: TaskPlotRun[], history: Record<string, string[]> = {}) {
  const groups = new Map<string, TaskPlotRun[]>();
  for (const run of runs) {
    const id = plotGroupId(run, runs, history);
    groups.set(id, [...(groups.get(id) ?? []), run]);
  }
  return [...groups.entries()].map(([id, versions]) => ({ id, versions,
    order: [...new Set([...(history[id] ?? []), ...versions.map(row => row.id)])],
    current: versions.filter(item => item.payload.status === "completed").at(-1) ?? versions.at(-1)!,
    completed: versions.some(item => item.payload.status === "completed") }));
}

/** Identify the drawing calls in the saved run, independently of its editable title. */
export function plotCodeKind(code: string): string | null {
  const calls = new Set<string>();
  const labels: Record<string,string> = { histplot:"直方图", hist:"直方图", barplot:"柱状图", bar:"柱状图", barh:"柱状图", countplot:"计数图", boxplot:"箱线图", violinplot:"小提琴图", pointplot:"均值图", scatterplot:"散点图", scatter:"散点图", heatmap:"热图", lineplot:"折线图", plot:"折线图", stripplot:"观测点", swarmplot:"观测点" };
  for (const line of code.split("\n")) {
    if (line.trimStart().startsWith("#")) continue;
    const re = /\.([a-zA-Z_]+)\s*\(/g;
    for (const match of line.matchAll(re)) if (labels[match[1]]) calls.add(labels[match[1]]);
  }
  if (calls.size > 1 && calls.has("观测点")) calls.delete("观测点");
  return calls.size ? Array.from(calls).join(" + ") : null;
}
