"use client";

import { Message as UiMessage } from "@/components/message";
import dynamic from "next/dynamic";
import { useMemo } from "react";
import { useLanguage } from "@/providers/language-provider";
import { escapeTooltipText } from "@/lib/chart-tooltip";

import { BASE_TEXT_STYLE, useIsClient } from "./effect";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});

function ChartSkeleton() {
  return (
    <div className="flex h-80 w-full items-center justify-center text-xs text-muted-foreground">
      {" "}
      <UiMessage id="ui.charts.loadingHeatmap" />{" "}
    </div>
  );
}

interface HeatmapChartProps {
  columns: string[];
  matrix: (number | null)[][];
  height?: number;
}

/** Correlation heatmap with a blue (−1) → white (0) → red (+1) scale. */
export function HeatmapChart({
  columns,
  matrix,
  height = 420,
}: HeatmapChartProps) {
  const { t, formatStat } = useLanguage();
  const isClient = useIsClient();

  const option = useMemo(() => {
    const data: [number, number, number | null][] = [];
    matrix.forEach((row, y) => {
      row.forEach((value, x) => {
        data.push([x, y, value]);
      });
    });

    return {
      aria: {
        enabled: true,
        description: t("chart.heatmap.description", { count: columns.length }),
      },
      grid: {
        left: 110,
        right: 60,
        top: 60,
        bottom: 30,
      },
      tooltip: {
        position: "top",
        textStyle: BASE_TEXT_STYLE,
        formatter: (params: { value: [number, number, number | null] }) => {
          const [x, y, v] = params.value;
          const val =
            typeof v === "number" && Number.isFinite(v)
              ? formatStat(v, 3)
              : t("common.unavailable");
          return `${escapeTooltipText(columns[y])} × ${escapeTooltipText(columns[x])}<br/>r = <b>${escapeTooltipText(val)}</b>`;
        },
      },
      xAxis: {
        type: "category",
        data: columns,
        position: "top",
        axisLabel: {
          ...BASE_TEXT_STYLE,
          fontSize: 10,
          rotate: 38,
          interval: 0,
          formatter: (value: string) => truncate(value, 10),
        },
        axisLine: { show: false },
        axisTick: { show: false },
        splitArea: { show: false },
      },
      yAxis: {
        type: "category",
        data: columns,
        axisLabel: {
          ...BASE_TEXT_STYLE,
          fontSize: 10,
          formatter: (value: string) => truncate(value, 12),
        },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      visualMap: {
        min: -1,
        max: 1,
        calculable: false,
        orient: "vertical",
        right: 0,
        top: "center",
        itemHeight: 160,
        textStyle: { ...BASE_TEXT_STYLE, fontSize: 10 },
        inRange: {
          color: ["#3b62c4", "#8fa5dd", "#f6f7f9", "#e2948a", "#c2452e"],
        },
        text: ["+1", "−1"],
      },
      series: [
        {
          type: "heatmap",
          data,
          itemStyle: {
            borderColor: "#ffffff",
            borderWidth: 1.5,
            borderRadius: 3,
          },
          emphasis: {
            itemStyle: { shadowBlur: 8, shadowColor: "rgba(44,58,82,0.3)" },
          },
          label: {
            show: columns.length <= 8,
            fontSize: 9,
            color: "#2c3a52",
            formatter: (params: { value: [number, number, number | null] }) =>
              params.value[2] === null ? "" : formatStat(params.value[2], 2),
          },
          animationDuration: 600,
        },
      ],
    };
  }, [columns, matrix, t, formatStat]);

  if (!isClient) return <ChartSkeleton />;
  if (columns.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
        {" "}
        <UiMessage id="ui.charts.noNumericColumnsAvailableForCorrelation" />{" "}
      </div>
    );
  }

  const dynamicHeight = Math.max(height, columns.length * 30 + 120);

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

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}
