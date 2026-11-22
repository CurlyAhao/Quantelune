"use client";
import { analysisJobApi } from "@/lib/analysis-jobs";
import { SavedFailureMessage } from "@/components/friendly-error";

import { FriendlyErrorMessage as UiError } from "@/components/friendly-error";
import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { DatabaseIcon, GaugeIcon } from "lucide-react";

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
import { mlColumns } from "@/lib/preview-types";
import { Card, CardContent } from "@/components/ui/card";
import { datasetsApi, mlApi } from "@/lib/api-endpoints";
import { friendlyApiError } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  MLExperiment,
  MLExperimentCreateRequest,
  MLPreflightStats,
  MLRun,
} from "@/lib/types";
import { MLDraftEditor } from "./ml-draft-editor";
import { flushProjectEditors } from "@/lib/project-save";
import { MLResultsView } from "./ml-results";
import { MLSavedRuns } from "./ml-saved-runs";

interface MachineLearningClientProps {
  projectId: string;
}

export default function MachineLearningClient({
  projectId,
}: MachineLearningClientProps) {
  const resolvedProjectId = useResultProjectId(projectId);
  if (!resolvedProjectId || resolvedProjectId === "__desktop_project__")
    return (
      <p role="alert">
        <UiMessage id="ui.ml.mlProjectIdentityIsMissing" />
      </p>
    );
  return (
    <UiAttributes messages={{ label: { id: "ui.ml.loadingMachineLearning" } }}>
      <AuthGuard label={"Loading machine learning…"}>
        <ResultSourceBoundary projectId={resolvedProjectId}>
          <ProjectShell projectId={resolvedProjectId}>
            <MLBody projectId={resolvedProjectId} />
          </ProjectShell>
        </ResultSourceBoundary>
      </AuthGuard>
    </UiAttributes>
  );
}

function MLBody({ projectId }: MachineLearningClientProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { formatBytes } = useLocaleDisplay();

  const params = useSearchParams();
  const selection = useResultSelection(
    params.get("datasetId"),
    params.get("runId"),
  );
  const selectedDatasetId = selection.datasetId;
  const linkedRunId = selection.runId;
  const [run, setRun] = useState<MLRun | null>(null);
  const [runExperiment, setRunExperiment] = useState<MLExperiment | null>(null);
  const [draft, setDraft] = useState<{
    config: MLExperimentCreateRequest;
    complete: boolean;
  } | null>(null);
  const [proposal, setProposal] = useState<{
    id: number;
    config: MLExperimentCreateRequest;
  } | null>(null);
  const [mutationError, setMutationError] = useState<React.ReactNode>(null);
  const flight = useRef<AbortController | null>(null);
  const currentJob = useRef<string | null>(null);
  const cancelRequested = useRef(false);
  useEffect(() => () => flight.current?.abort(), []);
  const [preflightQuery, setPreflightQuery] = useState<{
    target: string;
    taskType: string;
    splitStrategy: string;
    groupVariable: string | null;
    timeVariable: string | null;
    nSplits: number;
    testSize: number;
    randomSeed: number;
  } | null>(null);

  const datasetsQuery = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });
  const metadataQuery = useQuery({
    queryKey: ["ml", "metadata"],
    queryFn: ({ signal }) => mlApi.metadata(signal),
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

  // Trusted full-data stats for folds suggestion — never from the 5-row preview.
  // Query parameters mirror the real experiment config so suggestions cannot
  // drift from Group/Time / task / split choices.
  const preflightStatsQuery = useQuery({
    queryKey: [
      "ml",
      "preflight-stats",
      projectId,
      selectedDatasetId,
      preflightQuery,
      selection.queryIdentity,
    ],
    queryFn: ({ signal }) =>
      mlApi.preflightStats(
        {
          datasetId: selectedDatasetId as string,
          target: preflightQuery!.target,
          taskType: preflightQuery!.taskType,
          splitStrategy: preflightQuery!.splitStrategy,
          groupVariable: preflightQuery!.groupVariable,
          timeVariable: preflightQuery!.timeVariable,
          nSplits: preflightQuery!.nSplits,
          testSize: preflightQuery!.testSize,
          randomSeed: preflightQuery!.randomSeed,
        },
        signal,
      ),
    enabled:
      selectedDatasetId !== null &&
      preflightQuery !== null &&
      preflightQuery.target !== "",
    retry: false,
  });
  // Pass through untrusted stats so the UI can show the concrete reason;
  // only keep the payload when a real response arrived for the current query.
  const preflightStats: MLPreflightStats | null =
    preflightQuery !== null && preflightStatsQuery.data
      ? preflightStatsQuery.data
      : null;

  const runMutation = useMutation({
    mutationFn: async ({
      datasetId,
      request,
    }: {
      datasetId: string;
      request: MLExperimentCreateRequest;
      isCurrent: () => boolean;
    }) => {
      const controller = new AbortController();
      flight.current = controller;
      currentJob.current = null;
      cancelRequested.current = false;
      const experiment = await mlApi.createExperiment(
        datasetId,
        request,
        controller.signal,
      );
      return {
        run: await mlApi.runExperiment(
          experiment.id,
          controller.signal,
          async (job) => {
            currentJob.current = job.id;
            if (cancelRequested.current) await analysisJobApi.cancel(job.id);
          },
        ),
        experiment,
        controller,
      };
    },
    onSuccess: (result, input) => {
      if (
        result.controller.signal.aborted ||
        !input.isCurrent() ||
        input.datasetId !== selectedDatasetId ||
        result.experiment.project_id !== projectId
      )
        return;
      selection.select(input.datasetId, null);
      setDraft(null);
      setProposal(null);
      setPreflightQuery(null);
      setRun(result.run);
      setRunExperiment(result.experiment);
      setMutationError(null);
    },
    onError: (err, input) => {
      if (!input.isCurrent()) return;
      if (flight.current?.signal.aborted) {
        setMutationError(
          <UiMessage id="ui.ml.cancellationRequestedCheckHistoryForThisRunSFinalStatus" />,
        );
        return;
      }
      setMutationError(<UiError error={err} part="message" />);
    },
  });

  const datasets = datasetsQuery.data ?? [];
  const readyDatasets = datasets.filter((d) => d.upload_status === "ready");
  const selectedDataset =
    readyDatasets.find(
      (d) => d.id === selectedDatasetId && d.project_id === projectId,
    ) ?? null;
  const sourceJobId = params.get("sourceJob");
  const sourceDraftId = params.get("draftId");
  useEffect(() => { if (sourceDraftId) setProposal(null); }, [sourceDraftId]);
  const restoredJob = useRef("");
  const sourceJob = useQuery({
    queryKey: [
      "ml-source-job",
      projectId,
      sourceJobId,
      selectedDataset?.id,
      selectedDataset?.sha256,
    ],
    enabled: !!sourceJobId && !!selectedDataset && !sourceDraftId,
    queryFn: async ({ signal }) => {
      const record = await analysisJobApi.read(sourceJobId!, signal);
      if (
        record.id !== sourceJobId ||
        record.project_id !== projectId ||
        record.payload.operation !== "ml" ||
        record.payload.dataset_id !== selectedDataset!.id ||
        record.payload.dataset_sha256 !== selectedDataset!.sha256
      )
        throw new Error("Source mismatch");
      return record;
    },
    retry: false,
  });
  useEffect(() => {
    if (sourceDraftId || !sourceJob.data || restoredJob.current === sourceJob.data.id) return;
    restoredJob.current = sourceJob.data.id;
    setProposal((old) => ({
      id: (old?.id ?? 0) + 1,
      config: sourceJob.data.payload
        .configuration as unknown as MLExperimentCreateRequest,
    }));
  }, [sourceJob.data, sourceDraftId]);
  const linked = useQuery({
    queryKey: [
      "ml",
      "linked-run",
      projectId,
      selectedDatasetId,
      linkedRunId,
      selection.queryIdentity,
    ],
    enabled: !!linkedRunId && !!selectedDataset,
    queryFn: async ({ signal }) => {
      const saved = await mlApi.getRun(linkedRunId!, signal);
      const experiment = await mlApi.getExperiment(saved.experiment_id, signal);
      if (
        saved.id !== linkedRunId ||
        experiment.id !== saved.experiment_id ||
        experiment.project_id !== projectId ||
        experiment.dataset_id !== selectedDatasetId ||
        experiment.dataset_sha256 !== selectedDataset?.sha256
      )
        throw new Error("Source mismatch");
      return { run: saved, experiment };
    },
    retry: false,
  });
  const experiment = linkedRunId ? linked.data?.experiment : runExperiment;
  const candidateRun = linkedRunId ? linked.data?.run : run;
  const currentRun =
    selectedDataset &&
    experiment?.project_id === projectId &&
    experiment.dataset_id === selectedDataset.id &&
    experiment.dataset_sha256 === selectedDataset.sha256 &&
    candidateRun?.experiment_id === experiment.id &&
    (!linkedRunId || candidateRun.id === linkedRunId)
      ? (candidateRun ?? null)
      : null;
  const savedColumns =
    experiment && currentRun
      ? [
          experiment.target_variable,
          ...experiment.feature_variables,
          ...experiment.categorical_variables,
          ...(typeof experiment.config?.group_variable === "string"
            ? [experiment.config.group_variable]
            : []),
          ...(typeof experiment.config?.time_variable === "string"
            ? [experiment.config.time_variable]
            : []),
        ]
      : [];
  const columns = [
    ...new Set([
      ...(draft ? mlColumns(draft.config).filter(Boolean) : []),
      ...savedColumns,
    ]),
  ];
  usePreviewPublication({
    page: "机器学习",
    pageMessage: { id: "workflow.category.ml" },
    scope:
      selectedDataset?.project_id === projectId && columns.length
        ? {
            project_id: projectId,
            dataset_id: selectedDataset.id,
            dataset_sha256: selectedDataset.sha256,
            columns,
            ml_config: draft?.complete ? draft.config : null,
            ml_run_id: currentRun?.id ?? null,
          }
        : { project_id: projectId },
    draft: draft?.complete
      ? "当前有效实验配置可单独审批外发；真实 full-data preflight仍是执行前门槛。"
      : "实验配置未完整；先选目标、特征与模型，当前来源引用保持独立。",
    draftMessage: {
      id: draft?.complete
        ? "preview.context.mlComplete"
        : "preview.context.mlIncomplete",
    },
    applyML: (config) =>
      setProposal((old) => ({ id: (old?.id ?? 0) + 1, config })),
  });

  async function selectSource(datasetId: string | null, runId: string | null) {
    if (datasetId === selectedDatasetId && runId === null && params.get("draftId")) return;
    try { await flushProjectEditors(projectId); }
    catch (error) { setMutationError(<UiError error={error} part="message"/>); return; }
    flight.current?.abort();
    selection.select(datasetId, runId);
    runMutation.reset();
    setRun(null);
    setRunExperiment(null);
    setMutationError(null);
    setDraft(null);
    setProposal(null);
    setPreflightQuery(null);
    const url = new URL(window.location.href);
    for (const name of ["draftId", "sourceJob", "runId", "datasetId"]) url.searchParams.delete(name);
    if (datasetId) url.searchParams.set("datasetId", datasetId);
    if (runId) url.searchParams.set("runId", runId);
    window.history.replaceState(null, "", url.href);
  }

  if (datasetsQuery.isLoading || metadataQuery.isLoading) {
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
  if (datasets.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <DatabaseIcon className="size-8 text-primary/40" />
          <h3 className="font-heading text-lg font-bold">
            <UiMessage id="ui.data.noDatasetsYet" />
          </h3>
          <p className="max-w-md text-sm text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.uploadADatasetFirstThenBuildAReproducibleMlExperimentWithGuardedSplitsLeakageChecksAndMultiMetricComparison" />{" "}
          </p>
          <DesktopLink
            href={`/projects/${projectId}/data`}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-2.5 text-sm font-medium text-primary-foreground transition-all hover:bg-primary/80"
          >
            <DatabaseIcon className="size-4" />{" "}
            <UiMessage id="ui.ml.goToData" />{" "}
          </DesktopLink>
        </CardContent>
      </Card>
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
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <GaugeIcon className="size-8 text-primary/40" />
            <h3 className="font-heading text-lg font-bold">
              <UiMessage id="ui.ml.selectADataset" />
            </h3>
            <p className="max-w-md text-sm text-muted-foreground">
              {" "}
              <UiMessage id="ui.ml.chooseADatasetAboveToConfigureAndRunAMachineLearningExperiment" />{" "}
            </p>
          </CardContent>
        </Card>
      )}

      {selectedDatasetId !== null && !selectedDataset && (
        <p role="alert">
          <UiMessage id="ui.ml.theCurrentSourceDatasetIsUnavailableOrBelongsToAnotherProject" />
        </p>
      )}
      {selectedDataset && (
        <div className="space-y-6">
          <MLSavedRuns
            key={`saved:${selection.queryIdentity}`}
            projectId={projectId}
            datasetId={selectedDataset.id}
            sha256={selectedDataset?.sha256 ?? null}
            onSelect={(id) => {
              selectSource(selectedDataset.id, id);
            }}
          />
          {linked.isError && (
            <p role="alert">
              <UiMessage id="ui.ml.theSavedRunDoesNotMatchTheCurrentSourceVersionOrPermissions" />
            </p>
          )}
          {sourceJob.isError && (
            <p role="alert">
              <UiMessage id="ui.ml.theSavedRunDoesNotMatchTheCurrentSourceVersionOrPermissions" />
            </p>
          )}
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
            <MLDraftEditor
              key={`builder:${selection.queryIdentity}:${params.get("draftId") ?? "new"}`}
              projectId={projectId}
              datasetId={selectedDataset.id}
              initialId={params.get("draftId")}
              fresh={!!sourceJobId && !sourceDraftId}
              columns={previewQuery.data?.columns ?? []}
              metadata={
                metadataQuery.data ?? {
                  tasks: [],
                  split_strategies: [],
                  models: [],
                }
              }
              isRunning={runMutation.isPending}
              onCancel={() => {
                cancelRequested.current = true;
                if (currentJob.current)
                  void analysisJobApi
                    .cancel(currentJob.current)
                    .catch(() => undefined);
                flight.current?.abort();
                setMutationError(
                  <UiMessage id="ui.ml.cancellationRequestedCheckHistoryForThisRunSFinalStatus" />,
                );
              }}
              proposal={proposal}
              onConfigChange={(config, complete) =>
                setDraft((old) =>
                  JSON.stringify(old) === JSON.stringify({ config, complete })
                    ? old
                    : { config, complete },
                )
              }
              onSubmit={(request) =>
                runMutation.mutate({
                  datasetId: selectedDataset.id,
                  request,
                  isCurrent: selection.ticket(),
                })
              }
              datasetSha256={selectedDataset?.sha256 ?? null}
              preflightStats={preflightStats}
              onPreflightQueryChange={setPreflightQuery}
            />
          )}

          {mutationError && (
            <div
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
            >
              {mutationError}
            </div>
          )}

          {runMutation.isPending && (
            <div
              role="status"
              className="flex flex-col items-center gap-3 rounded-lg border border-dashed bg-card/60 py-16"
            >
              <UiAttributes
                messages={{
                  label: { id: "ui.ml.trainingAndEvaluatingModels" },
                }}
              >
                <PageLoader label={"Training and evaluating models…"} />
              </UiAttributes>
            </div>
          )}

          {currentRun && !runMutation.isPending && (
            <div className="animate-rise">
              {currentRun.status === "failed" && (
                <UiAttributes messages={{ title: { id: "ui.ml.mlRunFailed" } }}>
                  <ErrorState
                    title={"ML run failed"}

                    message={
                      <SavedFailureMessage code={currentRun.error_code} />
                    }
                    diagnostic={currentRun.error_message}
                  />
                </UiAttributes>
              )}
              <MLResultsView
                key={currentRun.id}
                run={currentRun}
                experiment={experiment ?? null}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
