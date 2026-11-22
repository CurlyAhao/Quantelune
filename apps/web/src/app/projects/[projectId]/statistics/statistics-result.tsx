"use client";

import { useRef, useState } from "react";
import { Maximize2, Minimize2, Table2 } from "lucide-react";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { SavedFailureMessage } from "@/components/friendly-error";
import { workflowStatusLabel } from "@/lib/workflow-labels";

import type { StatisticalResult } from "@/lib/types";
import { StatisticsRegressionView } from "./statistics-regression";
import styles from "./statistics-ui.module.css";
import readerStyles from "./statistics-reader.module.css";
import {
  descriptiveTable,
  displayValue,
  genericStatisticsPresentation,
  researchPresentation,
  summaryKind,
  type ResultTable,
} from "@/lib/research-results";

export function ResultFields({ data }: { data: Record<string, unknown> }) {
  const { formatStat } = useLocaleDisplay();

  if (!Object.keys(data).length)
    return (
      <p className="text-muted-foreground">
        <UiMessage id="ui.statistics.notReturned" />
      </p>
    );
  return (
    <dl className="result-fields">
      {Object.entries(data).filter(([, value]) => value == null || typeof value === "number" || typeof value === "string").map(([key, value]) => (
        <div key={key}>
          <dt>{({ value: "统计量", name: "名称", df: "自由度", lower: "下限", upper: "上限", mean: "均值", std: "标准差", median: "中位数", count: "有效 n", correlation: "相关系数", odds_ratio: "比值比", p_value: "p" } as Record<string, string>)[key] ?? key.replaceAll("_", " ")}</dt>
          <dd>
            {typeof value === "number" ? (
              formatStat(value)
            ) : value == null ? (
              <UiMessage id="ui.displayBranch.common.notReturned" />
            ) : (
              String(value)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function DescriptiveTable({
  descriptive,
}: {
  descriptive: Record<string, unknown>;
}) {
  const { locale } = useLocaleDisplay();
  const table = descriptiveTable(descriptive, undefined, locale);
  return <ResearchResultTable table={table} />;
}

export function ResearchResultTable({ table }: { table: ResultTable }) {
  const { locale, t } = useLocaleDisplay();
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      aria-label={t("research.table.aria", { caption: table.caption })}
    >
      <table className={`research-table ${styles.table}`}>
        <caption>{table.caption}</caption>
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th scope="col" key={column.key} className={column.kind === "text" ? styles.textCell : undefined}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, index) => (
            <tr key={index}>
              {table.columns.map((column, i) =>
                i === 0 ? (
                  <th
                    scope="row"
                    key={column.key}
                    title={displayValue(row[column.key], column.kind, locale)}
                  >
                    {displayValue(row[column.key], column.kind, locale)}
                  </th>
                ) : (
                  <td key={column.key} className={column.kind === "text" ? styles.textCell : undefined}>
                    {displayValue(row[column.key], column.kind, locale)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {!table.rows.length && (
        <p className="workspace-note">
          <UiMessage id="ui.statistics.theOriginalRecordDidNotReturnThisTable" />
        </p>
      )}
    </div>
  );
}

function DescriptiveResultReader({
  table,
  notes,
  result,
  inputConfig,
  singleObservation,
}: {
  table: ResultTable;
  notes: string[];
  result: StatisticalResult;
  inputConfig: Record<string, unknown> | null;
  singleObservation: boolean;
}) {
  const { locale } = useLocaleDisplay();
  const chinese = locale === "zh-CN";
  const dialog = useRef<HTMLDialogElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const [wide, setWide] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const expectedColumns = ["n", "missing", "mean", "std", "min", "q1", "median", "q3", "max", "ci_lower", "ci_upper"];
  const canShowMetrics = table.rows.length === 1 && !table.columns.some(column => column.key === "group") &&
    expectedColumns.every(key => table.columns.some(column => column.key === key));
  const row = table.rows[0];
  const confidence = typeof inputConfig?.confidence_level === "number" ? `${inputConfig.confidence_level * 100}% ` : "";
  const value = (key: string) => {
    const column = table.columns.find(item => item.key === key);
    const raw = row?.[key];
    const numeric = typeof raw === "number" && Number.isFinite(raw);
    const text = numeric && Math.abs(raw) >= 1000 && column?.kind !== "count"
      ? raw.toLocaleString(locale, {minimumFractionDigits: 4, maximumFractionDigits: 4})
      : displayValue(raw, column?.kind, locale);
    return <span title={numeric ? String(raw) : undefined}>{text}</span>;
  };
  const metric = (label: string, key: string) => <tr key={key}><th scope="row">{label}</th><td>{value(key)}</td></tr>;
  const title = canShowMetrics ? displayValue(row.label, "text", locale) : table.caption;
  const content = (expanded: boolean) => (
    <div className={readerStyles.sheet}>
      <header className={readerStyles.header}>
        <h3><Table2 size={19} aria-hidden="true" />{title}</h3>
        <div className={readerStyles.actions}>
          {canShowMetrics && <div className={readerStyles.views} role="group" aria-label={chinese ? "统计表阅读方式" : "Table view"}>
            <button type="button" aria-pressed={!wide} onClick={() => setWide(false)}>{chinese ? "按指标" : "Metrics"}</button>
            <button type="button" aria-pressed={wide} onClick={() => setWide(true)}>{chinese ? "原始宽表" : "Wide table"}</button>
          </div>}
          {expanded ? <button type="button" className={readerStyles.expand} aria-label={chinese ? "还原表格" : "Restore table"} title={chinese ? "还原表格" : "Restore table"} onClick={() => dialog.current?.close()}><Minimize2 size={16} aria-hidden="true" /></button> :
            <button ref={expandButton} type="button" className={readerStyles.expand} aria-label={chinese ? "展开表格" : "Expand table"} title={chinese ? "展开表格" : "Expand table"} onClick={() => { setExpanded(true); dialog.current?.showModal(); }}><Maximize2 size={16} aria-hidden="true" /></button>}
        </div>
      </header>
      <div className={readerStyles.body}>
        {canShowMetrics && !wide ? <div className={readerStyles.grid}>
          <table className={readerStyles.metrics}><caption>{chinese ? "样本与均值" : "Sample and mean"}</caption><tbody>
            {metric(chinese ? "有效样本" : "Valid observations", "n")}
            {metric(chinese ? "缺失样本" : "Missing observations", "missing")}
            {metric(chinese ? "均值" : "Mean", "mean")}
            {metric(chinese ? "标准差 SD" : "Standard deviation (SD)", "std")}
            <tr><th scope="row">{chinese ? `均值的 ${confidence}置信区间` : `${confidence}confidence interval for mean`}</th><td className={readerStyles.interval}>{value("ci_lower")} <span>{chinese ? "至" : "to"}</span> {value("ci_upper")}</td></tr>
          </tbody></table>
          <table className={readerStyles.metrics}><caption>{chinese ? "分位数与范围" : "Quantiles and range"}</caption><tbody>
            {metric(chinese ? "最小值" : "Minimum", "min")}
            {metric(chinese ? "下四分位数 Q1" : "First quartile (Q1)", "q1")}
            {metric(chinese ? "中位数" : "Median", "median")}
            {metric(chinese ? "上四分位数 Q3" : "Third quartile (Q3)", "q3")}
            {metric(chinese ? "最大值" : "Maximum", "max")}
          </tbody></table>
        </div> : <ResearchResultTable table={table} />}
      </div>
      {singleObservation && <p className={readerStyles.warning} role={result.method_version === "1.0.0" ? "alert" : undefined}>
        {chinese ? result.method_version === "1.0.0" ? "旧版单条观测的 SD/方差有误，请重新计算。" : "有效观测不足两条时，样本标准差、方差和均值置信区间用 — 表示。" : result.method_version === "1.0.0" ? "The old single-observation SD/variance is incorrect. Recalculate this analysis." : "With fewer than two observations, the sample SD, variance and mean confidence interval are shown as —."}
      </p>}
      {notes.length > 0 && <details className={readerStyles.notes}><summary>{chinese ? "计算说明" : "Calculation notes"}</summary>
        {result.sample && <p>{chinese ? `原始样本 ${result.sample.original_n} 条，排除 ${result.sample.excluded_n} 条，有效样本 ${result.sample.final_n} 条。` : `Original observations: ${result.sample.original_n}; excluded: ${result.sample.excluded_n}; valid: ${result.sample.final_n}.`}</p>}
        {!canShowMetrics && confidence && <p>{chinese ? "置信水平" : "Confidence level"} · {confidence.trim()}</p>}
        {notes.map(note => <p key={note}>{note}</p>)}
      </details>}
      {result.error_code && <p className={readerStyles.warning} role="alert"><SavedFailureMessage code={result.error_code} /></p>}
    </div>
  );
  return <div className={readerStyles.reader}>
    {content(false)}
    <dialog className={readerStyles.dialog} ref={dialog} onClose={() => { setExpanded(false); expandButton.current?.focus(); }} aria-label={chinese ? "统计结果" : "Statistical results"}>
      {expanded && content(true)}
    </dialog>
  </div>;
}

export function StatisticsResultView({
  result,
  inputConfig = null,
  missingPolicy,
  workspaceReader = false,
}: {
  result: StatisticalResult;
  inputConfig?: Record<string, unknown> | null;
  missingPolicy?: string | null;
  workspaceReader?: boolean;
}) {
  const { locale, t } = useLocaleDisplay();

  const presentation = researchPresentation(result, inputConfig, locale);
  const generic = genericStatisticsPresentation(result, locale);
  const singleObservation =
    result.method === "descriptive" &&
    Object.values(
      (result.descriptive_statistics?.variables ?? {}) as Record<
        string,
        { count?: number }
      >,
    ).some((summary) => summary.count === 1);
  if (workspaceReader && result.method === "descriptive" && presentation.tables.length === 1) {
    return <DescriptiveResultReader table={presentation.tables[0]} notes={presentation.notes} result={result} inputConfig={inputConfig} singleObservation={singleObservation} />;
  }
  return (
    <div className="research-results">
      <div className="result-summary">
        <span>
          <UiMessage id="ui.statistics.status" />
          {workflowStatusLabel(result.status, t)}
        </span>
        <span>
          {" "}
          <UiMessage id="ui.statistics.method" />
          {presentation.name}
        </span>
        {result.sample && (
          <span>
            {" "}
            <UiMessage id="ui.statistics.originalN" />
            {
              result.sample.original_n
            } <UiMessage id="ui.statistics.excluded" />{" "}
            {result.sample.excluded_n}{" "}
            <UiMessage id="ui.statistics.effectiveN" />
            {result.sample.final_n}
          </span>
        )}
      </div>
      {singleObservation && (
        <p
          className="workspace-note"
          role={result.method_version === "1.0.0" ? "alert" : undefined}
        >
          {locale === "zh-CN"
            ? result.method_version === "1.0.0"
              ? "旧版单条观测的 SD/方差有误，请重新计算。"
              : "有效观测不足两条时，样本标准差、方差和均值置信区间用 — 表示。"
            : result.method_version === "1.0.0"
              ? "The old single-observation SD/variance is incorrect. Recalculate this analysis."
              : "With fewer than two observations, the sample SD, variance and mean confidence interval are shown as —."}
        </p>
      )}
      {result.p_value == null && result.method !== "descriptive" && (
        <p className="workspace-note">
          <UiMessage id="ui.statistics.theOriginalRecordReturnedNoPValueNoSignificanceJudgmentIsMade" />
        </p>
      )}
      {inputConfig && (
        <div className={styles.context}>
          {Array.isArray(inputConfig.variables) && inputConfig.variables.length > 0 && (
            <span>{locale === "zh-CN" ? "变量：" : "Variables: "}{inputConfig.variables.map(String).join("、")}</span>
          )}
          {typeof inputConfig.group_variable === "string" && inputConfig.group_variable && (
            <span>{locale === "zh-CN" ? "分组：" : "Group: "}{inputConfig.group_variable}</span>
          )}
          {typeof inputConfig.target === "string" && inputConfig.target && (
            <span>{locale === "zh-CN" ? "因变量：" : "Outcome: "}{inputConfig.target}</span>
          )}
          {Array.isArray(inputConfig.categorical_variables) && inputConfig.categorical_variables.length > 0 && (
            <span>{locale === "zh-CN" ? "类别变量：" : "Categorical variables: "}{inputConfig.categorical_variables.map(String).join("、")}</span>
          )}
          {result.method === "descriptive" && result.sample?.reason?.includes("per variable") ? <span>{locale === "zh-CN" ? "缺失值：按变量分别排除" : "Missing values: excluded per variable"}</span> : missingPolicy && ({listwise_delete: locale === "zh-CN" ? "删除缺失行" : "Exclude incomplete rows", pairwise_delete: locale === "zh-CN" ? "按可用配对数据计算" : "Use available pairs", error_on_missing: locale === "zh-CN" ? "发现缺失时报错" : "Stop on missing values"} as Record<string,string>)[missingPolicy] && (
            <span>{locale === "zh-CN" ? "缺失值：" : "Missing values: "}{({listwise_delete: locale === "zh-CN" ? "删除缺失行" : "Exclude incomplete rows", pairwise_delete: locale === "zh-CN" ? "按可用配对数据计算" : "Use available pairs", error_on_missing: locale === "zh-CN" ? "发现缺失时报错" : "Stop on missing values"} as Record<string,string>)[missingPolicy]}</span>
          )}
          {typeof inputConfig.confidence_level === "number" && (
            <span>{locale === "zh-CN" ? "置信水平：" : "Confidence level: "}{inputConfig.confidence_level * 100}%</span>
          )}
        </div>
      )}
      {presentation.supported ? (
        <>
          {Object.keys(presentation.summary).length > 0 && (
            <section>
              <h3>
                <UiMessage id="ui.statistics.testsAndEstimates" />
              </h3>
              <dl className="result-fields">
                {Object.entries(presentation.summary).map(([key, value]) => (
                  <div key={key}>
                    <dt>{presentation.summaryLabels[key] ?? key}</dt>
                    <dd>{displayValue(value, summaryKind(key), locale)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
          {presentation.tables.map((table, i) => (
            <section key={i}>
              <ResearchResultTable table={table} />
            </section>
          ))}
          {presentation.notes.length > 0 && (
            <details className="workspace-note">
              <summary>
                {locale === "zh-CN" ? "计算说明" : "Calculation notes"}
              </summary>
              {presentation.notes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </details>
          )}
        </>
      ) : result.method?.endsWith("_regression") ? (
        <StatisticsRegressionView result={result} />
      ) : (
        <>
          {Object.keys(generic.summary).length > 0 && (
            <section>
              <h3><UiMessage id="ui.statistics.testsAndEstimates" /></h3>
              <dl className="result-fields">
                {Object.entries(generic.summary).map(([label, value]) => (
                  <div key={label}><dt>{label}</dt><dd>{displayValue(value, summaryKind(label), locale)}</dd></div>
                ))}
              </dl>
            </section>
          )}
          {generic.tables.map((table, i) => <section key={i}><ResearchResultTable table={table} /></section>)}
        </>
      )}
      {result.error_code && (
        <p role="alert"><SavedFailureMessage code={result.error_code} /></p>
      )}
    </div>
  );
}
