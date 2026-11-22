"use client";
import { SavedFailureMessage } from "@/components/friendly-error";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  BarChart3Icon,
  DatabaseIcon,
  HistoryIcon,
  RefreshCwIcon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { DesktopLink } from "@/components/desktop-link";
import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import {
  ResultSourceBoundary,
  useResultProjectId,
  useResultSelection,
} from "@/components/result-source";
import { usePreviewPublication } from "@/components/preview-workspace";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { datasetsApi, statisticsApi } from "@/lib/api-endpoints";
import { STAT_RUN_STATUS_META, friendlyApiError } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { StatisticalRun, StatisticalRunRequest } from "@/lib/types";
import { statisticalColumns } from "@/lib/preview-types";
import { StatisticsConfigForm } from "./statistics-config";
import { StatisticsHistoryTab } from "./statistics-history";
import { StatisticsResultView } from "./statistics-result";
import {
  ComputingPanel,
  EmptyPanel,
  MutationErrorNote,
  type StatRunMutation,
  type StatRunMutationVars,
} from "./statistics-shared";

interface StatisticsClientProps {
  projectId: string;
}

export default function StatisticsClient({ projectId }: StatisticsClientProps) {
  const resolvedProjectId = useResultProjectId(projectId);
  if (!resolvedProjectId || resolvedProjectId === "__desktop_project__")
    return (
      <p role="alert">
        <UiMessage id="ui.statistics.statisticsProjectIdentityIsMissing" />
      </p>
    );
  return (
    <UiAttributes
      messages={{ label: { id: "ui.statistics.loadingStatistics" } }}
    >
      <AuthGuard label={"Loading statistics…"}>
        <ResultSourceBoundary projectId={resolvedProjectId}>
          <ProjectShell projectId={resolvedProjectId}>
            <StatisticsBody projectId={resolvedProjectId} />
          </ProjectShell>
        </ResultSourceBoundary>
      </AuthGuard>
    </UiAttributes>
  );
}

/* ------------------------------------------------------------------ */

const ACTIVE_STATUSES = new Set(["pending", "running"]);

function pollWhileActive(query: {
  state: { data?: StatisticalRun };
}): number | false {
  return ACTIVE_STATUSES.has(query.state.data?.status ?? "") ? 2000 : false;
}

function StatisticsBody({ projectId }: StatisticsClientProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { formatBytes } = useLocaleDisplay();

  const queryClient = useQueryClient();
  const params = useSearchParams();
  const selection = useResultSelection(
    params.get("datasetId"),
    params.get("runId"),
  );
  const selectedDatasetId = selection.datasetId;
  const viewRunId = selection.runId;
  const [lastMethod, setLastMethod] = useState<string | null>(null);
  const [draft, setDraft] = useState<{
    config: StatisticalRunRequest;
    complete: boolean;
  } | null>(null);
  const [proposal, setProposal] = useState<{
    id: number;
    config: StatisticalRunRequest;
  } | null>(null);

  const datasetsQuery = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });

  const methodsQuery = useQuery({
    queryKey: ["statistics", "methods"],
    queryFn: ({ signal }) => statisticsApi.listMethods(signal),
  });

  const previewQuery = useQuery({
    queryKey: [
      "datasets",
      "preview",
      projectId,
      selectedDatasetId,
      selection.queryIdentity,
    ],
    queryFn: ({ signal }) =>
      datasetsApi.preview(selectedDatasetId as string, 5, signal),
    enabled: selectedDatasetId !== null,
  });

  const latestQuery = useQuery({
    queryKey: [
      "statistics",
      "latest",
      projectId,
      selectedDatasetId,
      selection.queryIdentity,
    ],
    queryFn: ({ signal }) =>
      statisticsApi.getLatest(selectedDatasetId as string, null, signal),
    enabled: selectedDatasetId !== null && viewRunId === null,
    retry: false,
    refetchInterval: pollWhileActive,
  });

  const runByIdQuery = useQuery({
    queryKey: [
      "statistics",
      "run",
      projectId,
      selectedDatasetId,
      viewRunId,
      selection.queryIdentity,
    ],
    queryFn: ({ signal }) => statisticsApi.getRun(viewRunId as string, signal),
    enabled: viewRunId !== null,
    retry: false,
    refetchInterval: pollWhileActive,
  });

  const runMutation = useMutation({
    mutationFn: ({ datasetId, request }: StatRunMutationVars) =>
      statisticsApi.run(datasetId, request),
    onSuccess: (run, input) => {
      if (
        !input.isCurrent() ||
        run.project_id !== projectId ||
        run.dataset_id !== input.datasetId
      )
        return;
      void queryClient.invalidateQueries({
        queryKey: ["statistics", "runs", run.dataset_id],
      });
      setLastMethod(run.method);
      selection.select(input.datasetId, null);
      setDraft(null);
      setProposal(null);
    },
  });

  const datasets = datasetsQuery.data ?? [];
  const readyDatasets = datasets.filter((d) => d.upload_status === "ready");
  const selected = readyDatasets.find(
    (d) => d.id === selectedDatasetId && d.project_id === projectId,
  );
  const active = viewRunId ? runByIdQuery.data : latestQuery.data;
  const currentRun =
    selected &&
    active?.project_id === projectId &&
    (!viewRunId || active.id === viewRunId) &&
    active.dataset_id === selected.id &&
    active.dataset_sha256 === selected.sha256
      ? active
      : null;
  const savedColumns = currentRun
    ? statisticalColumns({
        method_id: currentRun.method,
        variables: [],
        ...currentRun.input_config,
      } as StatisticalRunRequest)
    : [];
  const columns = [
    ...new Set([
      ...(draft ? statisticalColumns(draft.config) : []),
      ...savedColumns,
    ]),
  ];
  usePreviewPublication({
    page: "统计分析",
    pageMessage: { id: "workflow.category.statistics" },
    scope:
      selected && columns.length
        ? {
            project_id: projectId,
            dataset_id: selected.id,
            dataset_sha256: selected.sha256,
            columns,
            statistical_config: draft?.complete ? draft.config : null,
            statistical_run_id: currentRun?.id ?? null,
          }
        : { project_id: projectId },
    draft: draft?.complete
      ? "当前有效表单参数可单独审批外发。"
      : "表单未完整；当前只提供项目、已选字段或真实结果上下文。",
    draftMessage: {
      id: draft?.complete
        ? "preview.context.statisticsComplete"
        : "preview.context.statisticsIncomplete",
    },
    applyStatistics: (config) =>
      setProposal((old) => ({ id: (old?.id ?? 0) + 1, config })),
  });

  function selectSource(datasetId: string | null, runId: string | null) {
    selection.select(datasetId, runId);
    runMutation.reset();
    setLastMethod(null);
    setDraft(null);
    setProposal(null);
  }

  function submit(request: StatisticalRunRequest) {
    if (!selected) return;
    runMutation.mutate({
      datasetId: selected.id,
      request,
      isCurrent: selection.ticket(),
    });
  }

  if (datasetsQuery.isLoading || methodsQuery.isLoading) {
    return (
      <UiAttributes messages={{ label: { id: "common.loading" } }}>
        <PageLoader label={"Loading…"} />
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
  if (methodsQuery.isError) {
    const friendly = friendlyApiError(methodsQuery.error, uiCopyT);
    return (
      <ErrorState
        title={friendly.title}
        message={friendly.message}
        onRetry={() => void methodsQuery.refetch()}
      />
    );
  }
  if (datasets.length === 0) {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.data.noDatasetsYet" },
          message: {
            id: "ui.statistics.uploadADatasetFirstThenRunDescriptiveStatisticsCorrelationsTTestsChiSquareAnovaAndMoreEachWithAssumptionChecksAndEffectSizes",
          },
        }}
      >
        <EmptyPanel
          title={"No datasets yet"}
          message={
            "Upload a dataset first, then run descriptive statistics, correlations, t-tests, chi-square, ANOVA and more — each with assumption checks and effect sizes."
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

  const activeRunQuery: UseQueryResult<StatisticalRun, Error> =
    viewRunId !== null
      ? (runByIdQuery as UseQueryResult<StatisticalRun, Error>)
      : (latestQuery as UseQueryResult<StatisticalRun, Error>);

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
                  selectSource(ds.id, null);
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

      {selectedDatasetId === null && (
        <UiAttributes
          messages={{
            title: { id: "ui.ml.selectADataset" },
            message: {
              id: "ui.statistics.chooseADatasetAboveToConfigureAndRunAStatisticalAnalysis",
            },
          }}
        >
          <EmptyPanel
            title={"Select a dataset"}
            message={
              "Choose a dataset above to configure and run a statistical analysis."
            }
            icon={<BarChart3Icon className="size-8 text-primary/40" />}
          />
        </UiAttributes>
      )}

      {selectedDatasetId !== null && !selected && (
        <p role="alert">
          <UiMessage id="ui.ml.theCurrentSourceDatasetIsUnavailableOrBelongsToAnotherProject" />
        </p>
      )}
      {selected && (
        <div className="space-y-6">
          {/* Configuration form */}
          {previewQuery.isLoading ? (
            <UiAttributes messages={{ label: { id: "ui.ml.loadingColumns" } }}>
              <PageLoader label={"Loading columns…"} />
            </UiAttributes>
          ) : previewQuery.isError ? (
            <UiAttributes
              messages={{ title: { id: "ui.ml.couldNotLoadColumns" } }}
            >
              <ErrorState
                title={"Could not load columns"}

                message={friendlyApiError(previewQuery.error, uiCopyT).message}
                onRetry={() => void previewQuery.refetch()}
              />
            </UiAttributes>
          ) : (
            <StatisticsConfigForm
              key={`${selection.queryIdentity}-${methodsQuery.data?.methodset_version}`}
              methods={methodsQuery.data?.methods ?? []}
              columns={previewQuery.data?.columns ?? []}
              isRunning={runMutation.isPending}
              proposal={proposal}
              onConfigChange={(config, complete) =>
                setDraft((old) =>
                  JSON.stringify(old) === JSON.stringify({ config, complete })
                    ? old
                    : { config, complete },
                )
              }
              onSubmit={submit}
            />
          )}

          <MutationErrorNote mutation={runMutation} />

          {/* Result area */}
          {active && !currentRun ? (
            <p role="alert">
              <UiMessage id="ui.statistics.theSavedRunDoesNotMatchTheCurrentSourceIdentityOrVersion" />
            </p>
          ) : (
            <RunResultArea
              runQuery={activeRunQuery}
              runMutation={runMutation}
              datasetId={selected.id}
              isViewingHistory={viewRunId !== null}
              lastMethod={lastMethod}
              onBackToLatest={() => selectSource(selected.id, null)}
              onViewRun={(id) => selectSource(selected.id, id)}
              onRerun={submit}
            />
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

interface RunResultAreaProps {
  runQuery: UseQueryResult<StatisticalRun, Error>;
  runMutation: StatRunMutation;
  datasetId: string;
  isViewingHistory: boolean;
  lastMethod: string | null;
  onBackToLatest: () => void;
  onViewRun: (runId: string) => void;
  onRerun: (request: import("@/lib/types").StatisticalRunRequest) => void;
}

function RunResultArea({
  runQuery,
  runMutation,
  datasetId,
  isViewingHistory,
  lastMethod,
  onBackToLatest,
  onViewRun,
  onRerun,
}: RunResultAreaProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const { formatDate } = useLocaleDisplay();

  if (runMutation.isPending) {
    return (
      <UiAttributes
        messages={{ label: { id: "ui.statistics.computingStatistics" } }}
      >
        <ComputingPanel label={"Computing statistics…"} />
      </UiAttributes>
    );
  }
  if (runQuery.isLoading) {
    return (
      <UiAttributes
        messages={{ label: { id: "ui.statistics.loadingLatestRun" } }}
      >
        <PageLoader label={"Loading latest run…"} />
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

  if (notFound || !run) {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.statistics.noStatisticalRunYet" },
          message: {
            id: "ui.statistics.configureAMethodAboveAndRunYourFirstAnalysisResultsIncludeTheTestStatisticPValueConfidenceIntervalEffectSizeAndAssumptionChecks",
          },
        }}
      >
        <EmptyPanel
          title={"No statistical run yet"}
          message={
            "Configure a method above and run your first analysis. Results include the test statistic, p-value, confidence interval, effect size, and assumption checks."
          }
          icon={<BarChart3Icon className="size-8 text-primary/40" />}
        />
      </UiAttributes>
    );
  }

  if (run.status === "pending" || run.status === "running") {
    return (
      <UiAttributes
        messages={{
          label: { id: "ui.statistics.statisticalAnalysisInProgress" },
        }}
      >
        <ComputingPanel label={"Statistical analysis in progress…"} />
      </UiAttributes>
    );
  }

  if (run.status === "failed") {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.statistics.analysisFailed" },
          retryLabel: { id: "common.retry" },
        }}
      >
        <ErrorState
          title={"Analysis failed"}

          message={<SavedFailureMessage code={run.error_code} />}
          diagnostic={run.error_message}
          retryLabel="Retry"
          onRetry={() =>
            run.input_config &&
            onRerun(
              run.input_config as unknown as import("@/lib/types").StatisticalRunRequest,
            )
          }
        />
      </UiAttributes>
    );
  }

  const statusMeta = STAT_RUN_STATUS_META[run.status] ?? {
    label: run.status,
    tone: "slate" as const,
  };

  return (
    <div className="animate-rise space-y-4">
      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <StatusBadge
          label={localizedMetaLabel(statusMeta, uiLabelT)}
          tone={statusMeta.tone}
          pulse={false}
        />
        <span className="font-mono">{run.method}</span>
        <span>
          <UiMessage id="ui.quality.ruleset" /> {run.methodset_version}
        </span>
        <span>{formatDate(run.completed_at ?? run.created_at)}</span>
        <span>
          <UiMessage id="ui.statistics.policy" /> {run.missing_value_policy}
        </span>
        {lastMethod && !isViewingHistory && (
          <span className="text-teal-700">
            <UiMessage id="ui.statistics.justRan" /> {lastMethod}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          {isViewingHistory && (
            <Button size="sm" variant="secondary" onClick={onBackToLatest}>
              <HistoryIcon className="size-3.5" />{" "}
              <UiMessage id="ui.quality.backToLatest" />{" "}
            </Button>
          )}
          {run.input_config && (
            <Button
              size="sm"
              variant="outline"
              disabled={runMutation.isPending}
              onClick={() =>
                onRerun(
                  run.input_config as unknown as import("@/lib/types").StatisticalRunRequest,
                )
              }
            >
              <RefreshCwIcon className="size-3.5" />{" "}
              <UiMessage id="ui.statistics.reRun" />{" "}
            </Button>
          )}
        </div>
      </div>

      {isViewingHistory && (
        <div className="flex items-center gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <HistoryIcon className="size-4 shrink-0" />{" "}
          <UiMessage id="ui.quality.viewingAHistoricalRunFrom" />{" "}
          {formatDate(run.created_at)}.
        </div>
      )}

      {run.status === "not_applicable" && (
        <div className="flex items-center gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <BarChart3Icon className="size-4 shrink-0" />
          {run.result?.warnings?.[0] ?? (
            <UiMessage id="ui.common.thisMethodIsNotApplicableToTheSelectedVariables" />
          )}
        </div>
      )}

      <Tabs defaultValue="result">
        <TabsList>
          <TabsTrigger value="result">
            <UiMessage id="ui.statistics.result" />
          </TabsTrigger>
          <TabsTrigger value="history">
            <UiMessage id="ui.quality.history" />
          </TabsTrigger>
        </TabsList>

        <TabsContent value="result" className="mt-5">
          {run.result ? (
            <StatisticsResultView
              result={run.result}
              inputConfig={run.input_config}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              {" "}
              <UiMessage id="ui.statistics.noResultRecordedForThisRun" />{" "}
            </p>
          )}
        </TabsContent>

        <TabsContent value="history" className="mt-5">
          <StatisticsHistoryTab
            datasetId={datasetId}
            activeRunId={run.id}
            onViewRun={onViewRun}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
