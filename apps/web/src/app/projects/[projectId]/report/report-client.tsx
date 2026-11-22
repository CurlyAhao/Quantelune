"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import { AuthGuard } from "@/components/auth-guard";
import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import { SavedRunExport } from "@/components/saved-run-export";
import {
  ResultSourceBoundary,
  useResultProjectId,
  useResultSelection,
} from "@/components/result-source";
import { usePreviewPublication } from "@/components/preview-workspace";
import { statisticalColumns } from "@/lib/preview-types";
import type { StatisticalRunRequest } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";
import { datasetsApi, statisticsApi } from "@/lib/api-endpoints";
import { friendlyApiError, STAT_RUN_STATUS_META } from "@/lib/format";
import type {
  Dataset,
  StatisticalRun,
  StatisticalRunSummary,
  StatisticalRunsResponse,
} from "@/lib/types";

import { isRecord, reportMismatch } from "./report-projection";
import { SavedReport } from "./report-view";

const PAGE_SIZE = 10;

function validDatasets(value: unknown): value is Dataset[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        isRecord(item) &&
        typeof item.id === "string" &&
        typeof item.project_id === "string" &&
        typeof item.sha256 === "string" &&
        typeof item.original_filename === "string" &&
        typeof item.upload_status === "string",
    )
  );
}

function validHistory(
  value: StatisticalRunsResponse | undefined,
): value is StatisticalRunsResponse {
  return Boolean(
    value &&
    Array.isArray(value.runs) &&
    Number.isInteger(value.total) &&
    value.total >= 0 &&
    value.runs.every(
      (run) =>
        run &&
        typeof run.id === "string" &&
        typeof run.dataset_id === "string" &&
        typeof run.method === "string" &&
        typeof run.status === "string",
    ),
  );
}

function QueryError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const { t: uiCopyT } = useCopyLanguage();

  const friendly = friendlyApiError(error, uiCopyT);
  return (
    <ErrorState
      title={friendly.title}
      message={friendly.message}
      onRetry={onRetry}
    />
  );
}

function RunHistory({
  runs,
  total,
  page,
  selectedRunId,
  onSelect,
  onPage,
}: {
  runs: StatisticalRunSummary[];
  total: number;
  page: number;
  selectedRunId: string;
  onSelect: (id: string) => void;
  onPage: (page: number) => void;
}) {
  const { t: uiLabelT } = useDomainLanguage();

  const { formatDate } = useLocaleDisplay();

  return (
    <div className="space-y-3">
      {runs.length === 0 ? (
        <p role="status">
          <UiMessage id="ui.report.noSavedStatisticalRunsOnThisPage" />
        </p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {runs.map((run) => {
            const meta = STAT_RUN_STATUS_META[run.status] ?? {
              label: run.status,
              tone: "slate" as const,
            };
            return (
              <li
                key={run.id}
                className="flex flex-wrap items-center justify-between gap-3 p-3 text-sm"
              >
                <div className="space-y-1">
                  <p className="font-mono" translate="no">
                    {run.method} · {run.id}
                  </p>
                  <p className="text-muted-foreground">
                    {formatDate(run.created_at)}
                  </p>
                </div>
                <StatusBadge
                  label={localizedMetaLabel(meta, uiLabelT)}
                  tone={meta.tone}
                />
                <button
                  type="button"
                  aria-pressed={selectedRunId === run.id}
                  onClick={() => onSelect(run.id)}
                  className="rounded-md border px-3 py-1.5 hover:bg-muted"
                >
                  {" "}
                  <UiMessage id="ui.report.viewSavedRun" />{" "}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <UiAttributes
        messages={{ "aria-label": { id: "ui.report.runHistoryPages" } }}
      >
        <nav
          aria-label={"Run history pages"}

          className="flex items-center gap-3 text-sm"
        >
          <button
            type="button"
            disabled={page === 1}
            onClick={() => onPage(page - 1)}
            className="rounded-md border px-3 py-1 disabled:opacity-50"
          >
            {" "}
            <UiMessage id="ui.quality.previous" />{" "}
          </button>
          <span>
            {" "}
            <UiMessage id="ui.quality.page" /> {page}{" "}
            <UiMessage id="ui.data.of" />{" "}
            {Math.max(1, Math.ceil(total / PAGE_SIZE))}
          </span>
          <button
            type="button"
            disabled={page * PAGE_SIZE >= total}
            onClick={() => onPage(page + 1)}
            className="rounded-md border px-3 py-1 disabled:opacity-50"
          >
            {" "}
            <UiMessage id="ui.quality.next" />{" "}
          </button>
        </nav>
      </UiAttributes>
    </div>
  );
}

function SelectedRun({
  projectId,
  dataset,
  summary,
  requestedRunId,
  run,
  isLoading,
  isError,
  error,
  onRetry,
  selectionIdentity,
  ticket,
}: {
  projectId: string;
  dataset: Dataset;
  summary: StatisticalRunSummary | undefined;
  requestedRunId: string | null;
  run: StatisticalRun | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  selectionIdentity: string;
  ticket: () => () => boolean;
}) {
  const { t: uiLabelT } = useDomainLanguage();

  if (!requestedRunId)
    return (
      <p role="status">
        <UiMessage id="ui.report.selectASavedRunToInspectItsRecord" />
      </p>
    );
  if (summary && summary.dataset_id !== dataset.id)
    return (
      <p role="alert">
        {" "}
        <UiMessage id="ui.report.theSelectedHistoryRecordBelongsToADifferentDataset" />{" "}
      </p>
    );
  if (isLoading)
    return (
      <UiAttributes
        messages={{ label: { id: "ui.report.loadingSavedReport" } }}
      >
        <PageLoader label={"Loading saved report…"} />
      </UiAttributes>
    );
  if (isError) return <QueryError error={error} onRetry={onRetry} />;
  if (!run)
    return (
      <p role="alert">
        <UiMessage id="ui.report.theSavedRunCouldNotBeLoaded" />
      </p>
    );
  const mismatch = reportMismatch(
    projectId,
    dataset,
    summary,
    run,
    requestedRunId,
  );
  if (mismatch) return <p role="alert">{mismatch}</p>;
  const meta = STAT_RUN_STATUS_META[run.status] ?? {
    label: run.status,
    tone: "slate" as const,
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold">
          {" "}
          <UiMessage id="ui.report.savedRun" />{" "}
          <span translate="no">{run.id}</span>
        </h3>
        <StatusBadge
          label={localizedMetaLabel(meta, uiLabelT)}
          tone={meta.tone}
        />
      </div>
      {run.status === "completed" ? (
        <>
          <SavedRunExport
            projectId={projectId}
            dataset={dataset}
            run={run}
            selectionIdentity={selectionIdentity}
            ticket={ticket}
          />
          <SavedReport run={run} />
        </>
      ) : (
        <div role="status" className="rounded-lg border p-4 text-sm">
          <p>
            <UiMessage id="ui.report.thisRunIs" /> {run.status}
            <UiMessage id="ui.report.noCompletedReportIsAvailable" />
          </p>
          <p translate="no">
            {run.error_message || run.error_code || (
              <UiMessage id="ui.displayBranch.report.errorDetailNotRecorded" />
            )}
          </p>
          {run.result?.warnings?.length ? (
            <ul className="list-disc pl-5">
              {run.result.warnings.map((warning, index) => (
                <li key={index} translate="no">
                  {warning}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ReportBody({ projectId }: { projectId: string }) {
  const params = useSearchParams();
  const selection = useResultSelection(
    params.get("datasetId"),
    params.get("runId"),
  );
  const datasetId = selection.datasetId ?? "";
  const runId = selection.runId ?? "";
  const [page, setPage] = useState(1);
  const datasetsQuery = useQuery({
    queryKey: ["report", "datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
    retry: false,
  });
  const datasetList = validDatasets(datasetsQuery.data)
    ? datasetsQuery.data
    : null;
  const datasets =
    datasetList?.filter(
      (item) => item.project_id === projectId && item.upload_status === "ready",
    ) ?? [];
  const dataset = datasets.find((item) => item.id === datasetId);
  const runsQuery = useQuery({
    queryKey: ["report", "runs", projectId, datasetId, page],
    queryFn: ({ signal }) =>
      statisticsApi.listRuns(datasetId, { page, pageSize: PAGE_SIZE }, signal),
    enabled: Boolean(dataset),
    retry: false,
  });
  const history = validHistory(runsQuery.data) ? runsQuery.data : null;
  const runs = history?.runs ?? [];
  const summary = runs.find((item) => item.id === runId);
  const runQuery = useQuery({
    queryKey: [
      "report",
      "run",
      projectId,
      datasetId,
      runId,
      selection.queryIdentity,
    ],
    queryFn: ({ signal }) => statisticsApi.getRun(runId, signal),
    enabled: Boolean(dataset && runId),
    retry: false,
  });

  const selectDataset = (id: string) => {
    selection.select(id || null, null);
    setPage(1);
  };
  const checkedRun =
    dataset &&
    runQuery.data &&
    !reportMismatch(projectId, dataset, summary, runQuery.data, runId)
      ? runQuery.data
      : null;
  const columns = checkedRun
    ? statisticalColumns({
        method_id: checkedRun.method,
        variables: [],
        ...checkedRun.input_config,
      } as StatisticalRunRequest)
    : [];
  usePreviewPublication({
    page: "保存结果",
    pageMessage: { id: "preview.context.savedResults" },
    scope:
      dataset && checkedRun && columns.length
        ? {
            project_id: projectId,
            dataset_id: dataset.id,
            dataset_sha256: dataset.sha256,
            columns,
            statistical_run_id: checkedRun.id,
          }
        : { project_id: projectId },
    draft: checkedRun
      ? "当前解释来源是保存的真实 Run；未运行新分析。"
      : "先选择当前数据集和保存记录，旧结果引用已清除。",
    draftMessage: {
      id: checkedRun
        ? "preview.context.savedRun"
        : "preview.context.chooseSavedRun",
    },
  });
  const selectPage = (next: number) => {
    setPage(next);
    selection.select(selection.datasetId, null);
  };

  return (
    <div className="space-y-6 py-6">
      <header>
        <h2 className="font-heading text-2xl font-bold">
          {" "}
          <UiMessage id="ui.report.savedStatisticalReports" />{" "}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {" "}
          <UiMessage id="ui.report.readOnlyViewsOfPersistedStatisticalRunsNoAnalysisIsStartedHere" />{" "}
        </p>
      </header>
      {datasetsQuery.isLoading ? (
        <UiAttributes messages={{ label: { id: "ui.data.loadingDatasets" } }}>
          <PageLoader label={"Loading datasets…"} />
        </UiAttributes>
      ) : datasetsQuery.isError ? (
        <QueryError
          error={datasetsQuery.error}
          onRetry={() => void datasetsQuery.refetch()}
        />
      ) : !datasetList ? (
        <p role="alert">
          <UiMessage id="ui.report.datasetListIsMalformedAndCannotBeDisplayed" />
        </p>
      ) : datasets.length === 0 ? (
        <p role="status">
          <UiMessage id="ui.report.noReadyDatasetsAreAvailableForThisProject" />
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="report-dataset" className="text-sm font-medium">
              {" "}
              <UiMessage id="workflow.node.dataset.label" />{" "}
            </label>
            <SearchSelect
              id="report-dataset"
              value={datasetId}
              onChange={(event) => selectDataset(event.target.value)}
              className="rounded-md border bg-background px-3 py-2 text-sm"
              translate="no"
            >
              <option value="">
                <UiMessage id="ui.ml.selectADataset" />
              </option>
              {datasets.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.original_filename} · {item.id}
                </option>
              ))}
            </SearchSelect>
          </div>
          {!dataset ? (
            <p role="status">
              <UiMessage id="ui.report.selectADatasetToLoadItsSavedRuns" />
            </p>
          ) : (
            <UiAttributes
              messages={{ "aria-label": { id: "ui.report.savedRuns" } }}
            >
              <section aria-label={"Saved runs"} className="space-y-4">
                <h3 className="text-lg font-semibold">
                  <UiMessage id="ui.report.savedRuns" />
                </h3>
                {runsQuery.isLoading ? (
                  <UiAttributes
                    messages={{ label: { id: "ui.report.loadingSavedRuns" } }}
                  >
                    <PageLoader label={"Loading saved runs…"} />
                  </UiAttributes>
                ) : runsQuery.isError ? (
                  <QueryError
                    error={runsQuery.error}
                    onRetry={() => void runsQuery.refetch()}
                  />
                ) : !history ? (
                  <p role="alert">
                    {" "}
                    <UiMessage id="ui.report.savedRunHistoryIsMalformedAndCannotBeDisplayed" />{" "}
                  </p>
                ) : (
                  <RunHistory
                    runs={runs}
                    total={history.total}
                    page={page}
                    selectedRunId={runId}
                    onSelect={(id) => selection.select(datasetId, id)}
                    onPage={selectPage}
                  />
                )}
                <UiAttributes
                  messages={{ "aria-label": { id: "ui.report.selectedRun" } }}
                >
                  <section aria-label={"Selected run"} className="space-y-4">
                    <SelectedRun
                      projectId={projectId}
                      dataset={dataset}
                      summary={summary}
                      requestedRunId={runId || null}
                      run={runQuery.data}
                      isLoading={runQuery.isLoading}
                      isError={runQuery.isError}
                      error={runQuery.error}
                      onRetry={() => void runQuery.refetch()}
                      selectionIdentity={selection.queryIdentity}
                      ticket={selection.ticket}
                    />
                  </section>
                </UiAttributes>
              </section>
            </UiAttributes>
          )}
        </>
      )}
    </div>
  );
}

export default function ReportClient({ projectId }: { projectId: string }) {
  const resolvedProjectId = useResultProjectId(projectId);
  if (!resolvedProjectId || resolvedProjectId === "__desktop_project__") {
    return (
      <p role="alert">
        <UiMessage id="ui.report.reportProjectIdentityIsMissing" />
      </p>
    );
  }
  return (
    <UiAttributes messages={{ label: { id: "ui.report.loadingReports" } }}>
      <AuthGuard label={"Loading reports…"}>
        <ResultSourceBoundary projectId={resolvedProjectId}>
          <ProjectShell projectId={resolvedProjectId}>
            <ReportBody key={resolvedProjectId} projectId={resolvedProjectId} />
          </ProjectShell>
        </ResultSourceBoundary>
      </AuthGuard>
    </UiAttributes>
  );
}
