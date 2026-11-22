"use client";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useQuery } from "@tanstack/react-query";
import { HistoryIcon } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { StatusBadge } from "@/components/status-badge";
import { statisticsApi } from "@/lib/api-endpoints";
import { STAT_RUN_STATUS_META, friendlyApiError } from "@/lib/format";

interface StatisticsHistoryProps {
  datasetId: string;
  activeRunId: string | null;
  onViewRun: (runId: string) => void;
}

export function StatisticsHistoryTab({
  datasetId,
  activeRunId,
  onViewRun,
}: StatisticsHistoryProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const { formatStat, formatPValue, formatDate } = useLocaleDisplay();

  const runsQuery = useQuery({
    queryKey: ["statistics", "runs", datasetId],
    queryFn: ({ signal }) =>
      statisticsApi.listRuns(datasetId, undefined, signal),
  });

  if (runsQuery.isLoading)
    return (
      <UiAttributes
        messages={{ label: { id: "ui.statistics.loadingHistory" } }}
      >
        <PageLoader label={"Loading history…"} />
      </UiAttributes>
    );
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
      <p className="py-8 text-center text-sm text-muted-foreground">
        {" "}
        <UiMessage id="ui.statistics.noStatisticalRunsYetForThisDataset" />{" "}
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-muted-foreground">
            <th className="px-3 py-2 font-medium">
              <UiMessage id="workflow.parameter.method" />
            </th>
            <th className="px-3 py-2 font-medium">
              <UiMessage id="ui.projects.status" />
            </th>
            <th className="px-3 py-2 text-right font-medium">
              <UiMessage id="ui.report.statistic" />
            </th>
            <th className="px-3 py-2 text-right font-medium">
              <UiMessage id="ui.report.pValue" />
            </th>
            <th className="px-3 py-2 text-right font-medium">
              <UiMessage id="ui.statistics.n" />
            </th>
            <th className="px-3 py-2 font-medium">
              <UiMessage id="ui.statistics.runAt" />
            </th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const meta = STAT_RUN_STATUS_META[run.status] ?? {
              label: run.status,
              tone: "slate" as const,
            };
            const isActive = run.id === activeRunId;
            return (
              <tr
                key={run.id}
                className={
                  isActive ? "border-b bg-primary/5" : "border-b last:border-0"
                }
              >
                <td className="px-3 py-2 font-mono font-medium">
                  {run.method}
                </td>
                <td className="px-3 py-2">
                  <StatusBadge
                    label={localizedMetaLabel(meta, uiLabelT)}
                    tone={meta.tone}
                  />
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {formatStat(run.statistic_value)}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {formatPValue(run.p_value)}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {run.sample_size ?? "—"}
                </td>
                <td className="px-3 py-2 text-muted-foreground">
                  {formatDate(run.created_at)}
                </td>
                <td className="px-3 py-2 text-right">
                  {!isActive && (
                    <button
                      type="button"
                      onClick={() => onViewRun(run.id)}
                      className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium transition-colors hover:bg-muted"
                    >
                      <HistoryIcon className="size-3" />{" "}
                      <UiMessage id="ui.statistics.view" />{" "}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
