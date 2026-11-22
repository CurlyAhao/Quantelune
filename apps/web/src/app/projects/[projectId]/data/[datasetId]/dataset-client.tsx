"use client";

import { useLanguage as usePropertyLanguage } from "@/providers/language-provider";
import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeftIcon,
  CheckIcon,
  DatabaseIcon,
  DownloadIcon,
  MinusIcon,
  Table2Icon,
  Trash2Icon,
  TriangleAlertIcon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { DesktopLink } from "@/components/desktop-link";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DownloadButton } from "@/components/download-button";
import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { useResultProjectId } from "@/components/result-source";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { datasetsApi } from "@/lib/api-endpoints";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { UPLOAD_STATUS_META, friendlyApiError } from "@/lib/format";
import type { ColumnProfile, DatasetPreview } from "@/lib/types";
import { cn } from "@/lib/utils";

const PREVIEW_ROW_COUNT = 20;

export default function DatasetDetailClient({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const resolvedProjectId = useResultProjectId(projectId);
  const params = useSearchParams();
  const resolvedDatasetId =
    datasetId === "__desktop_dataset__" ? params.get("datasetId") : datasetId;
  if (
    !resolvedProjectId ||
    resolvedProjectId === "__desktop_project__" ||
    !resolvedDatasetId ||
    resolvedDatasetId === "__desktop_dataset__"
  )
    return (
      <p role="alert">
        <UiMessage id="ui.data.datasetIdentityUnavailable" />
      </p>
    );
  return (
    <UiAttributes messages={{ label: { id: "ui.data.loadingDataset" } }}>
      <AuthGuard label={"Loading dataset…"}>
        <DatasetDetailBody
          key={`${resolvedProjectId}:${resolvedDatasetId}`}
          projectId={resolvedProjectId}
          datasetId={resolvedDatasetId}
        />
      </AuthGuard>
    </UiAttributes>
  );
}

function DatasetDetailBody({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const { t: propertyT } = usePropertyLanguage();

  const { t: uiCopyT } = useCopyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const { formatBytes, formatNumber, formatDate } = useLocaleDisplay();

  const router = useRouter();
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const datasetQuery = useQuery({
    queryKey: ["dataset", projectId, datasetId],
    queryFn: async ({ signal }) => {
      const dataset = await datasetsApi.get(datasetId, signal);
      if (dataset.id !== datasetId || dataset.project_id !== projectId)
        throw new Error("Dataset source identity mismatch");
      return dataset;
    },
  });

  const previewQuery = useQuery({
    queryKey: ["dataset-preview", datasetId],
    queryFn: ({ signal }) =>
      datasetsApi.preview(datasetId, PREVIEW_ROW_COUNT, signal),
    enabled: datasetQuery.data?.upload_status === "ready",
  });

  const deleteMutation = useMutation({
    mutationFn: () => datasetsApi.remove(datasetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets", projectId] });
      toast.success(<UiMessage id="ui.data.datasetDeleted" />);
      navigateToAppRoute(router, `/projects/${projectId}/data`, "replace");
    },
    onError: (error) => {
      const friendly = friendlyApiError(error, uiCopyT);
      toast.error(friendly.title, { description: friendly.message });
      setDeleteOpen(false);
    },
  });

  if (datasetQuery.isLoading) {
    return (
      <UiAttributes messages={{ label: { id: "ui.data.fetchingDataset" } }}>
        <PageLoader label={"Fetching dataset…"} />
      </UiAttributes>
    );
  }

  if (datasetQuery.isError || !datasetQuery.data) {
    const friendly = friendlyApiError(datasetQuery.error, uiCopyT);
    return (
      <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
        <ErrorState
          title={friendly.title}
          message={friendly.message}
          onRetry={() => void datasetQuery.refetch()}
        />
      </div>
    );
  }

  const dataset = datasetQuery.data;
  const status =
    UPLOAD_STATUS_META[dataset.upload_status] ?? UPLOAD_STATUS_META.pending;
  const preview = previewQuery.data;
  const columnsNeedingAttention =
    preview?.columns.filter(
      (column) =>
        column.null_rate > 0.2 || (!column.nullable && column.null_count > 0),
    ) ?? [];

  return (
    <div className="mx-auto max-w-6xl px-4 pb-20 sm:px-6">
      {/* Breadcrumb */}
      <UiAttributes messages={{ "aria-label": { id: "ui.data.breadcrumb" } }}>
        <nav
          aria-label={"Breadcrumb"}

          className="flex items-center gap-1.5 pt-6 font-mono text-xs text-muted-foreground"
        >
          <DesktopLink
            href={`/projects/${projectId}/data`}
            className="flex items-center gap-1 rounded-sm transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <ArrowLeftIcon className="size-3.5" />{" "}
            <UiMessage id="ui.data.allDatasets" />{" "}
          </DesktopLink>
        </nav>
      </UiAttributes>

      {/* Header card */}
      <header className="animate-rise mt-4 overflow-hidden rounded-lg border bg-card shadow-plate">
        <div
          aria-hidden
          className="h-1 bg-gradient-to-r from-primary to-accent"
        />
        <div className="flex flex-wrap items-start justify-between gap-4 p-5 sm:p-6">
          <div className="flex min-w-0 items-start gap-3.5">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-md bg-primary/10">
              <DatabaseIcon className="size-5 text-primary" />
            </span>
            <div className="min-w-0">
              <h1
                className="truncate font-heading text-xl font-bold tracking-tight sm:text-2xl"
                title={dataset.original_filename}
              >
                {dataset.original_filename}
              </h1>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground">
                <span className="rounded-sm border bg-muted/60 px-1.5 py-0.5 font-medium tracking-wide">
                  {dataset.file_format.toUpperCase()}
                </span>
                <span className="tabular-nums">
                  {formatBytes(dataset.file_size)}
                </span>
                <span className="tabular-nums">
                  {formatNumber(dataset.row_count)}{" "}
                  <UiMessage id="ui.data.rows" />{" "}
                </span>
                <span className="tabular-nums">
                  {formatNumber(dataset.column_count)}{" "}
                  <UiMessage id="ui.data.cols" />{" "}
                </span>
                <span className="tabular-nums">
                  {" "}
                  <UiMessage id="ui.data.uploaded" />{" "}
                  {formatDate(dataset.created_at)}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <StatusBadge
              label={localizedMetaLabel(status, uiLabelT)}
              tone={status.tone}
              pulse={
                dataset.upload_status === "uploading" ||
                dataset.upload_status === "validating"
              }
            />
            <DownloadButton
              identity={dataset.id + dataset.sha256}
              download={(signal) =>
                datasetsApi.download(
                  dataset.id,
                  dataset.original_filename,
                  signal,
                )
              }
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              <DownloadIcon className="size-3.5" />{" "}
              <UiMessage id="ui.data.download" />{" "}
            </DownloadButton>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setDeleteOpen(true)}
            >
              <Trash2Icon className="size-3.5" />{" "}
              <UiMessage id="ui.data.delete" />{" "}
            </Button>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-px border-t bg-border text-xs sm:grid-cols-4">
          {[
            {
              label: propertyT("ui.data.safeFilename"),
              value: dataset.safe_filename,
            },
            { label: propertyT("ui.data.mimeType"), value: dataset.mime_type },
            {
              label: propertyT("ui.data.sha256"),
              value: dataset.sha256.slice(0, 16) + "…",
            },
            {
              label: propertyT("ui.data.validated"),
              value: dataset.validated_at
                ? formatDate(dataset.validated_at)
                : propertyT("data.notYetValidated"),
            },
          ].map(({ label, value }) => (
            <div key={label} className="bg-card px-4 py-2.5">
              <dt className="font-mono text-[0.6rem] tracking-widest text-muted-foreground uppercase">
                {label}
              </dt>
              <dd className="mt-0.5 truncate font-mono text-xs" title={value}>
                {value}
              </dd>
            </div>
          ))}
        </dl>
      </header>

      {/* Failure notice */}
      {dataset.upload_status === "failed" && (
        <Alert variant="destructive" className="animate-rise mt-6">
          <TriangleAlertIcon />
          <AlertTitle>
            {" "}
            <UiMessage id="ui.data.processingFailed" />{" "}
            {dataset.error_code && (
              <span className="font-mono text-xs">({dataset.error_code})</span>
            )}
          </AlertTitle>
          <AlertDescription>
            {dataset.error_message ?? (
              <UiMessage id="ui.displayBranch.data.fileProcessingFailed" />
            )}
          </AlertDescription>
        </Alert>
      )}

      {/* Pending notice */}
      {(dataset.upload_status === "pending" ||
        dataset.upload_status === "uploading" ||
        dataset.upload_status === "validating") && (
        <Alert className="animate-rise mt-6">
          <TriangleAlertIcon className="text-accent-foreground" />
          <AlertTitle>
            <UiMessage id="ui.data.stillProcessing" />
          </AlertTitle>
          <AlertDescription>
            {" "}
            <UiMessage id="ui.data.thisDatasetIs" /> {dataset.upload_status}
            <UiMessage id="ui.data.thePreviewAppearsAsSoonAsValidationCompletesRefreshInAMoment" />{" "}
          </AlertDescription>
        </Alert>
      )}

      {/* Data tabs */}
      {previewQuery.isLoading && (
        <UiAttributes messages={{ label: { id: "ui.data.profilingColumns" } }}>
          <PageLoader label={"Profiling columns…"} className="min-h-40" />
        </UiAttributes>
      )}

      {previewQuery.isError && (
        <ErrorState
          {...friendlyApiError(previewQuery.error, uiCopyT)}
          onRetry={() => void previewQuery.refetch()}
          className="mt-6"
        />
      )}

      {preview && (
        <Tabs defaultValue="rows" className="animate-rise mt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabsList variant="line">
              <TabsTrigger value="rows">
                <Table2Icon className="size-3.5" />{" "}
                <UiMessage id="ui.data.dataPreview" />{" "}
              </TabsTrigger>
              <TabsTrigger value="schema">
                <DatabaseIcon className="size-3.5" />{" "}
                <UiMessage id="ui.data.columnSchema" />{" "}
              </TabsTrigger>
            </TabsList>
            <p className="font-mono text-[0.65rem] tracking-wide text-muted-foreground uppercase tabular-nums">
              {" "}
              <UiMessage id="ui.data.showing" />{" "}
              {formatNumber(preview.showing_rows)} <UiMessage id="ui.data.of" />{" "}
              {formatNumber(preview.total_rows)}{" "}
              <UiMessage id="ui.data.rows" />{" "}
            </p>
          </div>

          {/* ------------------------------------------------ rows tab */}
          <TabsContent value="rows" className="mt-4">
            {columnsNeedingAttention.length > 0 && (
              <Alert className="mb-4">
                <TriangleAlertIcon className="text-accent-foreground" />
                <AlertTitle>
                  {columnsNeedingAttention.length}{" "}
                  {columnsNeedingAttention.length === 1 ? (
                    <UiMessage id="ui.displayBranch.data.columnNeeds" />
                  ) : (
                    <UiMessage id="ui.displayBranch.data.columnsNeed" />
                  )}{" "}
                  <UiMessage id="ui.data.attention" />{" "}
                </AlertTitle>
                <AlertDescription>
                  {" "}
                  <UiMessage id="ui.data.highNullRate20DetectedIn" />{" "}
                  {columnsNeedingAttention
                    .map((column) => column.name)
                    .slice(0, 6)
                    .join(", ")}
                  {columnsNeedingAttention.length > 6 ? "…" : ""}
                  <UiMessage id="ui.data.reviewThemInTheSchemaTab" />{" "}
                </AlertDescription>
              </Alert>
            )}

            <div className="overflow-hidden rounded-lg border bg-card shadow-plate">
              <div className="scrollbar-slim max-h-[32rem] overflow-auto">
                <PreviewTable preview={preview} />
              </div>
            </div>
          </TabsContent>

          {/* ------------------------------------------------ schema tab */}
          <TabsContent value="schema" className="mt-4">
            <div className="overflow-hidden rounded-lg border bg-card shadow-plate">
              <div className="scrollbar-slim overflow-x-auto">
                <SchemaTable columns={preview.columns} />
              </div>
            </div>
          </TabsContent>
        </Tabs>
      )}

      <UiAttributes messages={{ title: { id: "ui.data.deleteThisDataset" } }}>
        <ConfirmDialog
          title={"Delete this dataset?"}
          open={deleteOpen}
          onOpenChange={setDeleteOpen}

          description={
            <>
              <span className="font-medium text-foreground">
                “{dataset.original_filename}”
              </span>{" "}
              ({formatBytes(dataset.file_size)}
              <UiMessage id="ui.data.willBePermanentlyRemovedFromThisProjectYouWillReturnToTheDatasetList" />{" "}
            </>
          }
          confirmLabel="Delete dataset"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate()}
        />
      </UiAttributes>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Preview table                                                       */
/* ------------------------------------------------------------------ */

function PreviewTable({ preview }: { preview: DatasetPreview }) {
  return (
    <Table>
      <TableHeader className="sticky top-0 z-10">
        <TableRow className="bg-muted hover:bg-muted">
          <TableHead className="w-12 border-r font-mono text-[0.6rem] text-muted-foreground">
            #
          </TableHead>
          {preview.columns.map((column) => (
            <TableHead key={column.name} className="whitespace-nowrap">
              <span className="block font-medium">{column.name}</span>
              <span className="block font-mono text-[0.6rem] font-normal tracking-wide text-primary">
                {column.inferred_type}
              </span>
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {preview.rows.length === 0 && (
          <TableRow>
            <TableCell
              colSpan={preview.columns.length + 1}
              className="py-10 text-center text-sm text-muted-foreground"
            >
              {" "}
              <UiMessage id="ui.data.noRowsToPreview" />{" "}
            </TableCell>
          </TableRow>
        )}
        {preview.rows.map((row, rowIndex) => (
          <TableRow key={rowIndex} className="hover:bg-primary/4">
            <TableCell className="border-r font-mono text-[0.65rem] text-muted-foreground tabular-nums">
              {rowIndex + 1}
            </TableCell>
            {preview.columns.map((column) => (
              <TableCell
                key={column.name}
                className="max-w-60 truncate py-2 font-mono text-xs"
                title={cellTitle(row[column.name])}
              >
                <CellValue value={row[column.name]} />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function CellValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return (
      <span className="text-muted-foreground/70 italic">
        <UiMessage id="ui.data.null" />
      </span>
    );
  }
  if (typeof value === "number") {
    return (
      <span className="tabular-nums">
        {Number.isFinite(value) ? value : String(value)}
      </span>
    );
  }
  if (typeof value === "boolean") {
    return <span className="text-primary">{String(value)}</span>;
  }
  if (typeof value === "object") {
    return <span>{JSON.stringify(value)}</span>;
  }
  return <span>{String(value)}</span>;
}

function cellTitle(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/* ------------------------------------------------------------------ */
/* Schema table                                                        */
/* ------------------------------------------------------------------ */

function SchemaTable({ columns }: { columns: ColumnProfile[] }) {
  const { formatPercent, formatNumber } = useLocaleDisplay();

  return (
    <Table>
      <TableHeader>
        <TableRow className="bg-muted/50">
          {[
            "Column",
            "Inferred type",
            "Polars type",
            "Nullable",
            "Null rate",
            "Unique",
            "Sample values",
          ].map((heading) => (
            <TableHead
              key={heading}
              className="font-mono text-[0.65rem] tracking-widest uppercase"
            >
              {heading}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {columns.map((column, index) => {
          const highNulls = column.null_rate > 0.2;
          return (
            <TableRow
              key={column.name}
              className="animate-rise"
              style={{ animationDelay: `${Math.min(index, 12) * 35}ms` }}
            >
              <TableCell className="font-mono text-xs font-semibold whitespace-nowrap">
                {column.name}
              </TableCell>
              <TableCell>
                <span className="rounded-sm bg-primary/8 px-1.5 py-0.5 font-mono text-[0.7rem] font-medium text-primary">
                  {column.inferred_type}
                </span>
              </TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {column.polars_type}
              </TableCell>
              <TableCell>
                {column.nullable ? (
                  <span className="flex size-5 items-center justify-center rounded-full bg-primary/10">
                    <CheckIcon className="size-3 text-primary" />
                  </span>
                ) : (
                  <span className="flex size-5 items-center justify-center rounded-full bg-muted">
                    <MinusIcon className="size-3 text-muted-foreground" />
                  </span>
                )}
                <span className="sr-only">
                  {column.nullable ? (
                    <UiMessage id="ui.common.nullable" />
                  ) : (
                    <UiMessage id="ui.common.notNullable" />
                  )}
                </span>
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all duration-500",
                        highNulls ? "bg-accent" : "bg-primary",
                      )}
                      style={{
                        width: `${Math.min(100, Math.round(column.null_rate * 100))}%`,
                      }}
                    />
                  </div>
                  <span
                    className={cn(
                      "font-mono text-xs tabular-nums",
                      highNulls
                        ? "font-semibold text-accent-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    {formatPercent(column.null_rate)}
                  </span>
                </div>
                <span className="block font-mono text-[0.6rem] text-muted-foreground tabular-nums">
                  {formatNumber(column.null_count)}{" "}
                  <UiMessage id="ui.data.nulls" />{" "}
                </span>
              </TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground tabular-nums">
                {formatNumber(column.unique_count)}
              </TableCell>
              <TableCell>
                <div className="flex max-w-72 flex-wrap gap-1">
                  {column.sample_values
                    .slice(0, 3)
                    .map((sample, sampleIndex) => (
                      <span
                        key={sampleIndex}
                        className="max-w-32 truncate rounded-sm border bg-muted/50 px-1.5 py-0.5 font-mono text-[0.65rem]"
                        title={cellTitle(sample)}
                      >
                        {sample === null || sample === undefined ? (
                          <span className="text-muted-foreground/70 italic">
                            <UiMessage id="ui.data.null" />
                          </span>
                        ) : (
                          String(sample)
                        )}
                      </span>
                    ))}
                  {column.sample_values.length === 0 && (
                    <span className="text-xs text-muted-foreground/70 italic">
                      {" "}
                      <UiMessage id="ui.data.noSamples" />{" "}
                    </span>
                  )}
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
