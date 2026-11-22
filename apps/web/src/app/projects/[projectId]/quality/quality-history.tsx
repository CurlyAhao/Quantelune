"use client";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useQuery } from "@tanstack/react-query";
import { ChevronRightIcon, HistoryIcon } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { qualityApi } from "@/lib/api-endpoints";
import { QUALITY_RUN_STATUS_META, friendlyApiError } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { QualityRunSummary } from "@/lib/types";

function scoreTextColor(score: number | null): string {
  if (score === null) return "text-muted-foreground";
  if (score < 40) return "text-red-600";
  if (score < 70) return "text-amber-600";
  return "text-teal-700";
}

interface QualityHistoryTabProps {
  datasetId: string;
  activeRunId: string;
  onViewRun: (runId: string) => void;
}

export function QualityHistoryTab({
  datasetId,
  activeRunId,
  onViewRun,
}: QualityHistoryTabProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const runsQuery = useQuery({
    queryKey: ["quality", "runs", datasetId],
    queryFn: ({ signal }) => qualityApi.listRuns(datasetId, signal),
  });

  if (runsQuery.isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12">
        <Spinner className="size-5 text-primary" />
        <span className="text-sm text-muted-foreground">
          <UiMessage id="ui.quality.loadingRunHistory" />
        </span>
      </div>
    );
  }

  if (runsQuery.isError) {
    const friendly = friendlyApiError(runsQuery.error, uiCopyT);
    return (
      <ErrorState
        title={friendly.title}
        message={friendly.message}
        onRetry={() => void runsQuery.refetch()}
      />
    );
  }

  const runs = runsQuery.data?.runs ?? [];

  if (runs.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
          <HistoryIcon className="size-7 text-primary/40" />
          <p className="text-sm font-medium">
            <UiMessage id="ui.quality.noQualityRunsYet" />
          </p>
          <p className="max-w-sm text-xs text-muted-foreground">
            {" "}
            <UiMessage id="ui.quality.everyRunIncludingRecomputesWithDifferentThresholdsIsKeptHereSoYouCanCompareScoresOverTime" />{" "}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      {runs.map((run, index) => (
        <HistoryRow
          key={run.id}
          run={run}
          isLatest={index === 0}
          isActive={run.id === activeRunId}
          onView={() => onViewRun(run.id)}
        />
      ))}
    </div>
  );
}

function HistoryRow({
  run,
  isLatest,
  isActive,
  onView,
}: {
  run: QualityRunSummary;
  isLatest: boolean;
  isActive: boolean;
  onView: () => void;
}) {
  const { t: uiLabelT } = useDomainLanguage();

  const { formatDate } = useLocaleDisplay();

  const statusMeta = QUALITY_RUN_STATUS_META[run.status] ?? {
    label: run.status,
    tone: "slate" as const,
  };

  return (
    <UiAttributes
      messages={{
        "aria-label": {
          id: "ui.quality.loadQualityRunFromValue",
          values: { value0: String(formatDate(run.created_at)) },
        },
      }}
    >
      <button
        aria-label={"Load quality run from {value0}".replaceAll(
          "{value0}",
          String(formatDate(run.created_at)),
        )}
        type="button"
        onClick={onView}
        aria-pressed={isActive}

        className={cn(
          "flex w-full items-center gap-4 rounded-lg border bg-card px-4 py-3 text-left transition-all outline-none hover:shadow-plate focus-visible:ring-3 focus-visible:ring-ring/50",
          isActive && "border-primary ring-2 ring-primary/25",
        )}
      >
        {/* Score block */}
        <div className="w-14 shrink-0 text-center">
          <span
            className={cn(
              "font-heading text-2xl font-bold tabular-nums",
              scoreTextColor(run.overall_score),
            )}
          >
            {run.overall_score !== null ? Math.round(run.overall_score) : "—"}
          </span>
          <span className="block text-[0.6rem] tracking-wide text-muted-foreground uppercase">
            {" "}
            <UiMessage id="ui.quality.score" />{" "}
          </span>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">
              {formatDate(run.created_at)}
            </span>
            <StatusBadge
              label={localizedMetaLabel(statusMeta, uiLabelT)}
              tone={statusMeta.tone}
              dot={false}
            />
            {isLatest && (
              <UiAttributes messages={{ label: { id: "ui.quality.latest" } }}>
                <StatusBadge label={"latest"} tone="blue" dot={false} />
              </UiAttributes>
            )}
            {isActive && (
              <UiAttributes messages={{ label: { id: "ui.quality.viewing" } }}>
                <StatusBadge label={"viewing"} tone="teal" dot={false} />
              </UiAttributes>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[0.65rem] text-muted-foreground">
            <span>
              <UiMessage id="ui.quality.ruleset" /> {run.ruleset_version}
            </span>
            <span>
              {run.total_rules} <UiMessage id="ui.quality.rules" />
            </span>
            <span className="text-teal-700">
              {run.passed_count} <UiMessage id="ui.quality.passed" />
            </span>
            {run.warning_count > 0 && (
              <span className="text-amber-700">
                {run.warning_count} <UiMessage id="ui.quality.warn" />
              </span>
            )}
            {run.failed_count > 0 && (
              <span className="text-red-700">
                {run.failed_count} <UiMessage id="ui.quality.failed" />
              </span>
            )}
            {run.error_count > 0 && (
              <span className="text-red-700">
                {run.error_count} <UiMessage id="ui.quality.error" />
              </span>
            )}
            {run.skipped_count > 0 && (
              <span>
                {run.skipped_count} <UiMessage id="ui.quality.skipped" />
              </span>
            )}
            {run.ohlcv_enabled && (
              <span>
                <UiMessage id="ui.residual.quality.ohlcv" />
              </span>
            )}
            {run.sampled && (
              <span>
                <UiMessage id="ui.quality.sampled" />
              </span>
            )}
          </div>
        </div>

        <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground/40" />
      </button>
    </UiAttributes>
  );
}
