"use client";

import { Message as UiMessage } from "@/components/message";
import dynamic from "next/dynamic";
import { useMemo } from "react";
import { useLanguage } from "@/providers/language-provider";
import { escapeTooltipText } from "@/lib/chart-tooltip";

import { BASE_TEXT_STYLE, CHART_COLORS, useIsClient } from "./effect";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});

function ChartSkeleton() {
  return (
    <div className="flex h-64 w-full items-center justify-center text-xs text-muted-foreground">
      {" "}
      <UiMessage id="ui.statistics.loadingChart" />{" "}
    </div>
  );
}

interface MissingBarChartProps {
  data: { name: string; null_rate: number }[];
  height?: number;
}

/** Horizontal bar chart of per-column missing rates, sorted descending. */
export function MissingBarChart({ data, height }: MissingBarChartProps) {
  const { t, formatStat } = useLanguage();
  const isClient = useIsClient();

  const sorted = useMemo(
    () => [...data].sort((a, b) => b.null_rate - a.null_rate).slice(0, 25),
    [data],
  );

  const option = useMemo(() => {
    const names = sorted.map((d) => d.name).reverse();
    const rates = sorted.map((d) => d.null_rate * 100).reverse();

    return {
      aria: {
        enabled: true,
        description: t("chart.missing.description", { count: sorted.length }),
      },
      grid: { left: 130, right: 48, top: 12, bottom: 20 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        textStyle: BASE_TEXT_STYLE,
        formatter: (params: { name: string; value: number }[]) => {
          const p = params[0];
          return t("chart.tooltip.missing", {
            name: escapeTooltipText(p.name),
            value: escapeTooltipText(formatStat(p.value, 1)),
          });
        },
      },
      xAxis: {
        type: "value",
        max: 100,
        axisLabel: { ...BASE_TEXT_STYLE, fontSize: 10, formatter: "{value}%" },
        splitLine: { lineStyle: { color: CHART_COLORS.grid } },
      },
      yAxis: {
        type: "category",
        data: names,
        axisLabel: {
          ...BASE_TEXT_STYLE,
          fontSize: 10,
          formatter: (value: string) =>
            value.length > 16 ? `${value.slice(0, 15)}…` : value,
        },
        axisLine: { lineStyle: { color: CHART_COLORS.grid } },
        axisTick: { show: false },
      },
      series: [
        {
          type: "bar",
          data: rates.map((r) => ({
            value: r,
            itemStyle: {
              color:
                r > 20
                  ? CHART_COLORS.positive
                  : r > 5
                    ? CHART_COLORS.amber
                    : CHART_COLORS.teal,
              borderRadius: [0, 3, 3, 0],
            },
          })),
          label: {
            show: true,
            position: "right",
            fontSize: 9,
            color: CHART_COLORS.muted,
            formatter: (p: { value: number }) => `${formatStat(p.value, 1)}%`,
          },
          animationDuration: 700,
        },
      ],
    };
  }, [sorted, t, formatStat]);

  if (!isClient) return <ChartSkeleton />;

  const dynamicHeight = height ?? Math.max(160, sorted.length * 24 + 40);

  return (
    <ReactECharts
      option={option}
      style={{ height: dynamicHeight, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
      lazyUpdate
    />
  );
}
