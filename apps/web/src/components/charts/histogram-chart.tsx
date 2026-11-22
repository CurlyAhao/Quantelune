"use client";

import { Message as UiMessage } from "@/components/message";
import dynamic from "next/dynamic";
import { useMemo } from "react";

import { BASE_TEXT_STYLE, CHART_COLORS, useIsClient } from "./effect";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});

function ChartSkeleton() {
  return (
    <div className="flex h-56 w-full items-center justify-center text-xs text-muted-foreground">
      {" "}
      <UiMessage id="ui.statistics.loadingChart" />{" "}
    </div>
  );
}

interface HistogramChartProps {
  bins: number[];
  counts: number[];
  height?: number;
}

/** Distribution histogram for a numeric column. */
export function HistogramChart({
  bins,
  counts,
  height = 224,
}: HistogramChartProps) {
  const isClient = useIsClient();

  const option = useMemo(() => {
    const labels = bins.slice(0, -1).map((b, i) => {
      const next = bins[i + 1];
      return `${fmt(b)}–${fmt(next)}`;
    });
    return {
      grid: { left: 44, right: 12, top: 16, bottom: 40 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        textStyle: BASE_TEXT_STYLE,
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: {
          ...BASE_TEXT_STYLE,
          fontSize: 10,
          interval: Math.max(0, Math.floor(labels.length / 6) - 1),
          rotate: labels.length > 8 ? 30 : 0,
        },
        axisLine: { lineStyle: { color: CHART_COLORS.grid } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: { ...BASE_TEXT_STYLE, fontSize: 10 },
        splitLine: { lineStyle: { color: CHART_COLORS.grid } },
      },
      series: [
        {
          type: "bar",
          data: counts,
          itemStyle: {
            color: CHART_COLORS.teal,
            borderRadius: [3, 3, 0, 0],
          },
          emphasis: { itemStyle: { color: CHART_COLORS.tealSoft } },
          animationDuration: 700,
        },
      ],
    };
  }, [bins, counts]);

  if (!isClient) return <ChartSkeleton />;

  return (
    <ReactECharts
      option={option}
      style={{ height, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
      lazyUpdate
    />
  );
}

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2);
}
