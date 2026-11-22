"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  DatabaseIcon,
  DownloadIcon,
  EyeIcon,
  FileSpreadsheetIcon,
  Trash2Icon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { DesktopLink } from "@/components/desktop-link";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DownloadButton } from "@/components/download-button";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ProjectShell } from "@/components/project-shell";
import { useResultProjectId } from "@/components/result-source";
import { usePreviewPublication } from "@/components/preview-workspace";
import { StatusBadge } from "@/components/status-badge";
import { UploadDialog } from "@/components/upload-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { datasetsApi } from "@/lib/api-endpoints";
import { UPLOAD_STATUS_META, friendlyApiError } from "@/lib/format";
import type { Dataset } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function DataClient({ projectId }: { projectId: string }) {
  const resolvedProjectId = useResultProjectId(projectId);
  if (!resolvedProjectId || resolvedProjectId === "__desktop_project__")
    return (
      <p role="alert">
        <UiMessage id="ui.research.projectIdentityIsMissing" />
      </p>
    );
  return (
    <UiAttributes messages={{ label: { id: "ui.data.loadingDatasets" } }}>
      <AuthGuard label={"Loading datasets…"}>
        <ProjectShell projectId={resolvedProjectId}>
          <DataBody key={resolvedProjectId} projectId={resolvedProjectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

function DataBody({ projectId }: { projectId: string }) {
  const { t: uiCopyT } = useCopyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const { formatBytes, formatNumber, formatDate } = useLocaleDisplay();

  const queryClient = useQueryClient();
  const [pendingDelete, setPendingDelete] = useState<Dataset | null>(null);
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);

  const datasetsQuery = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: async ({ signal }) => {
      const datasets = await datasetsApi.list(projectId, signal);
      if (datasets.some((dataset) => dataset.project_id !== projectId))
        throw new Error("Dataset project identity mismatch");
      return datasets;
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (datasetId: string) => datasetsApi.remove(datasetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets", projectId] });
      setPendingDelete(null);
      toast.success(<UiMessage id="ui.data.datasetDeleted" />);
    },
    onError: (error) => {
      const friendly = friendlyApiError(error, uiCopyT);
      toast.error(friendly.title, { description: friendly.message });
      setPendingDelete(null);
    },
  });

  const datasets = datasetsQuery.data ?? [];
  const selected = datasets.find(
    (d) =>
      d.id === selectedDatasetId &&
      d.project_id === projectId &&
      d.upload_status === "ready",
  );
  const preview = useQuery({
    queryKey: ["datasets", "preview", selectedDatasetId],
    queryFn: ({ signal }) => datasetsApi.preview(selectedDatasetId, 5, signal),
    enabled: !!selected,
  });
  usePreviewPublication({
    page: selected ? `文件：${selected.original_filename}` : "项目文件",
    pageMessage: selected
      ? {
          id: "preview.context.file",
          values: { name: selected.original_filename },
        }
      : { id: "preview.context.projectFiles" },
    scope:
      selected && selectedColumns.length
        ? {
            project_id: projectId,
            dataset_id: selected.id,
            dataset_sha256: selected.sha256,
            columns: selectedColumns,
          }
        : { project_id: projectId },
    draft: selected
      ? selectedColumns.length
        ? "只发布勾选列的类型和受控摘要；预览行不外发。"
        : "已选文件，请勾选用于 AI 上下文的列。"
      : "选择当前文件和列以建立上下文。",
    draftMessage: {
      id: selected
        ? selectedColumns.length
          ? "preview.context.selectedColumns"
          : "preview.context.chooseColumns"
        : "preview.context.chooseFile",
    },
  });

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-heading text-xl font-bold tracking-tight">
            {" "}
            <UiMessage id="ui.data.datasets" />{" "}
          </h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {datasetsQuery.isSuccess ? (
              <UiMessage
                id={
                  datasets.length === 1
                    ? "data.fileCount.one"
                    : "data.fileCount.other"
                }
                values={{
                  count: formatNumber(datasets.length),
                }}
              />
            ) : (
              <UiMessage id="ui.displayBranch.data.projectUploadedFiles" />
            )}
          </p>
        </div>
        <UploadDialog projectId={projectId} />
      </div>
      <UiAttributes
        messages={{
          "aria-label": { id: "ui.data.aiContextForTheCurrentFile" },
        }}
      >
        <section
          aria-label={"当前文件 AI 上下文"}

          className="mt-4 space-y-2 rounded border p-3 text-sm"
        >
          <label>
            {" "}
            <UiMessage id="ui.data.currentFile" />{" "}
            <SearchSelect
              className="ml-2 rounded border bg-background p-1"
              value={selectedDatasetId}
              onChange={(e) => {
                setSelectedDatasetId(e.target.value);
                setSelectedColumns([]);
              }}
            >
              <option value="">
                <UiMessage id="ui.data.selectFile" />
              </option>
              {datasets
                .filter(
                  (d) =>
                    d.project_id === projectId && d.upload_status === "ready",
                )
                .map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.original_filename}
                  </option>
                ))}
            </SearchSelect>
          </label>
          <div className="flex flex-wrap gap-3">
            {selected &&
              preview.data?.dataset_id === selected.id &&
              preview.data.columns.map((c) => (
                <label key={c.name} className="flex gap-1">
                  <input
                    type="checkbox"
                    checked={selectedColumns.includes(c.name)}
                    onChange={(e) =>
                      setSelectedColumns(
                        e.target.checked
                          ? [...selectedColumns, c.name].slice(0, 50)
                          : selectedColumns.filter((v) => v !== c.name),
                      )
                    }
                  />
                  {c.name}
                </label>
              ))}
          </div>
        </section>
      </UiAttributes>

      <div className="mt-6">
        {datasetsQuery.isLoading && <DatasetTableSkeleton />}

        {datasetsQuery.isError && (
          <ErrorState
            {...friendlyApiError(datasetsQuery.error, uiCopyT)}
            onRetry={() => void datasetsQuery.refetch()}
          />
        )}

        {datasetsQuery.isSuccess && datasets.length === 0 && (
          <UiAttributes
            messages={{
              title: { id: "ui.data.noDatasetsYet" },
              description: {
                id: "ui.data.dropInACsvExcelWorkbookOrParquetFileStatforgeInfersTheSchemaAndProfilesEveryColumn",
              },
            }}
          >
            <EmptyState
              title={"No datasets yet"}
              description={
                "Drop in a CSV, Excel workbook or Parquet file — Quantelune infers the schema and profiles every column."
              }
              icon={DatabaseIcon}

              action={<UploadDialog projectId={projectId} />}
            />
          </UiAttributes>
        )}

        {datasetsQuery.isSuccess && datasets.length > 0 && (
          <div className="animate-rise overflow-hidden rounded-lg border bg-card shadow-plate">
            <div className="scrollbar-slim overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/50">
                    {(
                      [
                        "ui.common.file",
                        "data.table.format",
                        "ui.common.size",
                        "ui.profiling.rows",
                        "ui.common.cols",
                        "ui.projects.status",
                        "ui.common.uploaded",
                        null,
                      ] as const
                    ).map((heading, index) => (
                      <TableHead
                        key={heading || "actions"}
                        className={cn(
                          "font-mono text-[0.65rem] tracking-widest uppercase",
                          index >= 2 && index <= 4 && "text-right",
                          index === 7 && "text-right",
                        )}
                      >
                        {heading ? (
                          uiCopyT(heading)
                        ) : (
                          <span className="sr-only">
                            <UiMessage id="ui.projects.actions" />
                          </span>
                        )}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {datasets.map((dataset, index) => {
                    const status =
                      UPLOAD_STATUS_META[dataset.upload_status] ??
                      UPLOAD_STATUS_META.pending;
                    const isInFlight =
                      dataset.upload_status === "uploading" ||
                      dataset.upload_status === "validating";
                    return (
                      <TableRow
                        key={dataset.id}
                        className="animate-rise"
                        style={{
                          animationDelay: `${Math.min(index, 10) * 40}ms`,
                        }}
                      >
                        <TableCell>
                          <DesktopLink
                            href={`/projects/${projectId}/data/${dataset.id}`}
                            className="group flex items-center gap-2.5 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                          >
                            <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 transition-colors group-hover:bg-primary/20">
                              <FileSpreadsheetIcon className="size-4 text-primary" />
                            </span>
                            <span className="min-w-0">
                              <span className="block max-w-56 truncate text-sm font-medium group-hover:text-primary">
                                {dataset.original_filename}
                              </span>
                              <span className="block font-mono text-[0.65rem] text-muted-foreground">
                                {" "}
                                <UiMessage id="ui.data.sha256.dataClient" />{" "}
                                {dataset.sha256.slice(0, 10)}…
                              </span>
                            </span>
                          </DesktopLink>
                        </TableCell>
                        <TableCell>
                          <span className="rounded-sm border bg-muted/60 px-1.5 py-0.5 font-mono text-[0.65rem] font-medium tracking-wide">
                            {dataset.file_format.toUpperCase()}
                          </span>
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {formatBytes(dataset.file_size)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {formatNumber(dataset.row_count)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {formatNumber(dataset.column_count)}
                        </TableCell>
                        <TableCell>
                          <StatusBadge
                            label={localizedMetaLabel(status, uiLabelT)}
                            tone={status.tone}
                            pulse={isInFlight}
                          />
                          {dataset.upload_status === "failed" &&
                            dataset.error_message && (
                              <p className="mt-1 max-w-44 text-[0.65rem] text-destructive">
                                {dataset.error_message}
                              </p>
                            )}
                        </TableCell>
                        <TableCell className="font-mono text-xs whitespace-nowrap text-muted-foreground tabular-nums">
                          {formatDate(dataset.created_at)}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center justify-end gap-1">
                            <UiAttributes
                              messages={{
                                "aria-label": {
                                  id: "ui.data.previewValue",
                                  values: {
                                    value0: String(dataset.original_filename),
                                  },
                                },
                              }}
                            >
                              <DesktopLink
                                aria-label={"Preview {value0}".replaceAll(
                                  "{value0}",
                                  String(dataset.original_filename),
                                )}
                                href={`/projects/${projectId}/data/${dataset.id}`}

                                className={cn(
                                  buttonVariants({
                                    variant: "ghost",
                                    size: "icon-sm",
                                  }),
                                )}
                              >
                                <EyeIcon className="size-4" />
                              </DesktopLink>
                            </UiAttributes>
                            <DownloadButton
                              identity={dataset.id + dataset.sha256}
                              download={(signal) =>
                                datasetsApi.download(
                                  dataset.id,
                                  dataset.original_filename,
                                  signal,
                                )
                              }
                              ariaLabel={uiCopyT("ui.data.downloadValue", {
                                value0: dataset.original_filename,
                              })}
                              className={cn(
                                buttonVariants({
                                  variant: "ghost",
                                  size: "icon-sm",
                                }),
                              )}
                            >
                              <DownloadIcon className="size-4" />
                            </DownloadButton>
                            <UiAttributes
                              messages={{
                                "aria-label": {
                                  id: "ui.projects.deleteValue",
                                  values: {
                                    value0: String(dataset.original_filename),
                                  },
                                },
                              }}
                            >
                              <Button
                                aria-label={"Delete {value0}".replaceAll(
                                  "{value0}",
                                  String(dataset.original_filename),
                                )}
                                variant="ghost"
                                size="icon-sm"

                                className="text-muted-foreground hover:text-destructive"
                                onClick={() => setPendingDelete(dataset)}
                              >
                                <Trash2Icon className="size-4" />
                              </Button>
                            </UiAttributes>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
      </div>

      <UiAttributes messages={{ title: { id: "ui.data.deleteThisDataset" } }}>
        <ConfirmDialog
          title={"Delete this dataset?"}
          open={pendingDelete !== null}
          onOpenChange={(open) => {
            if (!open) setPendingDelete(null);
          }}

          description={
            pendingDelete && (
              <>
                <span className="font-medium text-foreground">
                  “{pendingDelete.original_filename}”
                </span>{" "}
                ({formatBytes(pendingDelete.file_size)}
                <UiMessage id="ui.data.willBePermanentlyRemovedFromThisProject" />{" "}
              </>
            )
          }
          confirmLabel="Delete dataset"
          isLoading={deleteMutation.isPending}
          onConfirm={() => {
            if (pendingDelete) deleteMutation.mutate(pendingDelete.id);
          }}
        />
      </UiAttributes>
    </div>
  );
}

function DatasetTableSkeleton() {
  return (
    <div
      className="animate-pulse overflow-hidden rounded-lg border bg-card"
      aria-hidden
    >
      <div className="h-11 border-b bg-muted/50" />
      {Array.from({ length: 3 }).map((_, index) => (
        <div
          key={index}
          className="flex items-center gap-6 border-b px-4 py-4 last:border-b-0"
        >
          <div className="flex items-center gap-3">
            <div className="size-8 rounded-md bg-muted" />
            <div className="h-4 w-40 rounded bg-muted/80" />
          </div>
          <div className="h-5 w-12 rounded-sm bg-muted/70" />
          <div className="ml-auto h-4 w-16 rounded bg-muted/60" />
          <div className="h-5 w-16 rounded-full bg-muted/70" />
          <div className="h-4 w-28 rounded bg-muted/60" />
        </div>
      ))}
    </div>
  );
}
