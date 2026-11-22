"use client";

import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import type { NoParameterMessageKey } from "@/lib/i18n-messages";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import {
  CalendarIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  HashIcon,
  TagIcon,
  TypeIcon,
  AlertTriangleIcon,
} from "lucide-react";

import { HistogramChart } from "@/components/charts/histogram-chart";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatNumber, type BadgeTone } from "@/lib/format";
import type { ProfileColumn } from "@/lib/types";

const TYPE_META: Record<
  string,
  {
    label: string;
    labelKey?: NoParameterMessageKey;
    tone: BadgeTone;
    icon: typeof HashIcon;
  }
> = {
  integer: {
    label: "integer",
    labelKey: "ui.profiling.integer",
    tone: "teal",
    icon: HashIcon,
  },
  float: {
    label: "float",
    labelKey: "ui.profiling.float",
    tone: "teal",
    icon: HashIcon,
  },
  boolean: {
    label: "boolean",
    labelKey: "ui.profiling.boolean",
    tone: "blue",
    icon: TagIcon,
  },
  string: {
    label: "string",
    labelKey: "ui.profiling.string",
    tone: "slate",
    icon: TypeIcon,
  },
  categorical: {
    label: "categorical",
    labelKey: "ui.profiling.categorical",
    tone: "amber",
    icon: TagIcon,
  },
  date: {
    label: "date",
    labelKey: "ui.profiling.date",
    tone: "blue",
    icon: CalendarIcon,
  },
  datetime: {
    label: "datetime",
    labelKey: "ui.profiling.datetime",
    tone: "blue",
    icon: CalendarIcon,
  },
  identifier: {
    label: "identifier",
    labelKey: "ui.profiling.identifier",
    tone: "slate",
    icon: HashIcon,
  },
  constant: {
    label: "constant",
    labelKey: "ui.profiling.constant",
    tone: "red",
    icon: TagIcon,
  },
  empty: {
    label: "empty",
    labelKey: "ui.profiling.empty",
    tone: "red",
    icon: TagIcon,
  },
  unknown: {
    label: "unknown",
    labelKey: "ui.profiling.unknown",
    tone: "slate",
    icon: TypeIcon,
  },
};

export function ProfilingColumns({ columns }: { columns: ProfileColumn[] }) {
  const { t: uiLabelT } = useDomainLanguage();

  const { formatNumber } = useLocaleDisplay();

  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="space-y-2">
      {columns.map((col) => {
        const meta = TYPE_META[col.inferred_type] ?? TYPE_META.unknown;
        const Icon = meta.icon;
        const isOpen = expanded === col.name;
        return (
          <div
            key={col.name}
            className="overflow-hidden rounded-lg border bg-card transition-shadow hover:shadow-plate"
          >
            {/* Row header */}
            <button
              type="button"
              onClick={() => setExpanded(isOpen ? null : col.name)}
              aria-expanded={isOpen}
              className="flex w-full items-center gap-3 px-4 py-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {isOpen ? (
                <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
              )}
              <Icon className="size-4 shrink-0 text-primary" />
              <span className="min-w-0 flex-1 truncate font-mono text-sm font-medium">
                {col.name}
              </span>
              {col.warnings.length > 0 && (
                <AlertTriangleIcon className="size-4 shrink-0 text-amber-500" />
              )}
              <div className="flex shrink-0 items-center gap-1.5">
                <StatusBadge
                  label={localizedMetaLabel(meta, uiLabelT)}
                  tone={meta.tone}
                  dot={false}
                />
                <span className="font-mono text-[0.62rem] text-muted-foreground">
                  {Math.round(col.inference_confidence * 100)}%
                </span>
              </div>
              <div className="hidden w-32 shrink-0 sm:block">
                <MissingRateBar rate={col.null_rate} />
              </div>
              <span className="hidden w-20 shrink-0 text-right font-mono text-xs text-muted-foreground md:block">
                {formatNumber(col.unique_count)}{" "}
                <UiMessage id="ui.profiling.uniq" />{" "}
              </span>
            </button>

            {/* Expanded detail */}
            {isOpen && (
              <div className="border-t bg-muted/30 px-4 py-4">
                <ColumnDetail col={col} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MissingRateBar({ rate }: { rate: number }) {
  const pct = rate * 100;
  const color = pct > 20 ? "#c2452e" : pct > 5 ? "#c99a2e" : "#2f7a74";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="w-11 text-right font-mono text-[0.65rem] text-muted-foreground">
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}

function ColumnDetail({ col }: { col: ProfileColumn }) {
  const { formatNumber, formatPercent } = useLocaleDisplay();

  const hasNumeric = col.numeric_stats !== null;
  const hasCategorical = col.categorical_stats !== null;
  const hasDatetime = col.datetime_stats !== null;

  return (
    <div className="space-y-4">
      {col.warnings.length > 0 && (
        <div className="space-y-1.5">
          {col.warnings.map((w, i) => (
            <div
              key={i}
              className="flex items-start gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900"
            >
              <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
              {w}
            </div>
          ))}
        </div>
      )}

      {/* Common stats grid */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        <UiAttributes messages={{ label: { id: "ui.profiling.count" } }}>
          <Stat label={"Count"} value={formatNumber(col.count)} />
        </UiAttributes>
        <UiAttributes messages={{ label: { id: "ui.profiling.nulls" } }}>
          <Stat
            label={"Nulls"}
            value={`${formatNumber(col.null_count)} (${formatPercent(col.null_rate)})`}
          />
        </UiAttributes>
        <UiAttributes messages={{ label: { id: "ui.profiling.unique" } }}>
          <Stat
            label={"Unique"}
            value={`${formatNumber(col.unique_count)} (${formatPercent(col.unique_rate)})`}
          />
        </UiAttributes>
        <UiAttributes messages={{ label: { id: "ui.profiling.physicalType" } }}>
          <Stat label={"Physical type"} value={col.physical_type} mono />
        </UiAttributes>
      </div>

      {col.sample_values.length > 0 && (
        <div>
          <p className="mb-1.5 text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
            {" "}
            <UiMessage id="ui.profiling.sampleValues" />{" "}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {col.sample_values.map((v, i) => (
              <code
                key={i}
                className="rounded-sm border bg-background px-1.5 py-0.5 font-mono text-[0.7rem]"
              >
                {v}
              </code>
            ))}
          </div>
        </div>
      )}

      {/* Numeric stats + histogram */}
      {hasNumeric && col.numeric_stats && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              <UiMessage id="ui.profiling.distribution" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <HistogramChart
              bins={col.numeric_stats.histogram.bins}
              counts={col.numeric_stats.histogram.counts}
            />
            <div className="mt-4 grid grid-cols-3 gap-x-6 gap-y-2 text-sm sm:grid-cols-6">
              <UiAttributes messages={{ label: { id: "ui.profiling.mean" } }}>
                <Stat
                  label={"Mean"}
                  value={fmt(col.numeric_stats.mean, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.median" } }}>
                <Stat
                  label={"Median"}
                  value={fmt(col.numeric_stats.median, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.stdDev" } }}>
                <Stat
                  label={"Std dev"}
                  value={fmt(col.numeric_stats.std, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.min" } }}>
                <Stat
                  label={"Min"}
                  value={fmt(col.numeric_stats.min, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.max" } }}>
                <Stat
                  label={"Max"}
                  value={fmt(col.numeric_stats.max, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.skewness" } }}
              >
                <Stat
                  label={"Skewness"}
                  value={fmt(col.numeric_stats.skewness, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.q1" } }}>
                <Stat label={"Q1"} value={fmt(col.numeric_stats.q1)} mono />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.q3" } }}>
                <Stat label={"Q3"} value={fmt(col.numeric_stats.q3)} mono />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.iqr" } }}>
                <Stat
                  label={"IQR"}
                  value={fmt(col.numeric_stats.iqr, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.p05" } }}>
                <Stat label={"P05"} value={fmt(col.numeric_stats.p05)} mono />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.p95" } }}>
                <Stat label={"P95"} value={fmt(col.numeric_stats.p95)} mono />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.outliers" } }}
              >
                <Stat
                  label={"Outliers"}

                  value={`${formatNumber(col.numeric_stats.outlier_count)} (${formatPercent(col.numeric_stats.outlier_rate)})`}
                  mono
                />
              </UiAttributes>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Categorical top values */}
      {hasCategorical && col.categorical_stats && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              <UiMessage id="ui.profiling.topValues" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1.5">
              {col.categorical_stats.top_values.map((tv) => (
                <div key={tv.value} className="flex items-center gap-3">
                  <span
                    className="w-36 truncate font-mono text-xs"
                    title={tv.value}
                  >
                    {tv.value}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary/70 transition-[width] duration-500"
                      style={{ width: `${tv.proportion * 100}%` }}
                    />
                  </div>
                  <span className="w-20 text-right font-mono text-[0.65rem] text-muted-foreground">
                    {formatNumber(tv.count)} ·{" "}
                    {(tv.proportion * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-3 gap-4 text-sm">
              <UiAttributes
                messages={{ label: { id: "ui.profiling.avgLength" } }}
              >
                <Stat
                  label={"Avg length"}
                  value={fmt(col.categorical_stats.avg_length, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.minLength" } }}
              >
                <Stat
                  label={"Min length"}
                  value={String(col.categorical_stats.min_length)}
                  mono
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.maxLength" } }}
              >
                <Stat
                  label={"Max length"}
                  value={String(col.categorical_stats.max_length)}
                  mono
                />
              </UiAttributes>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Datetime stats */}
      {hasDatetime && col.datetime_stats && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              <UiMessage id="ui.profiling.timeRange" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
              <UiAttributes
                messages={{ label: { id: "ui.profiling.earliest" } }}
              >
                <Stat
                  label={"Earliest"}
                  value={col.datetime_stats.earliest}
                  mono
                />
              </UiAttributes>
              <UiAttributes messages={{ label: { id: "ui.profiling.latest" } }}>
                <Stat label={"Latest"} value={col.datetime_stats.latest} mono />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.spanDays" } }}
              >
                <Stat
                  label={"Span (days)"}
                  value={fmt(col.datetime_stats.span_days, formatNumber)}
                  mono
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.monotonic" } }}
              >
                <Stat
                  label={"Monotonic"}

                  value={col.datetime_stats.is_monotonic ? "yes" : "no"}
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.duplicates" } }}
              >
                <Stat
                  label={"Duplicates"}
                  value={formatNumber(col.datetime_stats.duplicate_count)}
                  mono
                />
              </UiAttributes>
              <UiAttributes
                messages={{ label: { id: "ui.profiling.invalid" } }}
              >
                <Stat
                  label={"Invalid"}
                  value={formatNumber(col.datetime_stats.invalid_count)}
                  mono
                />
              </UiAttributes>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <p className="text-[0.62rem] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      <p
        className={`mt-0.5 text-sm font-medium ${mono ? "font-mono text-xs" : ""}`}
      >
        {value}
      </p>
    </div>
  );
}

function fmt(
  n: number,
  countFormatter: (value: number) => string = formatNumber,
): string {
  if (!Number.isFinite(n)) return "—";
  if (Number.isInteger(n)) return countFormatter(n);
  if (Math.abs(n) >= 1000) return countFormatter(Math.round(n));
  return n.toFixed(3);
}
