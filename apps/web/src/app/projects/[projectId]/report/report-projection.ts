import type {
  Dataset,
  StatisticalRun,
  StatisticalRunSummary,
} from "@/lib/types";

const INPUT_KEYS = [
  "method_id",
  "variables",
  "group_variable",
  "paired",
  "mu",
  "expected_proportions",
  "alternative",
  "confidence_level",
  "missing_policy",
  "missing_value_policy",
  "target",
  "categorical_variables",
  "reference_categories",
  "standardize",
  "alpha",
  "l1_ratio",
];

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function inputParameters(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    INPUT_KEYS.filter((key) => Object.hasOwn(value, key)).map((key) => [
      key,
      value[key],
    ]),
  );
}

export function safeAggregate(value: unknown, depth = 0): unknown {
  if (depth > 5) return "Nested value omitted";
  if (typeof value === "number")
    return Number.isFinite(value) ? value : "Non-finite value omitted";
  if (Array.isArray(value))
    return value.map((item) => safeAggregate(item, depth + 1));
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        safeAggregate(item, depth + 1),
      ]),
    );
  }
  if (typeof value === "string" || typeof value === "boolean" || value === null)
    return value;
  return "Unavailable";
}

export function storedText(value: unknown): string {
  if (value === null || value === undefined) return "Not recorded";
  if (typeof value === "number")
    return Number.isFinite(value) ? String(value) : "Non-finite value omitted";
  if (typeof value === "string" || typeof value === "boolean")
    return String(value);
  return JSON.stringify(safeAggregate(value));
}

export function reportMismatch(
  projectId: string,
  dataset: Dataset,
  summary: StatisticalRunSummary | undefined,
  run: StatisticalRun,
  requestedRunId: string | null | undefined = summary?.id,
): string | null {
  if (
    run.id !== requestedRunId ||
    run.project_id !== projectId ||
    run.dataset_id !== dataset.id ||
    dataset.project_id !== projectId ||
    run.dataset_sha256 !== dataset.sha256 ||
    (summary &&
      (run.id !== summary.id ||
        summary.dataset_id !== dataset.id ||
        run.method !== summary.method ||
        run.methodset_version !== summary.methodset_version ||
        run.status !== summary.status))
  )
    return "Saved run identity or state does not match the selected project, dataset, or history record.";
  if (
    run.result &&
    (run.result.status !== run.status || run.result.method !== run.method)
  ) {
    return "Saved result method or terminal state does not match its run.";
  }
  if (run.status === "completed" && !run.result) {
    return "This run is marked completed, but its saved result is missing. No report can be shown.";
  }
  return null;
}
