import type { AppLocale } from "./i18n";
import { createAppTranslator } from "./i18n-messages";
import { escapeMarkup as escape } from "./research-figure";
import { csvAggregates } from "./research-output";
import {
  displayValue,
  object,
  researchPresentation,
  summaryKind,
} from "./research-results";
import type { Dataset, StatisticalResult, StatisticalRun } from "./types";

/** A saved Run is its own source; no workspace record or new Artifact is invented. */
export function assertSavedRunSource(
  projectId: string,
  dataset: Dataset,
  run: StatisticalRun,
) {
  if (
    !projectId ||
    !dataset.id ||
    !run.id ||
    dataset.project_id !== projectId ||
    run.project_id !== projectId ||
    run.dataset_id !== dataset.id ||
    run.dataset_sha256 !== dataset.sha256 ||
    !/^[a-f0-9]{64}$/i.test(dataset.sha256) ||
    dataset.upload_status !== "ready" ||
    run.status !== "completed" ||
    !run.result ||
    run.result.status !== "completed" ||
    run.result.method !== run.method ||
    !run.method ||
    !run.methodset_version ||
    !run.result.method_version
  )
    throw new Error("Saved Run source is unavailable or changed.");
  return run.result;
}

const INPUT_KEYS = [
  "method_id",
  "variables",
  "target",
  "group_variable",
  "categorical_variables",
  "reference_categories",
  "missing_policy",
  "missing_value_policy",
  "alternative",
  "confidence_level",
  "paired",
  "standardize",
  "mu",
  "expected_proportions",
  "alpha",
  "l1_ratio",
] as const;

function inputs(run: StatisticalRun) {
  return Object.fromEntries(
    INPUT_KEYS.flatMap((key) => {
      const value = run.input_config?.[key];
      const scalar = (item: unknown) =>
        item === null ||
        typeof item === "string" ||
        typeof item === "boolean" ||
        (typeof item === "number" && Number.isFinite(item));
      if (scalar(value) || (Array.isArray(value) && value.every(scalar)))
        return [[key, value]];
      if (
        ["reference_categories", "expected_proportions"].includes(key) &&
        value &&
        !Array.isArray(value) &&
        typeof value === "object" &&
        Object.values(value).every(scalar)
      )
        return [[key, value]];
      return [];
    }),
  );
}

function source(projectId: string, dataset: Dataset, run: StatisticalRun) {
  return {
    project_id: projectId,
    run_id: run.id,
    dataset_id: dataset.id,
    dataset_sha256: dataset.sha256,
    dataset_filename: dataset.original_filename,
    method: run.method,
    methodset_version: run.methodset_version,
    method_version: run.result!.method_version,
    created_at: run.created_at,
    started_at: run.started_at,
    completed_at: run.completed_at,
    missing_value_policy: run.missing_value_policy,
  };
}

/** Static aggregate-only report: never serializes findings, diagnostics or observations. */
export function savedRunHtml(
  projectId: string,
  dataset: Dataset,
  run: StatisticalRun,
  locale: AppLocale,
) {
  assertSavedRunSource(projectId, dataset, run);
  const t = createAppTranslator(locale);
  // Present only the existing aggregate allowlist, including for effect-size fields.
  const result = csvAggregates(run) as unknown as StatisticalResult;
  const config = inputs(run);
  const presentation = researchPresentation(result, config, locale);
  const title = `${presentation.name} · Run ${run.id}`;
  const fields = Object.entries(presentation.summary)
    .map(
      ([label, value]) =>
        `<dt>${escape(presentation.summaryLabels[label] ?? label)}</dt><dd>${escape(displayValue(value, summaryKind(label), locale))}</dd>`,
    )
    .join("");
  const tables = presentation.tables
    .map(
      (table) =>
        `<table><caption>${escape(table.caption)}</caption><thead><tr>${table.columns.map((column) => `<th scope="col">${escape(column.label)}</th>`).join("")}</tr></thead><tbody>${table.rows.map((row) => `<tr>${table.columns.map((column, index) => `<${index ? "td" : 'th scope="row"'}>${escape(displayValue(row[column.key], column.kind, locale))}</${index ? "td" : "th"}>`).join("")}</tr>`).join("")}</tbody></table>`,
    )
    .join("");
  const describe = (value: Record<string, unknown>) =>
    Object.entries(value)
      .map(
        ([key, item]) =>
          `<dt>${escape(key)}</dt><dd>${escape(displayValue(item, "text", locale))}</dd>`,
      )
      .join("");
  const notes = [
    ...presentation.notes,
    ...(result.warnings ?? []),
    ...(result.limitations ?? []),
  ];
  return `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'"><title>${escape(title)}</title><style>body{font:16px/1.6 Arial,'Microsoft YaHei',sans-serif;color:#283c34;background:white;max-width:1100px;margin:32px auto;padding:0 24px}h1{font-size:24px;overflow-wrap:anywhere}h2{font-size:19px;margin-top:28px}table{width:100%;border-collapse:collapse;margin:20px 0;font-variant-numeric:tabular-nums}caption{text-align:left;font-weight:bold}th,td{padding:8px;text-align:right;border-bottom:1px solid #dedfd7}th:first-child{text-align:left}thead{background:#e8ece3}dl{display:grid;grid-template-columns:minmax(140px,1fr) 3fr;gap:4px 16px}dd{margin:0;overflow-wrap:anywhere}p{max-width:75ch}@media print{body{margin:0}}</style></head><body><h1>${escape(title)}</h1><p>${escape(t("research.output.single"))} · ${escape(t("research.output.status"))}: ${escape(result.status)}</p><h2>${escape(t("research.output.savedResults"))}</h2><dl>${fields}</dl>${tables || `<p>${escape(t("research.output.noTemplate"))}</p>`}<h2>${escape(t("research.output.parameters"))}</h2><dl>${describe(config)}</dl>${notes.map((note) => `<p>${escape(note)}</p>`).join("")}<h2>${escape(t("research.output.source"))}</h2><dl>${describe(source(projectId, dataset, run))}</dl><h2>${escape(t("research.output.software"))}</h2><dl>${describe(result.software_versions ?? {})}</dl><p>${escape(t("research.output.offline"))}</p></body></html>`;
}

// Match the existing summary CSV policy: preserve numeric precision; prefix text formulas.
function csvText(value: string) {
  let safe = Array.from(value)
    .map((char) =>
      char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? " " : char,
    )
    .join("");
  if (/^[\s\uFEFF]*[=+\-@]/u.test(safe)) safe = "'" + safe;
  return `"${safe.replaceAll('"', '""')}"`;
}

export function savedRunCsv(
  projectId: string,
  dataset: Dataset,
  run: StatisticalRun,
) {
  assertSavedRunSource(projectId, dataset, run);
  const rows: (string | number)[][] = [
    ["path", "value", "value_type", "text_encoding"],
  ];
  const visit = (path: string, value: unknown) => {
    if (value && typeof value === "object") {
      if (Array.isArray(value))
        value.forEach((item, index) => visit(`${path}.${index}`, item));
      else
        Object.entries(object(value)).forEach(([key, item]) =>
          visit(`${path}.${key}`, item),
        );
    } else
      rows.push([
        path,
        typeof value === "number" && Number.isFinite(value)
          ? value
          : value == null
            ? ""
            : String(value),
        typeof value === "number" ? "number" : "text",
        "UTF-8 BOM; RFC4180; control-to-space; formula-text-prefix-apostrophe",
      ]);
  };
  visit("source", source(projectId, dataset, run));
  visit("input", inputs(run));
  visit("result", csvAggregates(run));
  return (
    "\uFEFF" +
    rows
      .map((row) =>
        row
          .map((value) =>
            typeof value === "number" ? String(value) : csvText(value),
          )
          .join(","),
      )
      .join("\r\n") +
    "\r\n"
  );
}
