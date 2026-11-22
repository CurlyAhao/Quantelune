"use client";

import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import dynamic from "next/dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { BASE_TEXT_STYLE, useIsClient } from "@/components/charts/effect";
import type { StatisticalResult } from "@/lib/types";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => (
    <div className="flex h-56 w-full items-center justify-center text-xs text-muted-foreground">
      {" "}
      <UiMessage id="ui.statistics.loadingChart" />{" "}
    </div>
  ),
});

interface Coef {
  term: string;
  coefficient: number;
  std_error?: number;
  p_value?: number;
  ci_lower?: number;
  ci_upper?: number;
  odds_ratio?: number;
  or_ci_lower?: number;
  or_ci_upper?: number;
}

interface ErrorBarApi {
  value: (idx: number) => number;
  coord: (v: [number, number]) => [number, number];
}

function ForestPlot({
  coefs,
  useOdds,
  confidenceLevel,
}: {
  coefs: Coef[];
  useOdds: boolean;
  confidenceLevel?: number;
}) {
  const { t, formatNumber } = useLocaleDisplay();
  const isClient = useIsClient();
  if (!isClient) return null;
  const rows = coefs.filter(
    (c) =>
      c.term !== "const" &&
      c.term !== "Intercept" &&
      Number.isFinite(useOdds ? c.odds_ratio : c.coefficient),
  );
  const terms = rows.map((c) => c.term);
  const value = (c: Coef) => (useOdds ? c.odds_ratio! : c.coefficient);
  const lo = (c: Coef) =>
    useOdds ? (c.or_ci_lower ?? value(c)) : (c.ci_lower ?? value(c));
  const hi = (c: Coef) =>
    useOdds ? (c.or_ci_upper ?? value(c)) : (c.ci_upper ?? value(c));
  const reference = useOdds ? 1 : 0;

  const option = {
    textStyle: BASE_TEXT_STYLE,
    grid: { left: 130, right: 28, top: 12, bottom: 30 },
    tooltip: {
      trigger: "item",
      valueFormatter: (value: unknown) =>
        typeof value === "number"
          ? formatNumber(value, { maximumSignificantDigits: 17 })
          : String(value),
    },
    aria: { enabled: true, description: t("chart.diagnostics.saved") },
    xAxis: {
      type: "value",
      name: useOdds ? t("chart.axis.oddsRatio") : t("chart.axis.coefficient"),
      nameLocation: "middle",
      nameGap: 22,
    },
    yAxis: { type: "category", data: terms, inverse: true },
    series: [
      {
        name:
          typeof confidenceLevel === "number" &&
          Number.isFinite(confidenceLevel)
            ? t("chart.series.interval", { level: confidenceLevel * 100 })
            : t("chart.series.intervalUnknown"),
        type: "custom",
        z: 1,
        data: rows.flatMap((c, i) =>
          Number.isFinite(useOdds ? c.or_ci_lower : c.ci_lower) &&
          Number.isFinite(useOdds ? c.or_ci_upper : c.ci_upper)
            ? [[lo(c), hi(c), i]]
            : [],
        ),
        renderItem: (_params: { dataIndex: number }, api: ErrorBarApi) => {
          const lower = api.value(0);
          const upper = api.value(1);
          const start = api.coord([lower, api.value(2)]);
          const end = api.coord([upper, api.value(2)]);
          const y = start[1];
          const cap = 5;
          const lineStyle = { stroke: "#64748b", lineWidth: 1.5 };
          return {
            type: "group",
            children: [
              {
                type: "line",
                shape: { x1: start[0], y1: y, x2: end[0], y2: y },
                style: lineStyle,
              },
              {
                type: "line",
                shape: { x1: start[0], y1: y - cap, x2: start[0], y2: y + cap },
                style: lineStyle,
              },
              {
                type: "line",
                shape: { x1: end[0], y1: y - cap, x2: end[0], y2: y + cap },
                style: lineStyle,
              },
            ],
          };
        },
      },
      {
        name: t("chart.series.estimate"),
        type: "scatter",
        symbolSize: 9,
        z: 3,
        itemStyle: { color: "#2f6f6a" },
        data: rows.map((c) => value(c)),
      },
      {
        name: t("chart.series.reference"),
        type: "line",
        markLine: {
          silent: true,
          symbol: "none",
          data: [{ xAxis: reference }],
          lineStyle: { color: "#c2452e", type: "dashed" },
        },
        data: [],
      },
    ],
  };

  return (
    <ReactECharts
      option={option}
      style={{ height: Math.max(140, rows.length * 30) }}
    />
  );
}

function ResidualPlots({
  diagnostics,
}: {
  diagnostics: Record<string, unknown>;
}) {
  const { t, formatNumber } = useLocaleDisplay();
  const isClient = useIsClient();
  if (!isClient) return null;
  const residuals = (diagnostics.residuals ?? []) as Array<{
    fitted: number;
    residual: number;
  }>;
  const qq = diagnostics.qq as
    { theoretical: number[]; sample: number[] } | undefined;

  const residualOption = {
    textStyle: BASE_TEXT_STYLE,
    grid: { left: 50, right: 16, top: 16, bottom: 40 },
    tooltip: {
      trigger: "item",
      valueFormatter: (value: unknown) =>
        typeof value === "number"
          ? formatNumber(value, { maximumSignificantDigits: 17 })
          : String(value),
    },
    aria: { enabled: true, description: t("chart.diagnostics.saved") },
    xAxis: {
      type: "value",
      name: t("chart.axis.fitted"),
      nameLocation: "middle",
      nameGap: 26,
    },
    yAxis: { type: "value", name: t("chart.axis.residual") },
    series: [
      {
        type: "scatter",
        symbolSize: 6,
        itemStyle: { color: "#2f6f6a", opacity: 0.6 },
        data: residuals.map((r) => [r.fitted, r.residual]),
      },
      {
        type: "line",
        markLine: {
          silent: true,
          symbol: "none",
          data: [{ yAxis: 0 }],
          lineStyle: { color: "#c2452e" },
        },
        data: [],
      },
    ],
  };

  const qqOption = qq
    ? {
        textStyle: BASE_TEXT_STYLE,
        grid: { left: 50, right: 16, top: 16, bottom: 40 },
        tooltip: {
          trigger: "item",
          valueFormatter: (value: unknown) =>
            typeof value === "number"
              ? formatNumber(value, { maximumSignificantDigits: 17 })
              : String(value),
        },
        aria: { enabled: true, description: t("chart.diagnostics.saved") },
        xAxis: {
          type: "value",
          name: t("chart.axis.theoretical"),
          nameLocation: "middle",
          nameGap: 26,
        },
        yAxis: { type: "value", name: t("chart.axis.sample") },
        series: [
          {
            type: "scatter",
            symbolSize: 6,
            itemStyle: { color: "#3b6fb0", opacity: 0.6 },
            data: qq.theoretical.map((t, i) => [t, qq.sample[i]]),
          },
        ],
      }
    : null;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            <UiMessage id="ui.statistics.residualsVsFitted" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          {residuals.length > 0 ? (
            <ReactECharts option={residualOption} style={{ height: 240 }} />
          ) : (
            <p className="text-xs text-muted-foreground">
              <UiMessage id="ui.statistics.noResidualData" />
            </p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            <UiMessage id="ui.statistics.normalQQ" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          {qqOption ? (
            <ReactECharts option={qqOption} style={{ height: 240 }} />
          ) : (
            <p className="text-xs text-muted-foreground">
              <UiMessage id="ui.statistics.noQQData" />
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="font-mono text-[0.62rem] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="font-mono text-lg font-bold">{value}</span>
    </div>
  );
}

export function StatisticsRegressionView({
  result,
}: {
  result: StatisticalResult;
}) {
  const { formatStat, formatPValue, t } = useLocaleDisplay();

  const coefs = (result.estimates.coefficients ?? []) as Coef[];
  const encoding = (result.estimates.encoding ?? {}) as Record<
    string,
    { reference: string; encoded_levels: string[] }
  >;
  const ds = result.descriptive_statistics;
  const isLogistic = result.method === "logistic_regression";
  const isRegularized = [
    "ridge_regression",
    "lasso_regression",
    "elastic_net_regression",
  ].includes(result.method);
  const confusion = ds.confusion_matrix as
    { tp: number; fp: number; tn: number; fn: number } | undefined;
  const vif = (ds.vif ?? {}) as Record<string, number>;

  return (
    <div className="space-y-4">
      {/* Model summary */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            <UiMessage id="ui.statistics.modelSummary" />
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-x-8 gap-y-3">
          <UiAttributes messages={{ label: { id: "ui.statistics.n" } }}>
            <SummaryStat label={"n"} value={String(ds.n ?? "—")} />
          </UiAttributes>
          {!isRegularized && !isLogistic && (
            <>
              <UiAttributes messages={{ label: { id: "ui.ml.rSquared" } }}>
                <SummaryStat
                  label={"R²"}
                  value={formatStat(ds.r_squared as number)}
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.statistics.adjRSquared" } }}
              >
                <SummaryStat
                  label={"Adj R²"}
                  value={formatStat(ds.adj_r_squared as number)}
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.statistics.fPValue" } }}
              >
                <SummaryStat
                  label={"F p-value"}
                  value={formatPValue(ds.f_pvalue as number)}
                />
              </UiAttributes>
            </>
          )}
          {isLogistic && (
            <>
              <UiAttributes messages={{ label: { id: "ui.ml.rocAuc" } }}>
                <SummaryStat
                  label={"ROC-AUC"}
                  value={formatStat(ds.roc_auc as number)}
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.ml.prAuc" } }}>
                <SummaryStat
                  label={"PR-AUC"}
                  value={formatStat(ds.pr_auc as number)}
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.statistics.pseudoRSquared" } }}
              >
                <SummaryStat
                  label={"Pseudo R²"}
                  value={formatStat(ds.pseudo_r_squared as number)}
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.statistics.converged" } }}
              >
                <SummaryStat
                  label={"Converged"}

                  value={
                    typeof ds.converged === "boolean"
                      ? t(
                          ds.converged
                            ? "workflow.enum.yes"
                            : "workflow.enum.no",
                        )
                      : t("common.unavailable")
                  }
                />
              </UiAttributes>
            </>
          )}
          {isRegularized && (
            <>
              <UiAttributes
                messages={{ label: { id: "ui.statistics.trainRSquared" } }}
              >
                <SummaryStat
                  label={"Train R²"}
                  value={formatStat(ds.train_r2 as number)}
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.statistics.cvRSquaredMean" } }}
              >
                <SummaryStat
                  label={"CV R² mean"}
                  value={formatStat(result.effect_size.cv_r2_mean as number)}
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.statistics.cvRSquaredStd" } }}
              >
                <SummaryStat
                  label={"CV R² std"}
                  value={formatStat(result.effect_size.cv_r2_std as number)}
                />
              </UiAttributes>
              <SummaryStat label="α" value={formatStat(ds.alpha as number)} />
            </>
          )}
          {!isRegularized && (
            <>
              <UiAttributes messages={{ label: { id: "ui.statistics.aic" } }}>
                <SummaryStat
                  label={"AIC"}
                  value={formatStat(ds.aic as number, 1)}
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.statistics.bic" } }}>
                <SummaryStat
                  label={"BIC"}
                  value={formatStat(ds.bic as number, 1)}
                />
              </UiAttributes>
            </>
          )}
        </CardContent>
      </Card>

      {/* Coefficient forest plot */}
      {!isRegularized && coefs.length > 1 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t(
                isLogistic ? "chart.axis.oddsRatio" : "chart.axis.coefficient",
              )}
              {" · "}
              {Number.isFinite(result.confidence_level)
                ? t("chart.series.interval", {
                    level: result.confidence_level * 100,
                  })
                : t("chart.series.intervalUnknown")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ForestPlot
              coefs={coefs}
              useOdds={isLogistic}
              confidenceLevel={result.confidence_level}
            />
          </CardContent>
        </Card>
      )}

      {/* Coefficient table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            <UiMessage id="ui.statistics.coefficientTable" />
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-1.5 pr-3 font-medium">
                  <UiMessage id="ui.statistics.term" />
                </th>
                <th className="px-2 py-1.5 text-right font-medium">
                  {isLogistic ? (
                    <UiMessage id="ui.common.oddsRatio" />
                  ) : (
                    <UiMessage id="ui.common.coefficient" />
                  )}
                </th>
                {!isRegularized && (
                  <th className="px-2 py-1.5 text-right font-medium">
                    <UiMessage id="ui.statistics.stdError" />
                  </th>
                )}
                {!isRegularized && (
                  <th className="px-2 py-1.5 text-right font-medium">
                    <UiMessage id="ui.report.pValue" />
                  </th>
                )}
                {!isRegularized && (
                  <th className="px-2 py-1.5 text-right font-medium">
                    {Number.isFinite(result.confidence_level)
                      ? t("chart.series.interval", {
                          level: result.confidence_level * 100,
                        })
                      : t("chart.series.intervalUnknown")}
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {coefs.map((c) => (
                <tr key={c.term} className="border-b last:border-0">
                  <td className="py-1.5 pr-3 font-mono font-medium">
                    {c.term}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {formatStat(isLogistic ? c.odds_ratio : c.coefficient)}
                  </td>
                  {!isRegularized && (
                    <td className="px-2 py-1.5 text-right font-mono">
                      {formatStat(c.std_error)}
                    </td>
                  )}
                  {!isRegularized && (
                    <td className="px-2 py-1.5 text-right font-mono">
                      {formatPValue(c.p_value)}
                    </td>
                  )}
                  {!isRegularized && (
                    <td className="px-2 py-1.5 text-right font-mono">
                      {isLogistic
                        ? `[${formatStat(c.or_ci_lower)}, ${formatStat(c.or_ci_upper)}]`
                        : `[${formatStat(c.ci_lower)}, ${formatStat(c.ci_upper)}]`}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {/* Logistic: confusion matrix */}
      {isLogistic && confusion && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {" "}
              <UiMessage id="ui.statistics.confusionMatrixThreshold" />{" "}
              {String(ds.classification_threshold ?? 0.5)})
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid max-w-xs grid-cols-3 gap-1 text-center text-xs">
              <div />
              <div className="font-medium text-muted-foreground">
                <UiMessage id="ui.statistics.pred0" />
              </div>
              <div className="font-medium text-muted-foreground">
                <UiMessage id="ui.statistics.pred1" />
              </div>
              <div className="font-medium text-muted-foreground">
                <UiMessage id="ui.statistics.actual0" />
              </div>
              <div className="rounded bg-muted py-2 font-mono">
                {confusion.tn}
              </div>
              <div className="rounded bg-muted py-2 font-mono">
                {confusion.fp}
              </div>
              <div className="font-medium text-muted-foreground">
                <UiMessage id="ui.statistics.actual1" />
              </div>
              <div className="rounded bg-muted py-2 font-mono">
                {confusion.fn}
              </div>
              <div className="rounded bg-muted py-2 font-mono">
                {confusion.tp}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* VIF (linear) */}
      {!isLogistic && !isRegularized && Object.keys(vif).length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              <UiMessage id="ui.statistics.collinearityVif" />
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
            {Object.entries(vif).map(([term, val]) => (
              <span key={term}>
                <span className="font-mono">{term}</span>{" "}
                <span
                  className={
                    val > 10 ? "font-mono font-bold text-red-600" : "font-mono"
                  }
                >
                  {formatStat(val, 2)}
                </span>
              </span>
            ))}
            <span className="text-muted-foreground">
              <UiMessage id="ui.statistics.vif10SuggestsMulticollinearity" />
            </span>
          </CardContent>
        </Card>
      )}

      {/* Encoding map */}
      {Object.keys(encoding).length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              <UiMessage id="ui.statistics.categoricalEncoding" />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-xs">
            {Object.entries(encoding).map(([col, info]) => (
              <div key={col}>
                <span className="font-mono font-medium">{col}</span>
                <span className="ml-2 text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.statistics.reference" />{" "}
                  <span className="font-mono">{info.reference}</span>
                  <UiMessage id="ui.statistics.encodedLevels" />{" "}
                  <span className="font-mono">
                    {info.encoded_levels.join(", ") || "—"}
                  </span>
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Diagnostics (linear) */}
      {!isLogistic &&
        !isRegularized &&
        Object.keys(result.diagnostics).length > 0 && (
          <ResidualPlots diagnostics={result.diagnostics} />
        )}
    </div>
  );
}
