import type { AppLocale } from "./i18n";
import { object, researchPresentation, type ResultTable } from "./research-results";
import type { StatisticalRun } from "./types";

function cell(value: unknown): string {
  if (value == null || (typeof value === "number" && !Number.isFinite(value))) return "";
  if (typeof value === "number") return String(value);
  let text = typeof value === "object" ? JSON.stringify(value) : String(value);
  if (/^[\s\uFEFF]*[=+\-@]/u.test(text)) text = "'" + text;
  return `"${text.replaceAll('"', '""')}"`;
}

const labels: Record<string, string> = {
  mean: "均值", std: "标准差", median: "中位数", count: "有效 n", n: "有效 n",
  value: "统计量", name: "名称", df: "自由度", lower: "下限", upper: "上限",
  coefficient: "系数", std_error: "标准误", p_value: "p", min: "最小值", max: "最大值",
  correlation: "相关系数", odds_ratio: "比值比", confidence_level: "置信水平",
};

/** Export the saved result tables, using the same projection as the on-screen results.
 *  Values retain their saved numeric precision. This does not run any computation.
 *  The caller remains responsible for verifying the selected run/data identity.
 */
export function statisticsTableCsv(run: StatisticalRun, locale: AppLocale = "zh-CN"): string {
  if (run.status !== "completed" || !run.result) {
    throw new Error(locale === "zh-CN" ? "请选择已完成的统计结果。" : "Choose a completed statistical result.");
  }
  const zh = locale === "zh-CN";
  const presentation = researchPresentation(run.result, run.input_config, locale);
  const tables: ResultTable[] = [];
  if (Object.keys(presentation.summary).length) {
    tables.push({
      caption: zh ? "分析结果" : "Analysis results",
      columns: [{ key: "metric", label: zh ? "指标" : "Measure" }, { key: "value", label: zh ? "结果" : "Result" }],
      rows: Object.entries(presentation.summary).map(([key, value]) => ({ metric: presentation.summaryLabels[key] ?? key, value })),
    });
  }
  tables.push(...presentation.tables.filter((table) => table.rows.length));
  if (!tables.length) {
    const rows: Record<string, unknown>[] = [];
    function append(value: unknown, path: string[]) {
      const entry = object(value);
      if (Object.keys(entry).length) {
        for (const [key, nested] of Object.entries(entry)) append(nested, [...path, zh ? labels[key] ?? key : key.replaceAll("_", " ")]);
      } else if (value != null && (typeof value !== "object" || Array.isArray(value))) rows.push({ metric: path.join(" / "), value });
    }
    for (const [caption, value] of [
      [zh ? "估计值" : "Estimates", run.result.estimates],
      [zh ? "统计量" : "Statistic", run.result.statistic],
      ["p", run.result.p_value],
      [zh ? "置信区间" : "Confidence interval", run.result.confidence_interval],
      [zh ? "效应量" : "Effect size", run.result.effect_size],
      [zh ? "描述统计" : "Descriptive statistics", run.result.descriptive_statistics],
    ] as const) append(value, [caption]);
    tables.push({ caption: zh ? "分析结果" : "Analysis results", columns: [{ key: "metric", label: zh ? "指标" : "Measure" }, { key: "value", label: zh ? "结果" : "Result" }], rows });
  }
  const sections = tables.map((table) => [
    ...(tables.length > 1 ? [[table.caption].map(cell).join(",")] : []),
    table.columns.map((column) => cell(column.label)).join(","),
    ...table.rows.map((row) => table.columns.map((column) => cell(row[column.key])).join(",")),
  ].join("\r\n"));
  return "\uFEFF" + sections.join("\r\n\r\n") + "\r\n";
}
