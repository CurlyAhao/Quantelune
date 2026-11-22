import type { AppLocale } from "./i18n";
import {
  createAppTranslator,
  type NoParameterMessageKey,
} from "./i18n-messages";
import { formatLocaleNumber } from "./i18n-core";
import { formatPValue, formatStat } from "./format";
import type { StatisticalResult } from "./types";

export type ResultColumn = {
  key: string;
  label: string;
  kind?: "count" | "p" | "text";
};
export type ResultTable = {
  caption: string;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
};
export const summaryKind = (label: string): ResultColumn["kind"] | undefined =>
  label === "p" || label.endsWith(" p")
    ? "p"
    : label === "n" || label.endsWith(" n")
      ? "count"
      : undefined;
export const methodNames: Record<string, string> = {
  descriptive: "描述统计",
  welch_t: "Welch 两组均值比较",
  paired_t: "配对 t 检验",
  one_way_anova: "单因素 ANOVA",
  chi_square_independence: "卡方独立性检验",
  pearson: "Pearson 相关",
  spearman: "Spearman 秩相关",
  linear_regression: "OLS 线性回归",
};
const methodKeys: Record<string, NoParameterMessageKey> = {
  descriptive: "research.method.descriptive",
  welch_t: "research.method.welch_t",
  paired_t: "research.method.paired_t",
  one_way_anova: "research.method.one_way_anova",
  chi_square_independence: "research.method.chi_square_independence",
  pearson: "research.method.pearson",
  spearman: "research.method.spearman",
  linear_regression: "research.method.linear_regression",
};
export function localizedMethodName(id: string, locale: AppLocale): string {
  const key = methodKeys[id];
  return key ? createAppTranslator(locale)(key) : id;
}
export const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export function displayValue(
  value: unknown,
  kind?: ResultColumn["kind"],
  locale: AppLocale = "zh-CN",
): string {
  if (value == null || (typeof value === "number" && !Number.isFinite(value)))
    return "—";
  if (kind === "p")
    return formatPValue(typeof value === "number" ? value : null, locale);
  if (typeof value === "number")
    return kind === "count"
      ? formatLocaleNumber(value, locale)
      : formatStat(value, 4, locale);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function descriptiveTable(
  descriptive: Record<string, unknown>,
  missing?: Record<string, number>,
  locale: AppLocale = "zh-CN",
  selectedVariables: string[] = [],
): ResultTable {
  const t = createAppTranslator(locale);
  const source = object(descriptive.variables ?? descriptive);
  const variables = "variables" in descriptive;
  const grouping = variables && typeof descriptive.group_variable === "string"
    ? descriptive.group_variable : null;
  const columns: ResultColumn[] = [
    {
      key: "label",
      label: variables
        ? t("research.label.variable")
        : t("research.label.group"),
      kind: "text",
    },
    ...(grouping ? [{ key: "group", label: grouping, kind: "text" as const }] : []),
    { key: "n", label: t("research.label.validN"), kind: "count" },
    ...(variables
      ? [
          {
            key: "missing",
            label: t("research.label.missingN"),
            kind: "count" as const,
          },
        ]
      : []),
    { key: "mean", label: t("research.label.mean") },
    { key: "std", label: "SD" },
    ...(variables
      ? [
          { key: "min", label: t("research.label.min") },
          { key: "q1", label: "Q1" },
          { key: "median", label: t("research.label.median") },
          { key: "q3", label: "Q3" },
          { key: "max", label: t("research.label.max") },
          { key: "ci_lower", label: t("research.label.meanLower") },
          { key: "ci_upper", label: t("research.label.meanUpper") },
        ]
      : []),
  ];
  return {
    caption: t("research.table.descriptive"),
    columns,
    rows: Object.entries(source)
      .filter(([, value]) => Object.keys(object(value)).length)
      .map(([label, value]) => {
        const row = object(value),
          ci = object(row.mean_ci);
        const prefix = grouping ? `${grouping}=` : "";
        const matchingVariable = selectedVariables.find((name) => label.endsWith(` · ${name}`));
        const separator = grouping && label.startsWith(prefix)
          ? matchingVariable ? label.length - matchingVariable.length - 3 : label.lastIndexOf(" · ")
          : -1;
        return {
          ...row,
          label: separator >= 0 ? label.slice(separator + 3) : label,
          group: separator >= 0 ? label.slice(prefix.length, separator) : null,
          n: row.count ?? row.n,
          missing: missing?.[label],
          ci_lower: ci.lower,
          ci_upper: ci.upper,
        };
      }),
  };
}

export function researchPresentation(
  result: StatisticalResult,
  config: Record<string, unknown> | null = null,
  locale: AppLocale = "zh-CN",
) {
  const t = createAppTranslator(locale);
  const ds = result.descriptive_statistics ?? {},
    est = result.estimates ?? {},
    stat = result.statistic ?? {};
  const ci = result.confidence_interval ?? {},
    tables: ResultTable[] = [],
    notes: string[] = [];
  const variables = Array.isArray(config?.variables)
    ? config.variables.map(String)
    : [];
  const summary: Record<string, unknown> = {};
  const summaryLabels: Record<string, string> = {};
  const add = (
    label: string,
    value: unknown,
    displayKey?: NoParameterMessageKey,
  ) => {
    summary[label] = value;
    summaryLabels[label] = displayKey ? t(displayKey) : label;
  };
  if (result.method === "descriptive") {
    tables.push(descriptiveTable(ds, result.sample?.per_variable, locale, variables));
    notes.push(t("research.note.perVariable"), t("research.note.meanInterval"));
  } else if (["welch_t", "paired_t"].includes(result.method)) {
    const order = Array.isArray(ci.group_order)
      ? ci.group_order.map(String)
      : variables;
    add("均值差", est.mean_difference, "research.label.difference");
    add("t", stat.value);
    add("df", stat.df);
    add("p", result.p_value);
    notes.push(
      t("research.note.direction", {
        direction:
          order.length === 2
            ? order.join(" − ")
            : t("research.note.noGroupNames"),
      }),
    );
    if (result.method === "welch_t") {
      tables.push(descriptiveTable(ds, undefined, locale));
      if (ci.estimand === "mean_difference" && ci.schema_version === 1) {
        add(
          "均值差 CI 下限",
          ci.lower_unbounded ? "−∞" : ci.lower,
          "research.label.differenceLower",
        );
        add(
          "均值差 CI 上限",
          ci.upper_unbounded ? "+∞" : ci.upper,
          "research.label.differenceUpper",
        );
        notes.push(
          t("research.note.welchInterval", {
            level: Number(ci.confidence_level) * 100,
            alternative: ci.alternative === "less" ? (locale === "zh-CN" ? "小于" : "less") : ci.alternative === "greater" ? (locale === "zh-CN" ? "大于" : "greater") : (locale === "zh-CN" ? "双侧" : "two-sided"),
          }),
        );
      } else notes.push(t("research.note.noInterval"));
    } else notes.push(t("research.note.paired"));
    Object.entries(result.effect_size ?? {}).forEach(([key, value]) =>
      add(key, value),
    );
  } else if (result.method === "one_way_anova") {
    tables.push(descriptiveTable(ds, undefined, locale));
    add("F", stat.value);
    add("df 组间", stat.df_between, "research.label.dfBetween");
    add("df 组内", stat.df_within, "research.label.dfWithin");
    add("p", result.p_value);
    add("η²", result.effect_size?.eta_squared);
    add("ω²", result.effect_size?.omega_squared);
    notes.push(t("research.note.anova"));
  } else if (result.method === "chi_square_independence") {
    add("χ²", stat.value);
    add("df", stat.df);
    add("p", result.p_value);
    add("Cramér V", result.effect_size?.cramers_v ?? result.effect_size?.value);
    const rows = Array.isArray(est.row_labels)
      ? est.row_labels
      : Array.isArray(est.rows)
        ? est.rows
        : [];
    const columns = Array.isArray(est.col_labels)
      ? est.col_labels
      : Array.isArray(est.columns)
        ? est.columns
        : [];
    for (const key of ["observed", "expected"] as const) {
      const matrix = Array.isArray(est[key]) ? (est[key] as unknown[][]) : [];
      tables.push({
        caption:
          key === "observed"
            ? t("research.table.observed")
            : t("research.table.expected"),
        columns: [
          {
            key: "label",
            label: variables[0] ?? t("research.label.rowCategory"),
            kind: "text",
          },
          ...columns.map((c, i) => ({
            key: String(i),
            label: String(c),
            kind: key === "observed" ? ("count" as const) : undefined,
          })),
        ],
        rows: matrix.map((values, i) => ({
          label: rows[i] ?? t("research.label.row", { index: i + 1 }),
          ...Object.fromEntries(values.map((value, j) => [String(j), value])),
        })),
      });
    }
    notes.push(t("research.note.chiSquare"));
  } else if (["pearson", "spearman"].includes(result.method)) {
    add(
      result.method === "pearson" ? "Pearson r" : "Spearman ρ",
      est.correlation,
    );
    add("完整配对 n", stat.n, "research.label.completePairsN");
    add("p", result.p_value);
    const interval = object(ci.correlation);
    if (Object.keys(interval).length) {
      add("相关 CI 下限", interval.lower, "research.label.correlationLower");
      add("相关 CI 上限", interval.upper, "research.label.correlationUpper");
      notes.push(
        t("research.note.fisher", {
          level: Number(interval.confidence_level) * 100,
        }),
      );
    } else notes.push(t("research.note.noInterval"));
    notes.push(
      result.method === "pearson"
        ? t("research.note.pearson")
        : t("research.note.spearman"),
      t("research.note.legacyPairwise"),
    );
  } else if (result.method === "linear_regression") {
    for (const [key, label, displayKey] of [
      ["n", "有效 n", "research.label.validN"],
      ["r_squared", "R²"],
      ["adj_r_squared", "调整 R²", "research.label.adjustedR2"],
      ["f_statistic", "F"],
      ["f_pvalue", "模型 p", "research.label.modelP"],
      ["aic", "AIC"],
      ["bic", "BIC"],
    ])
      add(label, ds[key], displayKey as NoParameterMessageKey | undefined);
    const coefs = Array.isArray(est.coefficients)
      ? est.coefficients.map(object)
      : [];
    tables.push({
      caption: t("research.table.coefficients", {
        level: result.confidence_level * 100,
      }),
      columns: [
        { key: "term", label: t("research.label.term"), kind: "text" },
        { key: "coefficient", label: t("research.figure.coefficient") },
        { key: "std_error", label: "SE" },
        { key: "t", label: "t" },
        { key: "p_value", label: "p", kind: "p" },
        { key: "ci_lower", label: t("research.label.ciLower") },
        { key: "ci_upper", label: t("research.label.ciUpper") },
      ],
      rows: coefs,
    });
    tables.push({
      caption: t("research.table.encoding"),
      columns: [
        { key: "label", label: t("research.label.variable"), kind: "text" },
        {
          key: "reference",
          label: t("research.label.reference"),
          kind: "text",
        },
        { key: "levels", label: t("research.label.levels"), kind: "text" },
      ],
      rows: Object.entries(object(est.encoding)).map(([label, value]) => ({
        label,
        reference: object(value).reference,
        levels: Array.isArray(object(value).encoded_levels)
          ? (object(value).encoded_levels as unknown[]).join("、")
          : null,
      })),
    });
    notes.push(
      t("research.note.coefficients"),
      t("research.note.standardize", {
        request:
          config?.standardize == null
            ? t("research.note.unknown")
            : config.standardize ? (locale === "zh-CN" ? "是" : "yes") : (locale === "zh-CN" ? "否" : "no"),
      }),
      t("research.note.rowDiagnostics"),
    );
  }
  return {
    name: localizedMethodName(result.method, locale),
    supported: result.method in methodNames,
    tables,
    summary,
    summaryLabels,
    notes,
  };
}

/** Project the remaining manual methods from their saved result shapes. */
export function genericStatisticsPresentation(
  result: StatisticalResult,
  locale: AppLocale = "zh-CN",
): { summary: Record<string, unknown>; tables: ResultTable[] } {
  const zh = locale === "zh-CN";
  const est = result.estimates ?? {};
  const stat = result.statistic ?? {};
  const ci = result.confidence_interval ?? {};
  const effects = result.effect_size ?? {};
  const summary: Record<string, unknown> = {};
  const tables: ResultTable[] = [];
  const labels: Record<string, [string, string]> = {
    mean: ["均值", "Mean"], mu0: ["参考值", "Reference value"],
    mean_minus_mu0: ["均值与参考值之差", "Mean difference from reference"],
    mean_difference: ["均值差", "Mean difference"], median_difference: ["中位数差", "Median difference"],
    cohens_d: ["Cohen's d", "Cohen's d"], cohens_d_paired: ["配对 Cohen's d", "Paired Cohen's d"],
    hedges_g: ["Hedges' g", "Hedges' g"], rank_biserial: ["秩二列相关", "Rank-biserial correlation"],
    epsilon_squared: ["ε²", "ε²"], w: ["Cohen's w", "Cohen's w"],
    odds_ratio: ["比值比", "Odds ratio"], df: ["自由度", "df"],
    df_between: ["组间自由度", "Between-group df"], df_within: ["组内自由度", "Within-group df"],
    mae: ["平均绝对误差", "MAE"], rmse: ["均方根误差", "RMSE"], r2: ["R²", "R²"],
    tp: ["真正例", "True positives"], fp: ["假正例", "False positives"],
    tn: ["真负例", "True negatives"], fn: ["假负例", "False negatives"],
    accuracy: ["准确率", "Accuracy"], precision: ["精确率", "Precision"],
    recall: ["召回率", "Recall"], specificity: ["特异度", "Specificity"], f1: ["F1", "F1"],
  };
  const addKnown = (source: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(source)) {
      if (labels[key] && (typeof value === "number" || value == null))
        summary[labels[key][zh ? 0 : 1]] = value;
    }
  };
  addKnown(est);
  if (typeof stat.value === "number") {
    const names: Record<string, string> = { chi2: "χ²", F_welch: "F", kendall_r: "Kendall τ", odds_ratio: zh ? "比值比" : "Odds ratio" };
    const name = typeof stat.name === "string" ? stat.name : "";
    summary[names[name] ?? (name || (zh ? "统计量" : "Statistic"))] = stat.value;
  }
  addKnown(stat);
  if (result.p_value != null) summary.p = result.p_value;
  addKnown(effects);
  if (ci.estimand === "mean_difference" && ci.schema_version === 1) {
    if (typeof ci.confidence_level === "number")
      summary[zh ? "置信水平" : "Confidence level"] = `${ci.confidence_level * 100}%`;
    summary[zh ? "均值差置信区间下限" : "Mean difference CI lower"] = ci.lower_unbounded ? "−∞" : ci.lower;
    summary[zh ? "均值差置信区间上限" : "Mean difference CI upper"] = ci.upper_unbounded ? "+∞" : ci.upper;
    if (Array.isArray(ci.group_order) && ci.group_order.length === 2)
      summary[zh ? "差值方向" : "Difference direction"] = ci.group_order.map(String).join(" − ");
  }
  if (result.method === "chi_square_gof" && Array.isArray(est.categories)) {
    const observed = Array.isArray(est.observed) ? est.observed : [];
    const expected = Array.isArray(est.expected) ? est.expected : [];
    tables.push({ caption: zh ? "类别频数" : "Category frequencies", columns: [
      { key: "category", label: zh ? "类别" : "Category", kind: "text" },
      { key: "observed", label: zh ? "观察频数" : "Observed", kind: "count" },
      { key: "expected", label: zh ? "期望频数" : "Expected" },
    ], rows: est.categories.map((category, i) => ({ category, observed: observed[i], expected: expected[i] })) });
  }
  if (result.method === "fisher_exact" && Array.isArray(est.rows) && Array.isArray(est.columns) && Array.isArray(est.observed)) {
    const columns = est.columns;
    tables.push({ caption: zh ? "观察频数" : "Observed frequencies", columns: [
      { key: "label", label: zh ? "类别" : "Category", kind: "text" },
      ...columns.map((column, i) => ({ key: String(i), label: String(column), kind: "count" as const })),
    ], rows: est.rows.map((label, i) => {
      const values = Array.isArray((est.observed as unknown[])[i]) ? (est.observed as unknown[][])[i] : [];
      return { label, ...Object.fromEntries(values.map((value, j) => [String(j), value])) };
    }) });
  }
  if (["welch_anova", "kruskal_wallis", "mann_whitney"].includes(result.method)) {
    const grouped = object(result.descriptive_statistics);
    const isMedian = result.method !== "welch_anova";
    const rows = Object.entries(grouped).map(([label, value]) => ({
      label, n: object(value).n,
      estimate: isMedian && result.method === "mann_whitney" ? est[`median_${label}`] : est[label],
      std: object(value).std,
    }));
    if (rows.length) tables.push({ caption: zh ? "分组统计" : "Group statistics", columns: [
      { key: "label", label: zh ? "组别" : "Group", kind: "text" },
      { key: "n", label: zh ? "有效 n" : "Valid n", kind: "count" },
      { key: "estimate", label: isMedian ? (zh ? "中位数" : "Median") : (zh ? "均值" : "Mean") },
      ...(!isMedian ? [{ key: "std", label: "SD" }] : []),
    ], rows });
  }
  if (result.method === "student_t" && Object.keys(object(result.descriptive_statistics)).length)
    tables.push(descriptiveTable(result.descriptive_statistics, undefined, locale));
  return { summary, tables };
}
