"use client";
import { SavedFailureMessage } from "@/components/friendly-error";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DatabaseIcon,
  HistoryIcon,
  InfoIcon,
  Loader2Icon,
  PlayIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { DesktopLink } from "@/components/desktop-link";
import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { QualityFindingsTab } from "./quality-findings";
import { QualityHistoryTab } from "./quality-history";
import { QualityOverviewTab } from "./quality-overview";
import { QualityThresholds } from "./quality-thresholds";
import {
  MutationErrorNote,
  type QualityRunMutation,
  type RunMutationVars,
} from "./quality-shared";
import { datasetsApi, qualityApi } from "@/lib/api-endpoints";
import { QUALITY_RUN_STATUS_META, friendlyApiError } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { QualityRun } from "@/lib/types";

interface QualityClientProps {
  projectId: string;
}

export default function QualityClient({ projectId }: QualityClientProps) {
  return (
    <UiAttributes messages={{ label: { id: "ui.quality.loadingQuality" } }}>
      <AuthGuard label={"Loading quality…"}>
        <ProjectShell projectId={projectId}>
          <QualityBody projectId={projectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

/* ------------------------------------------------------------------ */

const ACTIVE_RUN_STATUSES = new Set(["pending", "running"]);

function pollWhileActive(query: {
  state: { data?: QualityRun };
}): number | false {
  return ACTIVE_RUN_STATUSES.has(query.state.data?.status ?? "") ? 3000 : false;
}

function QualityBody({ projectId }: QualityClientProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { formatBytes } = useLocaleDisplay();

  const queryClient = useQueryClient();
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(
    null,
  );
  const [viewRunId, setViewRunId] = useState<string | null>(null);

  const datasetsQuery = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });

  // Latest run for the selected dataset (hidden while viewing history).
  const latestQuery = useQuery({
    queryKey: ["quality", "latest", selectedDatasetId],
    queryFn: ({ signal }) =>
      qualityApi.getLatest(selectedDatasetId as string, signal),
    enabled: selectedDatasetId !== null && viewRunId === null,
    retry: false,
    refetchInterval: pollWhileActive,
  });

  // A specific historical run chosen from the History tab.
  const runByIdQuery = useQuery({
    queryKey: ["quality", "run", viewRunId],
    queryFn: ({ signal }) => qualityApi.getRun(viewRunId as string, signal),
    enabled: viewRunId !== null,
    retry: false,
    refetchInterval: pollWhileActive,
  });

  const handleRunSuccess = (run: QualityRun) => {
    queryClient.setQueryData(["quality", "latest", run.dataset_id], run);
    queryClient.setQueryData(["quality", "run", run.id], run);
    void queryClient.invalidateQueries({
      queryKey: ["quality", "runs", run.dataset_id],
    });
    setViewRunId(null);
  };

  const runMutation = useMutation({
    mutationFn: ({ datasetId, request }: RunMutationVars) =>
      qualityApi.run(datasetId, request),
    onSuccess: handleRunSuccess,
  });

  const recomputeMutation = useMutation({
    mutationFn: ({ datasetId, request }: RunMutationVars) =>
      qualityApi.recompute(datasetId, request),
    onSuccess: handleRunSuccess,
  });

  const datasets = datasetsQuery.data ?? [];
  const readyDatasets = datasets.filter((d) => d.upload_status === "ready");

  if (datasetsQuery.isLoading) {
    return (
      <UiAttributes messages={{ label: { id: "ui.data.loadingDatasets" } }}>
        <PageLoader label={"Loading datasets…"} />
      </UiAttributes>
    );
  }

  if (datasetsQuery.isError) {
    const friendly = friendlyApiError(datasetsQuery.error, uiCopyT);
    return (
      <ErrorState
        title={friendly.title}
        message={friendly.message}
        onRetry={() => void datasetsQuery.refetch()}
      />
    );
  }

  if (datasets.length === 0) {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.data.noDatasetsYet" },
          message: {
            id: "ui.quality.uploadADatasetFirstThenRunAQualityCheckToScoreItsCompletenessValidityUniquenessAndMore",
          },
        }}
      >
        <EmptyPanel
          title={"No datasets yet"}
          message={
            "Upload a dataset first, then run a quality check to score its completeness, validity, uniqueness, and more."
          }
          icon={<DatabaseIcon className="size-8 text-primary/40" />}

          action={
            <DesktopLink
              href={`/projects/${projectId}/data`}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-2.5 text-sm font-medium text-primary-foreground transition-all hover:bg-primary/80"
            >
              <DatabaseIcon className="size-4" />{" "}
              <UiMessage id="ui.ml.goToData" />{" "}
            </DesktopLink>
          }
        />
      </UiAttributes>
    );
  }

  return (
    <div className="space-y-6">
      {/* Dataset selector */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm font-medium text-muted-foreground">
          {" "}
          <UiMessage id="workflow.node.dataset.label" />{" "}
        </label>
        <div className="flex flex-wrap gap-2">
          {readyDatasets.map((ds) => {
            const isActive = selectedDatasetId === ds.id;
            return (
              <button
                key={ds.id}
                type="button"
                onClick={() => {
                  setSelectedDatasetId(ds.id);
                  setViewRunId(null);
                }}
                aria-pressed={isActive}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                  isActive
                    ? "border-primary bg-primary/8 font-medium text-primary"
                    : "bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <DatabaseIcon className="size-3.5" />
                <span className="max-w-48 truncate">
                  {ds.original_filename}
                </span>
                <span className="font-mono text-[0.62rem] text-muted-foreground">
                  {formatBytes(ds.file_size)}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Nothing selected */}
      {selectedDatasetId === null && (
        <UiAttributes
          messages={{
            title: { id: "ui.ml.selectADataset" },
            message: {
              id: "ui.quality.chooseADatasetAboveToReviewItsLatestQualityRunOrStartANewOne",
            },
          }}
        >
          <EmptyPanel
            title={"Select a dataset"}
            message={
              "Choose a dataset above to review its latest quality run, or start a new one."
            }
            icon={<ShieldCheckIcon className="size-8 text-primary/40" />}
          />
        </UiAttributes>
      )}

      {/* Run area */}
      {selectedDatasetId !== null && (
        <RunArea
          key={selectedDatasetId}
          runQuery={
            viewRunId !== null
              ? (runByIdQuery as UseQueryResult<QualityRun, Error>)
              : (latestQuery as UseQueryResult<QualityRun, Error>)
          }
          runMutation={runMutation}
          recomputeMutation={recomputeMutation}
          datasetId={selectedDatasetId}
          isViewingHistory={viewRunId !== null}
          onBackToLatest={() => setViewRunId(null)}
          onViewRun={(runId) => setViewRunId(runId)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

import type { UseQueryResult } from "@tanstack/react-query";

interface RunAreaProps {
  runQuery: UseQueryResult<QualityRun, Error>;
  runMutation: QualityRunMutation;
  recomputeMutation: QualityRunMutation;
  datasetId: string;
  isViewingHistory: boolean;
  onBackToLatest: () => void;
  onViewRun: (runId: string) => void;
}

function RunArea({
  runQuery,
  runMutation,
  recomputeMutation,
  datasetId,
  isViewingHistory,
  onBackToLatest,
  onViewRun,
}: RunAreaProps) {
  const { t: uiCopyT } = useCopyLanguage();

  // A run was just kicked off from this page — show progress immediately.
  if (runMutation.isPending || recomputeMutation.isPending) {
    return (
      <UiAttributes
        messages={{ label: { id: "ui.quality.runningQualityChecks" } }}
      >
        <ComputingPanel label={"Running quality checks…"} />
      </UiAttributes>
    );
  }

  if (runQuery.isLoading) {
    return (
      <UiAttributes
        messages={{ label: { id: "ui.quality.loadingQualityRun" } }}
      >
        <PageLoader label={"Loading quality run…"} />
      </UiAttributes>
    );
  }

  const notFound =
    runQuery.isError && (runQuery.error as { status?: number })?.status === 404;

  if (runQuery.isError && !notFound) {
    const friendly = friendlyApiError(runQuery.error, uiCopyT);
    return (
      <ErrorState
        title={friendly.title}
        message={friendly.message}
        onRetry={() => void runQuery.refetch()}
      />
    );
  }

  const run = runQuery.data;

  // Never run against this dataset yet.
  if (notFound || !run) {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.quality.noQualityCheckYet" },
          message: {
            id: "ui.quality.runTheDeterministicRuleEngineToScoreCompletenessValidityUniquenessConsistencyTemporalBehaviorAndUsabilityWithAFullFindingTrail",
          },
        }}
      >
        <EmptyPanel
          title={"No quality check yet"}
          message={
            "Run the deterministic rule engine to score completeness, validity, uniqueness, consistency, temporal behavior, and usability — with a full finding trail."
          }
          icon={<ShieldCheckIcon className="size-8 text-primary/40" />}

          action={
            <div className="flex flex-col items-center gap-2">
              <Button
                onClick={() => runMutation.mutate({ datasetId, request: {} })}
              >
                <PlayIcon className="size-4" />{" "}
                <UiMessage id="ui.quality.runQualityCheck" />{" "}
              </Button>
              <MutationErrorNote mutation={runMutation} />
            </div>
          }
        />
      </UiAttributes>
    );
  }

  // Hard failure of the engine itself.
  if (run.status === "failed") {
    return (
      <div className="space-y-3">
        <UiAttributes
          messages={{
            title: { id: "ui.quality.qualityRunFailed" },
            retryLabel: { id: "ui.common.retryRun" },
          }}
        >
          <ErrorState
            title={"Quality run failed"}

            message={<SavedFailureMessage code={run.error_code} />}
            diagnostic={run.error_message}
            retryLabel="Retry run"
            onRetry={() =>
              recomputeMutation.mutate({
                datasetId,
                request: {
                  thresholds: run.threshold_config ?? undefined,
                  enable_ohlcv: run.ohlcv_enabled,
                },
              })
            }
          />
        </UiAttributes>
        <MutationErrorNote mutation={recomputeMutation} />
      </div>
    );
  }

  // In flight (started elsewhere, or still settling).
  if (run.status === "pending" || run.status === "running") {
    return (
      <UiAttributes
        messages={{ label: { id: "ui.quality.qualityChecksInProgress" } }}
      >
        <ComputingPanel label={"Quality checks in progress…"} />
      </UiAttributes>
    );
  }

  return (
    <QualityReport
      run={run}
      datasetId={datasetId}
      isViewingHistory={isViewingHistory}
      onBackToLatest={onBackToLatest}
      onViewRun={onViewRun}
      runMutation={runMutation}
      recomputeMutation={recomputeMutation}
    />
  );
}

/* ------------------------------------------------------------------ */

interface QualityReportProps {
  run: QualityRun;
  datasetId: string;
  isViewingHistory: boolean;
  onBackToLatest: () => void;
  onViewRun: (runId: string) => void;
  runMutation: QualityRunMutation;
  recomputeMutation: QualityRunMutation;
}

function QualityReport({
  run,
  datasetId,
  isViewingHistory,
  onBackToLatest,
  onViewRun,
  runMutation,
  recomputeMutation,
}: QualityReportProps) {
  const { t: uiLabelT } = useDomainLanguage();

  const { formatDate, formatNumber } = useLocaleDisplay();

  const statusMeta = QUALITY_RUN_STATUS_META[run.status] ?? {
    label: run.status,
    tone: "slate" as const,
  };
  const isComputing = runMutation.isPending || recomputeMutation.isPending;
  const findingsCount = run.findings?.length ?? run.total_rules;

  return (
    <div className="animate-rise space-y-4">
      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <StatusBadge
          label={localizedMetaLabel(statusMeta, uiLabelT)}
          tone={statusMeta.tone}
          pulse={run.status === "running"}
        />
        <span>
          <UiMessage id="ui.quality.ruleset" /> {run.ruleset_version}
        </span>
        <span>
          <UiMessage id="ui.quality.run" />{" "}
          {formatDate(run.completed_at ?? run.created_at)}
        </span>
        <span>
          {run.total_rules} <UiMessage id="ui.quality.rules" />
        </span>
        {run.ohlcv_enabled && (
          <UiAttributes
            messages={{ label: { id: "ui.residual.quality.ohlcv" } }}
          >
            <StatusBadge label="ohlcv" tone="blue" dot={false} />
          </UiAttributes>
        )}
        {run.sampled && (
          <UiAttributes messages={{ label: { id: "ui.quality.sampled" } }}>
            <StatusBadge label={"sampled"} tone="slate" dot={false} />
          </UiAttributes>
        )}
        <div className="ml-auto flex gap-2">
          {isViewingHistory && (
            <Button size="sm" variant="secondary" onClick={onBackToLatest}>
              <HistoryIcon className="size-3.5" />{" "}
              <UiMessage id="ui.quality.backToLatest" />{" "}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={isComputing}
            onClick={() =>
              recomputeMutation.mutate({
                datasetId,
                request: {
                  thresholds: run.threshold_config ?? undefined,
                  enable_ohlcv: run.ohlcv_enabled,
                },
              })
            }
          >
            {isComputing ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}{" "}
            <UiMessage id="ui.profiling.recompute" />{" "}
          </Button>
        </div>
      </div>

      {isViewingHistory && (
        <div className="flex items-center gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <HistoryIcon className="size-4 shrink-0" />{" "}
          <UiMessage id="ui.quality.viewingAHistoricalRunFrom" />{" "}
          {formatDate(run.created_at)}
          <UiMessage id="ui.quality.scoresAndFindingsReflectTheDataAndThresholdsAtThatTime" />{" "}
        </div>
      )}

      {run.status === "completed_with_errors" && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900"
        >
          <ShieldAlertIcon className="size-4 shrink-0" />
          <UiMessage
            id="quality.rulesUnavailable"
            values={{ count: formatNumber(run.error_count) }}
          />
        </div>
      )}

      {run.sampled && (
        <div className="flex items-center gap-2 rounded-md border border-sky-300/60 bg-sky-50 px-3 py-2 text-xs text-sky-900">
          <InfoIcon className="size-4 shrink-0" />{" "}
          <UiMessage id="ui.quality.thisDatasetWasSampledAffectedCountsAndRatiosAreEstimates" />{" "}
        </div>
      )}

      <MutationErrorNote mutation={recomputeMutation} />

      {/* Threshold configuration */}
      <QualityThresholds
        key={run.id}
        run={run}
        datasetId={datasetId}
        runMutation={runMutation}
      />

      {/* Report tabs */}
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">
            <UiMessage id="ui.profiling.overview" />
          </TabsTrigger>
          <TabsTrigger value="findings">
            {" "}
            <UiMessage id="ui.quality.findings" />{" "}
            <span className="ml-1.5 font-mono text-[0.62rem] opacity-60">
              {findingsCount}
            </span>
          </TabsTrigger>
          <TabsTrigger value="history">
            <UiMessage id="ui.quality.history" />
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-5">
          <QualityOverviewTab run={run} />
        </TabsContent>

        <TabsContent value="findings" className="mt-5">
          <QualityFindingsTab run={run} />
        </TabsContent>

        <TabsContent value="history" className="mt-5">
          <QualityHistoryTab
            datasetId={datasetId}
            activeRunId={run.id}
            onViewRun={onViewRun}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function ComputingPanel({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center gap-3 rounded-lg border border-dashed bg-card/60 py-16"
    >
      <Loader2Icon className="size-8 animate-spin text-primary" />
      <p className="text-sm text-muted-foreground">{label}</p>
    </div>
  );
}

function EmptyPanel({
  icon,
  title,
  message,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  message: string;
  action?: React.ReactNode;
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
        {icon}
        <h3 className="font-heading text-lg font-bold">{title}</h3>
        <p className="max-w-md text-sm text-muted-foreground">{message}</p>
        {action}
      </CardContent>
    </Card>
  );
}
