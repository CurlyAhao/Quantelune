"use client";
import { SavedFailureMessage } from "@/components/friendly-error";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { Suspense, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DatabaseIcon,
  Loader2Icon,
  MicroscopeIcon,
  PlayIcon,
  RefreshCwIcon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { DesktopLink } from "@/components/desktop-link";
import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import { StatusBadge } from "@/components/status-badge";
import { ProfilingColumns } from "./profiling-columns";
import { ProfilingCorrelations } from "./profiling-correlations";
import { ProfilingMissing } from "./profiling-missing";
import { ProfilingOverview } from "./profiling-overview";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { datasetsApi, profilingApi } from "@/lib/api-endpoints";
import { friendlyApiError } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useResultSelection } from "@/components/result-source";

interface ProfilingClientProps {
  projectId: string;
}

export default function ProfilingClient({ projectId }: ProfilingClientProps) {
  return (
    <UiAttributes messages={{ label: { id: "ui.profiling.loadingProfiling" } }}>
      <AuthGuard label={"Loading profiling…"}>
        <ProjectShell projectId={projectId}>
          <Suspense fallback={<PageLoader label="正在读取画像…" />}>
            <ProfilingBody projectId={projectId} />
          </Suspense>
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

function ProfilingBody({ projectId }: ProfilingClientProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { formatBytes } = useLocaleDisplay();

  const queryClient = useQueryClient();
  const params = useSearchParams();
  const queryDatasetId = params.get("datasetId"), queryRunId = params.get("runId");
  const selection = useResultSelection(queryDatasetId, queryRunId);
  const selectedDatasetId = selection.datasetId;
  const profileId = selection.runId;
  const selectFromUrl = useRef(selection.select);
  selectFromUrl.current = selection.select;
  useEffect(() => {
    selectFromUrl.current(queryDatasetId, queryRunId);
  }, [queryDatasetId, queryRunId]);

  const datasetsQuery = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });

  const datasets = datasetsQuery.data ?? [];
  const readyDatasets = datasets.filter((d) => d.upload_status === "ready");
  const selectedDataset =
    datasets.find((d) => d.id === selectedDatasetId) ?? null;

  // Fetch profile once a dataset is selected
  const profileQuery = useQuery({
    queryKey: ["profile", selectedDatasetId, profileId],
    queryFn: async ({ signal }) => {
      const value = profileId
        ? await profilingApi.getRun(selectedDatasetId!, profileId, signal)
        : await profilingApi.get(selectedDatasetId!, signal);
      if (value.dataset_id !== selectedDatasetId || (profileId && value.id !== profileId)) throw new Error("画像来源不匹配，请重新打开结果。");
      return value;
    },
    enabled: selectedDatasetId !== null,
    retry: false,
  });

  const computeMutation = useMutation({
    mutationFn: ({ datasetId, force }: { datasetId: string; force: boolean }) =>
      profilingApi.compute(datasetId, force),
    onSuccess: (value, variables) => {
      queryClient.setQueryData(["profile", variables.datasetId, value.id], value);
      if (selection.datasetId === variables.datasetId) selection.select(variables.datasetId, value.id);
      void queryClient.invalidateQueries({
        queryKey: ["profile", selectedDatasetId],
      });
    },
  });

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

  // No datasets at all
  if (datasets.length === 0) {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.data.noDatasetsYet" },
          message: {
            id: "ui.profiling.uploadADatasetFirstThenRunAProfileToExploreItsStructureAndQuality",
          },
        }}
      >
        <EmptyPanel
          title={"No datasets yet"}
          message={
            "Upload a dataset first, then run a profile to explore its structure and quality."
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
        <label
          htmlFor="dataset-select"
          className="text-sm font-medium text-muted-foreground"
        >
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
                onClick={() => selection.select(ds.id, null)}
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
              id: "ui.profiling.chooseADatasetAboveToInspectItsProfileOrRunANewOne",
            },
          }}
        >
          <EmptyPanel
            title={"Select a dataset"}
            message={
              "Choose a dataset above to inspect its profile, or run a new one."
            }
            icon={<MicroscopeIcon className="size-8 text-primary/40" />}
          />
        </UiAttributes>
      )}

      {/* Profile area */}
      {selectedDatasetId !== null && (
        <ProfileArea
          key={selectedDatasetId}
          profileQuery={profileQuery as UseQueryResult<Profile, Error>}
          computeMutation={computeMutation}
          datasetId={selectedDatasetId}
          fileSize={selectedDataset?.file_size ?? null}
          exactProfileId={profileId}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import type { Profile } from "@/lib/types";

interface ProfileAreaProps {
  profileQuery: UseQueryResult<Profile, Error>;
  computeMutation: UseMutationResult<
    Profile,
    Error,
    { datasetId: string; force: boolean }
  >;
  datasetId: string;
  fileSize: number | null;
  exactProfileId?: string | null;
}

function ProfileArea({
  profileQuery,
  computeMutation,
  datasetId,
  fileSize,
  exactProfileId,
}: ProfileAreaProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { formatDate, formatNumber } = useLocaleDisplay();

  const isComputing = computeMutation.isPending;

  // Loading profile
  if (profileQuery.isLoading) {
    return (
      <UiAttributes messages={{ label: { id: "ui.profiling.loadingProfile" } }}>
        <PageLoader label={"Loading profile…"} />
      </UiAttributes>
    );
  }

  // No profile yet (404) or error fetching
  const notFound =
    profileQuery.isError &&
    (profileQuery.error as { status?: number })?.status === 404;

  if (profileQuery.isError && !notFound) {
    const friendly = friendlyApiError(profileQuery.error, uiCopyT);
    return (
      <ErrorState
        title={friendly.title}
        message={friendly.message}
        onRetry={() => void profileQuery.refetch()}
      />
    );
  }

  const profile = profileQuery.data;

  if (notFound && exactProfileId) return <ErrorState title="画像不存在" message="此画像已不可用，请返回任务选择其他结果。" onRetry={() => void profileQuery.refetch()} />;

  // No profile computed yet
  if (notFound || !profile) {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.profiling.noProfileYet" },
          message: {
            id: "ui.profiling.runAProfileToComputePerColumnStatisticsMissingValueAnalysisCorrelationsAndADataQualityScore",
          },
        }}
      >
        <EmptyPanel
          title={"No profile yet"}
          message={
            "Run a profile to compute per-column statistics, missing-value analysis, correlations, and a data-quality score."
          }
          icon={<MicroscopeIcon className="size-8 text-primary/40" />}

          action={
            <Button
              onClick={() =>
                computeMutation.mutate({ datasetId, force: false })
              }
              disabled={isComputing}
            >
              {isComputing ? (
                <>
                  <Loader2Icon className="size-4 animate-spin" />{" "}
                  <UiMessage id="ui.profiling.computing" />{" "}
                </>
              ) : (
                <>
                  <PlayIcon className="size-4" />{" "}
                  <UiMessage id="ui.profiling.runProfiling" />{" "}
                </>
              )}
            </Button>
          }
        />
      </UiAttributes>
    );
  }

  // Failed profile
  if (profile.status === "failed") {
    return (
      <UiAttributes
        messages={{ title: { id: "ui.profiling.profilingFailed" } }}
      >
        <ErrorState
          title={"Profiling failed"}

          message={<SavedFailureMessage code={profile.error_code} />}
          diagnostic={profile.error_message}
          onRetry={() => computeMutation.mutate({ datasetId, force: true })}
        />
      </UiAttributes>
    );
  }

  // Computing / pending
  if (
    profile.status === "profiling" ||
    profile.status === "pending" ||
    isComputing
  ) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed bg-card/60 py-16">
        <Loader2Icon className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">
          <UiMessage id="ui.profiling.computingProfile" />
        </p>
      </div>
    );
  }

  const result = profile.result;
  if (!result) {
    return (
      <UiAttributes
        messages={{
          title: { id: "ui.profiling.emptyProfile" },
          message: {
            id: "ui.profiling.thisProfileHasNoResultPayloadTryRecomputing",
          },
        }}
      >
        <EmptyPanel
          title={"Empty profile"}
          message={"This profile has no result payload. Try recomputing."}
          icon={<MicroscopeIcon className="size-8 text-primary/40" />}

          action={
            <Button
              onClick={() => computeMutation.mutate({ datasetId, force: true })}
            >
              <RefreshCwIcon className="size-4" />{" "}
              <UiMessage id="ui.profiling.recompute" />{" "}
            </Button>
          }
        />
      </UiAttributes>
    );
  }

  const isStale = profile.status === "stale";

  return (
    <div className="space-y-4">
      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <StatusBadge
          label={
            isStale
              ? uiCopyT("ui.residual.common.stale")
              : `v${profile.profiling_version}`
          }
          tone={isStale ? "amber" : "teal"}
          dot={false}
        />
        <span>
          <UiMessage id="ui.profiling.algorithm" /> {profile.algorithm_version}
        </span>
        <span>
          <UiMessage id="ui.profiling.computed" />{" "}
          {formatDate(profile.completed_at)}
        </span>
        <span>
          {formatNumber(profile.row_count)} <UiMessage id="ui.data.rows" />
        </span>
        {profile.sampled && (
          <span>
            <UiMessage id="ui.profiling.sampled" />
            {formatNumber(profile.sample_size)})
          </span>
        )}
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => computeMutation.mutate({ datasetId, force: true })}
            disabled={isComputing}
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

      {isStale && (
        <div className="rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {" "}
          <UiMessage id="ui.profiling.theUnderlyingDataChangedSinceThisProfileWasComputedRecomputeToRefreshTheStatistics" />{" "}
        </div>
      )}

      {/* Tabs */}
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">
            <UiMessage id="ui.profiling.overview" />
          </TabsTrigger>
          <TabsTrigger value="columns">
            {" "}
            <UiMessage id="workflow.parameter.columns" />{" "}
            <span className="ml-1.5 font-mono text-[0.62rem] opacity-60">
              {result.columns.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="missing">
            <UiMessage id="ui.profiling.missing" />
          </TabsTrigger>
          <TabsTrigger value="correlations">
            <UiMessage id="ui.profiling.correlations" />
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-5">
          <ProfilingOverview
            overview={result.overview}
            quality={result.quality}
            fileSize={fileSize}
          />
        </TabsContent>

        <TabsContent value="columns" className="mt-5">
          <ProfilingColumns columns={result.columns} />
        </TabsContent>

        <TabsContent value="missing" className="mt-5">
          <ProfilingMissing missingness={result.missingness} />
        </TabsContent>

        <TabsContent value="correlations" className="mt-5">
          <ProfilingCorrelations correlations={result.correlations} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

/* ------------------------------------------------------------------ */

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
