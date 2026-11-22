"use client";

import { useLanguage as usePropertyLanguage } from "@/providers/language-provider";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { AlertTriangleIcon, CheckCircle2Icon, InfoIcon } from "lucide-react";

import { QualityScore } from "@/components/charts/quality-score";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { ProfileOverview, ProfileQuality } from "@/lib/types";

const SEVERITY_META: Record<
  string,
  { tone: "teal" | "amber" | "slate" | "red" | "blue"; icon: typeof InfoIcon }
> = {
  critical: { tone: "red", icon: AlertTriangleIcon },
  high: { tone: "red", icon: AlertTriangleIcon },
  medium: { tone: "amber", icon: InfoIcon },
  low: { tone: "slate", icon: InfoIcon },
  info: { tone: "teal", icon: CheckCircle2Icon },
};

interface OverviewTabProps {
  overview: ProfileOverview;
  quality: ProfileQuality;
  fileSize: number | null;
}

export function ProfilingOverview({
  overview,
  quality,
  fileSize,
}: OverviewTabProps) {
  const { t: propertyT } = usePropertyLanguage();

  const { formatNumber, formatPercent } = useLocaleDisplay();

  const typeBreakdown = [
    {
      label: propertyT("ui.profiling.numeric"),
      value: overview.numeric_columns,
      color: "#2f7a74",
    },
    {
      label: propertyT("workflow.parameter.categorical"),
      value: overview.categorical_columns,
      color: "#c99a2e",
    },
    {
      label: propertyT("ui.profiling.datetime.profilingOverview"),
      value: overview.datetime_columns,
      color: "#3b62c4",
    },
    {
      label: propertyT("ui.profiling.boolean.profilingOverview"),
      value: overview.boolean_columns,
      color: "#8b5cf6",
    },
    {
      label: propertyT("ui.profiling.string.profilingOverview"),
      value: overview.string_columns,
      color: "#c2452e",
    },
    {
      label: propertyT("ui.profiling.other"),
      value: overview.other_columns,
      color: "#7d8aa0",
    },
  ].filter((t) => t.value > 0);

  return (
    <div className="space-y-6">
      {/* Headline stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <UiAttributes messages={{ label: { id: "ui.profiling.rows" } }}>
          <StatTile label={"Rows"} value={formatNumber(overview.row_count)} />
        </UiAttributes>
        <UiAttributes
          messages={{ label: { id: "workflow.parameter.columns" } }}
        >
          <StatTile
            label={"Columns"}
            value={formatNumber(overview.column_count)}
          />
        </UiAttributes>
        <UiAttributes messages={{ label: { id: "ui.profiling.fileSize" } }}>
          <StatTile
            label={"File size"}
            value={fileSize !== null ? fmtBytes(fileSize) : "—"}
          />
        </UiAttributes>
        <UiAttributes messages={{ label: { id: "ui.profiling.completeness" } }}>
          <StatTile
            label={"Completeness"}
            value={formatPercent(overview.completeness)}
            accent
          />
        </UiAttributes>
        <UiAttributes
          messages={{ label: { id: "ui.profiling.duplicateRows" } }}
        >
          <StatTile
            label={"Duplicate rows"}
            value={formatPercent(overview.duplicate_row_rate)}
          />
        </UiAttributes>
        <UiAttributes messages={{ label: { id: "ui.profiling.quality" } }}>
          <StatTile
            label={"Quality"}
            value={`${Math.round(quality.total_score)}`}
            accent
          />
        </UiAttributes>
      </div>

      {overview.sampled && (
        <div className="flex items-center gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <InfoIcon className="size-4 shrink-0" />{" "}
          <UiMessage id="ui.profiling.profiledOnASampleOf" />{" "}
          {formatNumber(overview.sample_size)}{" "}
          <UiMessage id="ui.profiling.rows.profilingOverview" />{" "}
          {formatNumber(overview.row_count)}{" "}
          <UiMessage id="ui.profiling.totalStatisticsAreEstimates" />{" "}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        {/* Quality score */}
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle className="text-base">
              <UiMessage id="ui.profiling.dataQuality" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <QualityScore quality={quality} />
          </CardContent>
        </Card>

        {/* Column type breakdown */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">
              <UiMessage id="ui.profiling.columnTypes" />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* Stacked bar */}
            <div className="flex h-4 w-full overflow-hidden rounded-full bg-muted">
              {typeBreakdown.map((t) => (
                <div
                  key={t.label}
                  className="h-full transition-[width] duration-700"
                  style={{
                    width: `${(t.value / Math.max(overview.column_count, 1)) * 100}%`,
                    backgroundColor: t.color,
                  }}
                  title={`${t.label}: ${t.value}`}
                />
              ))}
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
              {typeBreakdown.map((t) => (
                <div key={t.label} className="flex items-center gap-2 text-sm">
                  <span
                    className="size-2.5 shrink-0 rounded-sm"
                    style={{ backgroundColor: t.color }}
                  />
                  <span className="text-muted-foreground">{t.label}</span>
                  <span className="ml-auto font-mono text-xs font-semibold">
                    {t.value}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Alerts */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {" "}
            <UiMessage id="ui.profiling.alerts" />{" "}
            <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
              {quality.alerts.length}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {quality.alerts.length === 0 ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CheckCircle2Icon className="size-4 text-teal-600" />{" "}
              <UiMessage id="ui.profiling.noQualityAlertsForThisDataset" />{" "}
            </div>
          ) : (
            <ul className="divide-y">
              {quality.alerts.map((alert, i) => {
                const meta = SEVERITY_META[alert.severity] ?? SEVERITY_META.low;
                const Icon = meta.icon;
                return (
                  <li
                    key={i}
                    className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0"
                  >
                    <Icon className="mt-0.5 size-4 shrink-0" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge
                          label={alert.severity}
                          tone={meta.tone}
                          dot={false}
                        />
                        <span className="font-mono text-[0.65rem] tracking-wide text-muted-foreground uppercase">
                          {alert.category}
                        </span>
                      </div>
                      <p className="mt-1 text-sm">{alert.message}</p>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-3 shadow-plate">
      <p className="text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      <p
        className={`mt-1 font-heading text-xl font-bold tabular-nums ${
          accent ? "text-primary" : ""
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let v = bytes / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[u]}`;
}
