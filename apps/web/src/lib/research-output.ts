import type { AppLocale } from "./i18n";
import { createAppTranslator } from "./i18n-messages";
import { workflowStatusLabel } from "./workflow-labels";
import type { StatisticalRun } from "./types";
import type {
  AnalysisDraft,
  FigureData,
  FigureSpec,
  WorkspaceRecord,
} from "./workspace-api";
import {
  displayValue,
  object,
  researchPresentation,
  summaryKind,
} from "./research-results";
import {
  assertFigureSource,
  escapeMarkup as escape,
  researchFigureSvg,
} from "./research-figure";
import { figureSvg } from "./workspace-figure";

type Work = WorkspaceRecord<AnalysisDraft | FigureSpec>;
function validate(
  record: Work,
  run: StatisticalRun,
  snapshot?: WorkspaceRecord<FigureData> | null,
) {
  const scope = record.payload.scope;
  if (
    record.project_id !== run.project_id ||
    run.id !== scope.statistical_run_id ||
    run.dataset_id !== scope.dataset_id ||
    run.dataset_sha256 !== scope.dataset_sha256 ||
    !run.result
  )
    throw new Error("单分析输出来源失效。");
  if (record.payload.kind === "figure" && record.payload.schema_version === 2)
    assertFigureSource(record.payload, run, snapshot);
  return run.result;
}
export function analysisHtml(
  record: Work,
  run: StatisticalRun,
  snapshot?: WorkspaceRecord<FigureData> | null,
  includeObservations = false,
  locale: AppLocale = "zh-CN",
): string {
  const t = createAppTranslator(locale);
  const result = validate(record, run, snapshot),
    presentation = researchPresentation(result, run.input_config, locale);
  let svg = "",
    figureNote = t("research.output.noFigure");
  if (record.payload.kind === "figure") {
    const spec = record.payload;
    if (spec.chart_type === "correlation_scatter" && !includeObservations) {
      figureNote = t("research.output.omitScatter");
    } else {
      svg =
        spec.schema_version === 2
          ? researchFigureSvg(spec, run, snapshot, {
              omitOutliers: true,
              locale,
            })
          : figureSvg(spec, run, undefined, locale);
      figureNote =
        spec.chart_type === "correlation_scatter"
          ? t("research.output.includeScatter", {
              displayed: snapshot!.payload.displayed_n,
              total: snapshot!.payload.n,
            })
          : spec.chart_type === "distribution"
            ? t("research.output.distribution")
            : t("research.output.summaryFigure");
    }
  }
  const tables = presentation.tables
    .map(
      (table) =>
        `<table><caption>${escape(table.caption)}</caption><thead><tr>${table.columns.map((c) => `<th scope="col">${escape(c.label)}</th>`).join("")}</tr></thead><tbody>${table.rows.map((row) => `<tr>${table.columns.map((c, i) => `<${i ? "td" : 'th scope="row"'}>${escape(displayValue(row[c.key], c.kind, locale))}</${i ? "td" : "th"}>`).join("")}</tr>`).join("")}</tbody></table>`,
    )
    .join("");
  const fields = Object.entries(presentation.summary)
    .map(
      ([label, value]) =>
        `<dt>${escape(presentation.summaryLabels[label] ?? label)}</dt><dd>${escape(displayValue(value, summaryKind(label), locale))}</dd>`,
    )
    .join("");
  const parameters = Object.entries(run.input_config ?? {})
    .filter(([key]) =>
      [
        "method_id",
        "variables",
        "target",
        "group_variable",
        "categorical_variables",
        "reference_categories",
        "missing_policy",
        "alternative",
        "confidence_level",
        "paired",
        "standardize",
      ].includes(key),
    )
    .map(
      ([key, value]) =>
        `<dt>${escape(key)}</dt><dd>${escape(displayValue(value, "text", locale))}</dd>`,
    )
    .join("");
  const warnings = [
    ...presentation.notes,
    ...(result.warnings ?? []),
    ...(result.limitations ?? []),
    ...(result.assumption_checks ?? []).map(
      (c) =>
        `${c.passed ? t("research.output.passed") : t("research.output.check")} · ${c.name}：${c.detail}`,
    ),
  ];
  return `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'"><title>${escape(record.payload.title)} · ${escape(t("research.output.single"))}</title><style>body{font:16px/1.6 Arial,'Microsoft YaHei',sans-serif;color:#172337;background:white;max-width:1100px;margin:32px auto;padding:0 24px}h1{font-size:24px;overflow-wrap:anywhere}h2{font-size:19px;margin-top:28px}table{width:100%;border-collapse:collapse;margin:20px 0;font-variant-numeric:tabular-nums}caption{text-align:left;font-weight:bold}th,td{padding:8px;text-align:right;border-bottom:1px solid #ccd4df}th:first-child{text-align:left}thead{background:#edf1f6}svg{width:100%;height:auto}dl{display:grid;grid-template-columns:minmax(140px,1fr) 3fr;gap:4px 16px}dd{margin:0;overflow-wrap:anywhere}p{max-width:75ch}.source{font-size:13px;overflow-wrap:anywhere}@media print{body{margin:0}}</style></head><body><h1>${escape(record.payload.title)}</h1><p>${escape(t("research.output.single"))} · ${escape(presentation.name)} · ${escape(t("research.output.status"))} ${escape(workflowStatusLabel(result.status, t))} · ${escape(t("research.output.engine"))} ${escape(result.method_version)}</p><p>${result.sample ? `${escape(t("research.output.sample", { original: result.sample.original_n, excluded: result.sample.excluded_n, valid: result.sample.final_n }))} · ${escape(result.sample.reason)}` : escape(t("research.output.noSample"))}</p>${svg}<p>${escape(figureNote)}</p><h2>${escape(t("research.output.savedResults"))}</h2><dl>${fields}</dl>${tables || `<p>${escape(t("research.output.noTemplate"))}</p>`}<h2>${escape(t("research.output.parameters"))}</h2><dl>${parameters}</dl>${warnings.map((note) => `<p>${escape(note)}</p>`).join("")}<h2>${escape(t("research.output.source"))}</h2><p class="source">${escape(t("research.output.work"))} ${escape(record.id)} · revision ${record.revision}<br>Run ${escape(run.id)} · ${escape(t("research.output.data"))} ${escape(run.dataset_id)}<br>${escape(t("research.output.dataSha"))} ${escape(run.dataset_sha256)}<br>Schema SHA256 ${escape(record.payload.scope.schema_sha256 ?? t("research.output.notReturned"))}<br>${snapshot ? `${escape(t("research.output.snapshot"))} ${escape(snapshot.id)} · data SHA256 ${escape(snapshot.payload.content_sha256)} · ${escape(snapshot.payload.engine_version)}` : escape(t("research.output.noSnapshot"))}</p><p class="source">${escape(t("research.output.software"))}${escape(JSON.stringify(result.software_versions ?? {}))}</p><p>${escape(t("research.output.offline"))}</p></body></html>`;
}

function csvText(value: string): string {
  let safe = Array.from(value)
    .map((char) =>
      char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? " " : char,
    )
    .join("");
  if (/^[\s\uFEFF]*[=+\-@]/u.test(safe)) safe = "'" + safe;
  return `"${safe.replaceAll('"', '""')}"`;
}
const pick = (value: unknown, keys: string[]) =>
  Object.fromEntries(
    keys
      .filter((key) => key in object(value))
      .map((key) => [key, object(value)[key]]),
  );
export function csvAggregates(run: StatisticalRun) {
  const result = run.result!,
    est = result.estimates ?? {},
    ds = result.descriptive_statistics ?? {};
  const out: Record<string, unknown> = pick(result, [
    "method",
    "method_version",
    "status",
    "p_value",
    "alternative",
    "confidence_level",
    "warnings",
    "limitations",
    "software_versions",
  ]);
  out.sample = pick(result.sample, [
    "original_n",
    "excluded_n",
    "final_n",
    "reason",
    "per_variable",
  ]);
  out.statistic = pick(result.statistic, [
    "name",
    "value",
    "n",
    "df",
    "df_between",
    "df_within",
  ]);
  out.effect_size = pick(result.effect_size, [
    "name",
    "value",
    "interpretation",
    "cohens_d",
    "hedges_g",
    "cohens_d_paired",
    "magnitude",
    "eta_squared",
    "omega_squared",
    "cramers_v",
  ]);
  const interval = object(result.confidence_interval);
  if (result.method === "welch_t")
    out.confidence_interval = pick(interval, [
      "schema_version",
      "estimand",
      "direction",
      "group_order",
      "lower",
      "upper",
      "lower_unbounded",
      "upper_unbounded",
      "confidence_level",
      "alternative",
      "df",
      "method",
    ]);
  else if (["pearson", "spearman"].includes(result.method))
    out.confidence_interval = {
      correlation: pick(interval.correlation, [
        "lower",
        "upper",
        "confidence_level",
      ]),
    };
  if (result.method === "descriptive") {
    out.descriptive_statistics = {
      variables: Object.fromEntries(
        Object.entries(object(ds.variables)).map(([key, value]) => [
          key,
          {
            ...pick(value, [
              "count",
              "mean",
              "median",
              "std",
              "variance",
              "min",
              "max",
              "q1",
              "q3",
              "iqr",
              "skewness",
              "kurtosis",
            ]),
            mean_ci: pick(object(value).mean_ci, [
              "lower",
              "upper",
              "confidence_level",
            ]),
          },
        ]),
      ),
    };
  } else if (["welch_t", "one_way_anova"].includes(result.method)) {
    out.descriptive_statistics = Object.fromEntries(
      Object.entries(ds).map(([key, value]) => [
        key,
        pick(value, ["n", "mean", "std"]),
      ]),
    );
    out.estimates =
      result.method === "welch_t"
        ? pick(est, ["mean_difference", "group_1_mean", "group_2_mean"])
        : Object.fromEntries(
            Object.entries(est).filter(
              ([, value]) => typeof value === "number",
            ),
          );
  } else if (result.method === "paired_t")
    out.estimates = pick(est, ["mean_difference"]);
  else if (["pearson", "spearman"].includes(result.method))
    out.estimates = pick(est, ["correlation"]);
  else if (result.method === "chi_square_independence")
    out.estimates = pick(est, ["rows", "columns", "observed", "expected"]);
  else if (result.method === "linear_regression") {
    out.estimates = {
      coefficients: Array.isArray(est.coefficients)
        ? est.coefficients.map((value) =>
            pick(value, [
              "term",
              "coefficient",
              "std_error",
              "t",
              "p_value",
              "ci_lower",
              "ci_upper",
            ]),
          )
        : [],
      encoding: Object.fromEntries(
        Object.entries(object(est.encoding)).map(([key, value]) => [
          key,
          pick(value, ["reference", "encoded_levels", "all_levels"]),
        ]),
      ),
    };
    out.descriptive_statistics = pick(ds, [
      "n",
      "r_squared",
      "adj_r_squared",
      "f_statistic",
      "f_pvalue",
      "aic",
      "bic",
      "vif",
      "residual_std",
    ]);
  }
  return out;
}
/** Explicit aggregate allowlist; diagnostics and plot point arrays cannot enter CSV. */
export function analysisCsv(
  record: Work,
  run: StatisticalRun,
  snapshot?: WorkspaceRecord<FigureData> | null,
): string {
  validate(record, run, snapshot);
  const rows: (string | number)[][] = [
    ["path", "value", "value_type", "text_encoding"],
  ];
  const visit = (path: string, value: unknown) => {
    if (value && typeof value === "object") {
      if (Array.isArray(value))
        value.forEach((v, i) => visit(`${path}.${i}`, v));
      else
        Object.entries(object(value)).forEach(([key, v]) =>
          visit(`${path}.${key}`, v),
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
  visit("source", {
    title: record.payload.title,
    record_id: record.id,
    revision: record.revision,
    run_id: run.id,
    dataset_id: run.dataset_id,
    dataset_sha256: run.dataset_sha256,
    schema_sha256: record.payload.scope.schema_sha256,
    snapshot_id: snapshot?.id,
    snapshot_data_sha256: snapshot?.payload.content_sha256,
  });
  visit("result", csvAggregates(run));
  for (const key of [
    "method_id",
    "variables",
    "target",
    "group_variable",
    "categorical_variables",
    "reference_categories",
    "missing_policy",
    "alternative",
    "confidence_level",
    "paired",
    "standardize",
  ])
    visit(`input.${key}`, run.input_config?.[key]);
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
