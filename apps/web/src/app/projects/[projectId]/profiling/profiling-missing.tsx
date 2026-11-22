"use client";

import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import dynamic from "next/dynamic";
import { AlertCircleIcon, CheckCircle2Icon } from "lucide-react";

import { MissingBarChart } from "@/components/charts/missing-bar-chart";
import {
  BASE_TEXT_STYLE,
  CHART_COLORS,
  useIsClient,
} from "@/components/charts/effect";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { ProfileMissingness } from "@/lib/types";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

export function ProfilingMissing({
  missingness,
}: {
  missingness: ProfileMissingness;
}) {
  const { formatNumber, formatPercent } = useLocaleDisplay();

  const isClient = useIsClient();
  const hasMissing = missingness.per_column.some((c) => c.null_count > 0);

  const rowDistOption = {
    grid: { left: 48, right: 16, top: 16, bottom: 36 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      textStyle: BASE_TEXT_STYLE,
    },
    xAxis: {
      type: "category",
      name: "missing values per row",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { ...BASE_TEXT_STYLE, fontSize: 10 },
      data: missingness.row_missing_distribution.map((d) => d.missing_count),
      axisLabel: { ...BASE_TEXT_STYLE, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_COLORS.grid } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      name: "rows",
      nameTextStyle: { ...BASE_TEXT_STYLE, fontSize: 10 },
      axisLabel: { ...BASE_TEXT_STYLE, fontSize: 10 },
      splitLine: { lineStyle: { color: CHART_COLORS.grid } },
    },
    series: [
      {
        type: "bar",
        data: missingness.row_missing_distribution.map((d) => d.row_count),
        itemStyle: { color: CHART_COLORS.amber, borderRadius: [3, 3, 0, 0] },
        animationDuration: 700,
      },
    ],
  };

  return (
    <div className="space-y-6">
      {/* Summary row */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <UiAttributes messages={{ label: { id: "ui.profiling.completeRows" } }}>
          <SummaryTile
            label={"Complete rows"}

            value={`${formatNumber(missingness.complete_rows)} (${formatPercent(missingness.complete_row_rate)})`}
          />
        </UiAttributes>
        <UiAttributes
          messages={{ label: { id: "ui.profiling.allNullColumns" } }}
        >
          <SummaryTile
            label={"All-null columns"}

            value={String(missingness.all_null_columns.length)}
            warn={missingness.all_null_columns.length > 0}
          />
        </UiAttributes>
        <UiAttributes
          messages={{ label: { id: "ui.profiling.highMissingColumns50" } }}
        >
          <SummaryTile
            label={"High-missing columns (>50%)"}

            value={String(missingness.high_missing_columns.length)}
            warn={missingness.high_missing_columns.length > 0}
          />
        </UiAttributes>
      </div>

      {(missingness.all_null_columns.length > 0 ||
        missingness.high_missing_columns.length > 0) && (
        <div className="space-y-1.5">
          {missingness.all_null_columns.map((c) => (
            <div
              key={c}
              className="flex items-center gap-2 rounded-md border border-red-300/60 bg-red-50 px-3 py-2 text-xs text-red-900"
            >
              <AlertCircleIcon className="size-3.5 shrink-0" />{" "}
              <UiMessage id="workflow.parameter.column" />{" "}
              <code className="font-mono font-semibold">{c}</code>{" "}
              <UiMessage id="ui.profiling.isEntirelyEmpty" />{" "}
            </div>
          ))}
          {missingness.high_missing_columns.map((c) => (
            <div
              key={c}
              className="flex items-center gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900"
            >
              <AlertCircleIcon className="size-3.5 shrink-0" />{" "}
              <UiMessage id="workflow.parameter.column" />{" "}
              <code className="font-mono font-semibold">{c}</code>{" "}
              <UiMessage id="ui.profiling.isMissingMoreThan50OfValues" />{" "}
            </div>
          ))}
        </div>
      )}

      {/* Per-column missing rates */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            <UiMessage id="ui.profiling.missingRateByColumn" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!hasMissing ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <CheckCircle2Icon className="size-4 text-teal-600" />{" "}
              <UiMessage id="ui.profiling.noMissingValuesInThisDataset" />{" "}
            </div>
          ) : (
            <MissingBarChart data={missingness.per_column} />
          )}
        </CardContent>
      </Card>

      {/* Row missing distribution */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            <UiMessage id="ui.profiling.rowsByMissingValueCount" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          {missingness.row_missing_distribution.length === 0 || !isClient ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              {" "}
              <UiMessage id="ui.profiling.noDistributionData" />{" "}
            </div>
          ) : (
            <ReactECharts
              option={rowDistOption}
              style={{ height: 240, width: "100%" }}
              opts={{ renderer: "canvas" }}
              notMerge
              lazyUpdate
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryTile({
  label,
  value,
  warn,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-3 shadow-plate">
      <p className="text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      <p
        className={`mt-1 font-heading text-lg font-bold tabular-nums ${warn ? "text-red-600" : ""}`}
      >
        {value}
      </p>
    </div>
  );
}
