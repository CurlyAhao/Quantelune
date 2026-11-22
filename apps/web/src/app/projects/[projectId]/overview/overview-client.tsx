"use client";

import { useLanguage as usePropertyLanguage } from "@/providers/language-provider";
import { FriendlyErrorMessage as UiError } from "@/components/friendly-error";

import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronRightIcon,
  DatabaseIcon,
  FileUpIcon,
  ListChecksIcon,
  MicroscopeIcon,
  WorkflowIcon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { DesktopLink } from "@/components/desktop-link";
import { ProjectShell } from "@/components/project-shell";
import { StatusBadge } from "@/components/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { datasetsApi, projectsApi } from "@/lib/api-endpoints";
import { UPLOAD_STATUS_META } from "@/lib/format";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function OverviewClient({ projectId }: { projectId: string }) {
  return (
    <UiAttributes messages={{ label: { id: "ui.projects.loadingProject" } }}>
      <AuthGuard label={"Loading project…"}>
        <ProjectShell projectId={projectId}>
          <OverviewBody projectId={projectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

function OverviewBody({ projectId }: { projectId: string }) {
  const { t: propertyT } = usePropertyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const { formatDate, formatBytes } = useLocaleDisplay();

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: ({ signal }) => projectsApi.get(projectId, signal),
  });

  const datasetsQuery = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });

  const project = projectQuery.data as Project | undefined;
  const datasets = datasetsQuery.data ?? [];
  const totalBytes = datasets.reduce((sum, d) => sum + d.file_size, 0);

  const nextSteps = [
    {
      label: propertyT("ui.projects.uploadADataset"),
      detail: propertyT("ui.common.csvXlsxParquetUpTo100Mb"),
      href: `/projects/${projectId}/data`,
      icon: FileUpIcon,
      done: datasets.length > 0,
    },
    {
      label: propertyT("ui.projects.inspectTheSchemaProfile"),
      detail: propertyT("ui.common.typesNullRatesAndSampleValues"),
      href: `/projects/${projectId}/data`,
      icon: MicroscopeIcon,
      done: datasets.some((d) => d.upload_status === "ready"),
    },
    {
      label: propertyT("ui.projects.buildAnAnalysisWorkflow"),
      detail: propertyT("ui.common.transformationsStatisticsMl"),
      href: `/projects/${projectId}/workflow`,
      icon: WorkflowIcon,
      done: false,
    },
  ];

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      {/* Main column */}
      <div className="space-y-6 lg:col-span-2">
        <section
          aria-labelledby="about-heading"
          className="animate-rise rounded-lg border bg-card p-5 shadow-plate sm:p-6"
        >
          <h2
            id="about-heading"
            className="font-mono text-xs tracking-[0.22em] text-primary uppercase"
          >
            {" "}
            <UiMessage id="ui.projects.aboutThisProject" />{" "}
          </h2>
          <div className="mt-4 space-y-4 text-sm leading-relaxed">
            <p
              className={
                project?.description ? "" : "text-muted-foreground italic"
              }
            >
              {project?.description ?? (
                <UiMessage id="ui.common.noDescriptionYetTheStoryOfThisAnalysisIsStillUnwritten" />
              )}
            </p>

            {project?.research_question && (
              <blockquote className="border-l-2 border-accent bg-accent/8 py-2 pr-3 pl-4">
                <p className="font-mono text-[0.65rem] tracking-widest text-accent-foreground uppercase">
                  {" "}
                  <UiMessage id="ui.projects.researchQuestion" />{" "}
                </p>
                <p className="mt-1 text-foreground">
                  {project.research_question}
                </p>
              </blockquote>
            )}
          </div>

          <Separator className="my-5" />

          <dl className="grid grid-cols-2 gap-4 font-mono text-xs sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground uppercase">
                <UiMessage id="ui.projects.created" />
              </dt>
              <dd className="mt-1 tabular-nums">
                {formatDate(project?.created_at)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground uppercase">
                <UiMessage id="ui.projects.updated" />
              </dt>
              <dd className="mt-1 tabular-nums">
                {formatDate(project?.updated_at)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground uppercase">
                <UiMessage id="ui.projects.revision" />
              </dt>
              <dd className="mt-1 tabular-nums">
                {project?.current_revision ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground uppercase">
                <UiMessage id="ui.data.datasets" />
              </dt>
              <dd className="mt-1 tabular-nums">
                {datasetsQuery.isLoading
                  ? "…"
                  : `${datasets.length} · ${formatBytes(totalBytes)}`}
              </dd>
            </div>
          </dl>
        </section>

        {/* Datasets snapshot */}
        <section
          aria-labelledby="datasets-heading"
          className="animate-rise rounded-lg border bg-card p-5 shadow-plate sm:p-6"
          style={{ animationDelay: "80ms" }}
        >
          <div className="flex items-center justify-between gap-3">
            <h2
              id="datasets-heading"
              className="flex items-center gap-2 font-mono text-xs tracking-[0.22em] text-primary uppercase"
            >
              <DatabaseIcon className="size-4" />{" "}
              <UiMessage id="ui.data.datasets" />{" "}
            </h2>
            <DesktopLink
              href={`/projects/${projectId}/data`}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              <FileUpIcon className="size-3.5" />{" "}
              <UiMessage id="ui.projects.manageData" />{" "}
            </DesktopLink>
          </div>

          <div className="mt-4">
            {datasetsQuery.isLoading && (
              <div className="space-y-2" aria-hidden>
                {Array.from({ length: 2 }).map((_, index) => (
                  <div
                    key={index}
                    className="h-12 animate-pulse rounded-md bg-muted/70"
                  />
                ))}
              </div>
            )}

            {datasetsQuery.isError && (
              <p className="text-sm text-destructive">
                {<UiError error={datasetsQuery.error} part="message" />}
              </p>
            )}

            {datasetsQuery.isSuccess && datasets.length === 0 && (
              <p className="rounded-md border border-dashed bg-muted/40 px-4 py-6 text-center text-sm text-muted-foreground">
                {" "}
                <UiMessage id="ui.projects.noDataOnTheBenchYetUploadACsvExcelOrParquetFileToBegin" />{" "}
              </p>
            )}

            {datasetsQuery.isSuccess && datasets.length > 0 && (
              <ul className="divide-y">
                {datasets.slice(0, 5).map((dataset) => {
                  const status =
                    UPLOAD_STATUS_META[dataset.upload_status] ??
                    UPLOAD_STATUS_META.pending;
                  return (
                    <li key={dataset.id}>
                      <DesktopLink
                        href={`/projects/${projectId}/data/${dataset.id}`}
                        className="flex items-center gap-3 rounded-md px-2 py-2.5 transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                      >
                        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10">
                          <DatabaseIcon className="size-3.5 text-primary" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">
                            {dataset.original_filename}
                          </span>
                          <span className="block font-mono text-[0.65rem] text-muted-foreground tabular-nums">
                            {formatBytes(dataset.file_size)} ·{" "}
                            {dataset.row_count ?? "—"}{" "}
                            <UiMessage id="ui.data.rows" />{" "}
                          </span>
                        </span>
                        <StatusBadge
                          label={localizedMetaLabel(status, uiLabelT)}
                          tone={status.tone}
                          pulse={
                            dataset.upload_status === "uploading" ||
                            dataset.upload_status === "validating"
                          }
                        />
                      </DesktopLink>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>
      </div>

      {/* Side column */}
      <div className="space-y-6">
        <section
          aria-labelledby="next-steps-heading"
          className="animate-rise rounded-lg border bg-card p-5 shadow-plate"
          style={{ animationDelay: "120ms" }}
        >
          <h2
            id="next-steps-heading"
            className="flex items-center gap-2 font-mono text-xs tracking-[0.22em] text-primary uppercase"
          >
            <ListChecksIcon className="size-4" />{" "}
            <UiMessage id="ui.projects.nextSteps" />{" "}
          </h2>
          <ol className="mt-4 space-y-1">
            {nextSteps.map((step, index) => (
              <li key={step.label}>
                <DesktopLink
                  href={step.href}
                  className="group flex items-start gap-3 rounded-md p-2.5 transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <span
                    className={`mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full font-mono text-[0.65rem] font-semibold ${
                      step.done
                        ? "bg-primary text-primary-foreground"
                        : "border bg-muted text-muted-foreground"
                    }`}
                  >
                    {step.done ? "✓" : index + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium group-hover:text-primary">
                      {step.label}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {step.detail}
                    </span>
                  </span>
                  <ChevronRightIcon className="mt-1 size-3.5 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5" />
                </DesktopLink>
              </li>
            ))}
          </ol>
        </section>

        <p
          className="animate-rise px-1 font-mono text-[0.65rem] leading-relaxed text-muted-foreground"
          style={{ animationDelay: "160ms" }}
        >
          {" "}
          <UiMessage id="ui.projects.everyChangeInStatforgeIsVersionedTheRevisionCounterOnThisProjectIncrementsAsAnalysisArtifactsAreCommitted" />{" "}
        </p>
      </div>
    </div>
  );
}
