import type { AppLocale } from "./i18n";
import type { StatisticalRun } from "./types";
import type {
  FigureData,
  FigureSpec,
  ReportBlock,
  ReportDocument,
  WorkspaceRecord,
} from "./workspace-api";
import {
  displayValue,
  object,
  researchPresentation,
  type ResultTable,
} from "./research-results";
import { escapeMarkup as escape } from "./research-figure";
import { figureSvg } from "./workspace-figure";

export function reportTables(
  block: ReportBlock,
  locale: AppLocale,
): ResultTable[] {
  const payload = block.source_snapshot ?? {};
  if (block.source?.kind === "statistics") {
    const run = payload as unknown as StatisticalRun;
    if (!run.result) return [];
    const view = researchPresentation(run.result, run.input_config, locale);
    const tables = [...view.tables];
    if (Object.keys(view.summary).length)
      tables.unshift({
        caption: locale === "zh-CN" ? "检验与估计" : "Tests and estimates",
        columns: [
          {
            key: "name",
            label: locale === "zh-CN" ? "指标" : "Metric",
            kind: "text",
          },
          { key: "value", label: locale === "zh-CN" ? "结果" : "Value" },
        ],
        rows: Object.entries(view.summary).map(([key, value]) => ({
          name: view.summaryLabels[key] ?? key,
          value,
        })),
      });
    if (!view.supported)
      tables.push({
        caption: locale === "zh-CN" ? "保存结果" : "Saved results",
        columns: [
          { key: "name", label: "Metric", kind: "text" },
          { key: "value", label: "Value" },
        ],
        rows: Object.entries({
          ...run.result.statistic,
          ...run.result.estimates,
        }).map(([name, value]) => ({ name, value })),
      });
    return tables;
  }
  if (block.source?.kind === "ml") {
    const models = Array.isArray(payload.models) ? payload.models : [];
    return [
      {
        caption: locale === "zh-CN" ? "模型结果" : "Model results",
        columns: [
          {
            key: "model",
            label: locale === "zh-CN" ? "模型" : "Model",
            kind: "text",
          },
          {
            key: "metric",
            label: locale === "zh-CN" ? "指标" : "Metric",
            kind: "text",
          },
          { key: "value", label: locale === "zh-CN" ? "结果" : "Value" },
        ],
        rows: models.flatMap((model) => {
          const row = object(model);
          if (row.status !== "completed") return [];
          return Object.entries(object(row.test_metrics)).map(
            ([metric, value]) => ({
              model: row.model_id ?? row.model ?? row.name,
              metric,
              value,
            }),
          );
        }),
      },
    ];
  }
  if (block.source?.kind === "quality" || block.source?.kind === "workflow")
    return [
      {
        caption: locale === "zh-CN" ? "运行摘要" : "Run summary",
        columns: [
          {
            key: "name",
            label: locale === "zh-CN" ? "指标" : "Metric",
            kind: "text",
          },
          { key: "value", label: locale === "zh-CN" ? "结果" : "Value" },
        ],
        rows: Object.entries(payload)
          .filter(([key]) => !["id", "input_config"].includes(key))
          .map(([name, value]) => ({ name, value })),
      },
    ];
  return [];
}

export function reportFigure(block: ReportBlock, locale: AppLocale): string {
  if (block.source?.kind === "plot_run") {
    const png = block.source_snapshot?.figure_png;
    return typeof png === "string" && png.length <= 27_000_000 && /^[A-Za-z0-9+/]+={0,2}$/.test(png)
      ? `<img class="native-plot" src="data:image/png;base64,${png}" alt="${escape(block.title)}" style="max-width:100%;height:auto">` : "";
  }
  if (block.source?.kind !== "figure" || !block.source_snapshot) return "";
  const record = block.source_snapshot.record as WorkspaceRecord<FigureSpec>;
  const run = block.source_snapshot.run as StatisticalRun;
  const snapshot = block.source_snapshot
    .snapshot as WorkspaceRecord<FigureData> | null;
  return figureSvg(record.payload, run, snapshot, locale);
}

export function reportQuote(block: ReportBlock): string {
  if (block.source?.kind !== "citation") return "";
  const locator = object(block.source_snapshot?.locator);
  return typeof locator.quote === "string"
    ? locator.quote
    : typeof locator.note === "string"
      ? locator.note
      : "";
}

export function reportHtml(
  report: ReportDocument,
  locale: AppLocale,
  projectTitle: string,
): string {
  const sections = report.blocks
    .map((block) => {
      const tables = reportTables(block, locale)
        .map(
          (table) =>
            `<div class="table"><table><caption>${escape(table.caption)}</caption><thead><tr>${table.columns.map((column) => `<th>${escape(column.label)}</th>`).join("")}</tr></thead><tbody>${table.rows.map((row) => `<tr>${table.columns.map((column) => `<td>${escape(displayValue(row[column.key], column.kind, locale))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`,
        )
        .join("");
      const quote = reportQuote(block);
      const source = block.source
        ? `<details><summary>${locale === "zh-CN" ? "来源" : "Source"}</summary><p>${escape(block.source.kind)} · ${escape(block.source.id)} · r${block.source.revision}</p><p>SHA256 ${escape(block.source.sha256)}</p><pre>${escape(JSON.stringify(block.source_snapshot, null, 2))}</pre></details>`
        : "";
      return `<section><h2>${escape(block.title)}</h2>${quote ? `<blockquote>${escape(quote)}</blockquote>` : ""}<p class="prose">${escape(block.body)}</p>${reportFigure(block, locale)}${tables}${block.caption ? `<p class="caption">${escape(block.caption)}</p>` : ""}${source}</section>`;
    })
    .join("");
  return `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>${escape(report.title)}</title><style>body{font:16px/1.7 Arial,'Microsoft YaHei',sans-serif;background:#fff;color:#263b32;max-width:980px;margin:48px auto;padding:0 28px}h1{font-size:30px}h2{font-size:22px}section{margin:36px 0}p.prose,blockquote{white-space:pre-wrap}p,blockquote{overflow-wrap:anywhere}.project,.caption,details{color:#58675e;font-size:14px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{border-bottom:1px solid #d7ded8;padding:9px;text-align:right}th:first-child,td:first-child{text-align:left}caption{text-align:left;font-weight:600;padding:8px 0}.table{overflow-x:auto}svg{width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin-top:16px}blockquote{margin:20px 0;padding:12px 20px;background:#f3f5f0}@media print{body{max-width:none;margin:0}details{display:none}thead{display:table-header-group}}</style></head><body><p class="project">${escape(projectTitle)}</p><h1>${escape(report.title)}</h1><p class="prose">${escape(report.subtitle)}</p>${sections}</body></html>`;
}

export function reportMarkdown(
  report: ReportDocument,
  locale: AppLocale,
): string {
  const safe = (value: string) =>
    value.replaceAll("|", "\\|").replaceAll("\n", " ");
  return (
    `# ${report.title}\n\n${report.subtitle}\n\n` +
    report.blocks
      .map((block) => {
        const tables = reportTables(block, locale)
          .map(
            (table) =>
              `\n${table.caption}\n\n| ${table.columns.map((column) => safe(column.label)).join(" | ")} |\n| ${table.columns.map(() => "---").join(" | ")} |\n${table.rows.map((row) => `| ${table.columns.map((column) => safe(displayValue(row[column.key], column.kind, locale))).join(" | ")} |`).join("\n")}\n`,
          )
          .join("\n");
        const quote = reportQuote(block);
        return `## ${block.title}\n\n${
          quote
            ? quote
                .split("\n")
                .map((line) => "> " + line)
                .join("\n") + "\n\n"
            : ""
        }${block.body}\n\n${reportFigure(block, locale)}\n${tables}\n${block.caption}\n${block.source ? `\n来源：${block.source.kind} ${block.source.id} r${block.source.revision} · SHA256 ${block.source.sha256}\n` : ""}`;
      })
      .join("\n")
  );
}
