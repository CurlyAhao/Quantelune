"use client";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowRightIcon,
  FolderKanbanIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DesktopLink } from "@/components/desktop-link";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { projectsApi } from "@/lib/api-endpoints";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import {
  PROJECT_STATUS_META,
  friendlyApiError,
  taskTypeLabel,
} from "@/lib/format";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function ProjectsPage() {
  return (
    <UiAttributes messages={{ label: { id: "ui.projects.loadingProjects" } }}>
      <AuthGuard label={"Loading projects…"}>
        <ProjectsClient />
      </AuthGuard>
    </UiAttributes>
  );
}

function ProjectsClient() {
  const { t: uiCopyT } = useCopyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const { formatDate } = useLocaleDisplay();

  const router = useRouter();
  const queryClient = useQueryClient();
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => projectsApi.list(signal),
  });

  const deleteMutation = useMutation({
    mutationFn: (projectId: string) => projectsApi.remove(projectId),
    onSuccess: (_data, projectId) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.removeQueries({ queryKey: ["project", projectId] });
      setPendingDelete(null);
      toast.success(<UiMessage id="ui.projects.projectDeleted" />, {
        description: (
          <UiMessage id="ui.projects.itsDatasetsAndHistoryWereRemovedWithIt" />
        ),
      });
    },
    onError: (error) => {
      const friendly = friendlyApiError(error, uiCopyT);
      toast.error(friendly.title, { description: friendly.message });
      setPendingDelete(null);
    },
  });

  const projects = projectsQuery.data ?? [];

  return (
    <div className="mx-auto max-w-6xl px-4 pb-20 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4 pt-10">
        <div className="animate-rise space-y-1.5">
          <p className="font-mono text-xs tracking-[0.22em] text-primary uppercase">
            {" "}
            <UiMessage id="ui.projects.workspaceIndex" />{" "}
          </p>
          <h1 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">
            {" "}
            <UiMessage id="ui.dashboard.projects" />{" "}
          </h1>
          <p className="text-sm text-muted-foreground">
            {projectsQuery.isSuccess ? (
              <UiMessage
                id="ui.projects.valueValueOnTheBench"
                values={{
                  value0: String(projects.length),
                  value1: String(
                    projects.length === 1 ? "project" : "projects",
                  ),
                }}
              />
            ) : (
              <UiMessage id="ui.common.everyAnalysisLivesInsideAProject" />
            )}
          </p>
        </div>
        <Button
          size="lg"
          onClick={() => router.push("/projects/new")}
          className="animate-rise shadow-plate"
          style={{ animationDelay: "80ms" }}
        >
          <PlusIcon className="size-4" />{" "}
          <UiMessage id="ui.dashboard.newProject" />{" "}
        </Button>
      </div>

      <div className="mt-8">
        {projectsQuery.isLoading && <ProjectsTableSkeleton />}

        {projectsQuery.isError && (
          <ErrorState
            {...friendlyApiError(projectsQuery.error, uiCopyT)}
            onRetry={() => void projectsQuery.refetch()}
          />
        )}

        {projectsQuery.isSuccess && projects.length === 0 && (
          <UiAttributes
            messages={{
              title: { id: "ui.projects.noProjectsYet" },
              description: {
                id: "ui.projects.createAProjectToHoldYourDatasetsWorkflowsAndResults",
              },
            }}
          >
            <EmptyState
              title={"No projects yet"}
              description={
                "Create a project to hold your datasets, workflows and results."
              }
              icon={FolderKanbanIcon}

              action={
                <Button onClick={() => router.push("/projects/new")}>
                  <PlusIcon className="size-4" />{" "}
                  <UiMessage id="ui.dashboard.newProject" />{" "}
                </Button>
              }
            />
          </UiAttributes>
        )}

        {projectsQuery.isSuccess && projects.length > 0 && (
          <div className="animate-rise overflow-hidden rounded-lg border bg-card shadow-plate">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/50">
                  <TableHead className="font-mono text-[0.65rem] tracking-widest uppercase">
                    {" "}
                    <UiMessage id="ui.projects.project" />{" "}
                  </TableHead>
                  <TableHead className="font-mono text-[0.65rem] tracking-widest uppercase">
                    {" "}
                    <UiMessage id="ui.projects.taskType" />{" "}
                  </TableHead>
                  <TableHead className="font-mono text-[0.65rem] tracking-widest uppercase">
                    {" "}
                    <UiMessage id="ui.projects.status" />{" "}
                  </TableHead>
                  <TableHead className="font-mono text-[0.65rem] tracking-widest uppercase">
                    {" "}
                    <UiMessage id="ui.projects.rev" />{" "}
                  </TableHead>
                  <TableHead className="font-mono text-[0.65rem] tracking-widest uppercase">
                    {" "}
                    <UiMessage id="ui.projects.updated" />{" "}
                  </TableHead>
                  <TableHead className="text-right font-mono text-[0.65rem] tracking-widest uppercase">
                    <span className="sr-only">
                      <UiMessage id="ui.projects.actions" />
                    </span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projects.map((project, index) => {
                  const status = PROJECT_STATUS_META[project.status];
                  return (
                    <TableRow
                      key={project.id}
                      className="group animate-rise cursor-pointer"
                      style={{
                        animationDelay: `${Math.min(index, 10) * 40}ms`,
                      }}
                      onClick={() =>
                        navigateToAppRoute(
                          router,
                          `/projects/${project.id}/workspace`,
                        )
                      }
                    >
                      <TableCell>
                        <DesktopLink
                          href={`/projects/${project.id}/workspace`}
                          onClick={(event) => event.stopPropagation()}
                          className="font-medium outline-none group-hover:text-primary focus-visible:ring-3 focus-visible:ring-ring/50"
                        >
                          {project.title}
                        </DesktopLink>
                        {project.description && (
                          <p className="mt-0.5 line-clamp-1 max-w-md text-xs text-muted-foreground">
                            {project.description}
                          </p>
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {taskTypeLabel(project.task_type, uiLabelT)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          label={localizedMetaLabel(status, uiLabelT)}
                          tone={status.tone}
                          pulse={project.status === "active"}
                        />
                      </TableCell>
                      <TableCell className="font-mono text-xs tabular-nums">
                        {project.current_revision}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground tabular-nums">
                        {formatDate(project.updated_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <UiAttributes
                            messages={{
                              "aria-label": {
                                id: "ui.projects.openValue",
                                values: { value0: String(project.title) },
                              },
                            }}
                          >
                            <Button
                              aria-label={"Open {value0}".replaceAll(
                                "{value0}",
                                String(project.title),
                              )}
                              variant="ghost"
                              size="icon-sm"

                              onClick={(event) => {
                                event.stopPropagation();
                                navigateToAppRoute(
                                  router,
                                  `/projects/${project.id}/workspace`,
                                );
                              }}
                            >
                              <ArrowRightIcon className="size-4" />
                            </Button>
                          </UiAttributes>
                          <UiAttributes
                            messages={{
                              "aria-label": {
                                id: "ui.projects.deleteValue",
                                values: { value0: String(project.title) },
                              },
                            }}
                          >
                            <Button
                              aria-label={"Delete {value0}".replaceAll(
                                "{value0}",
                                String(project.title),
                              )}
                              variant="ghost"
                              size="icon-sm"

                              className="text-muted-foreground hover:text-destructive"
                              onClick={(event) => {
                                event.stopPropagation();
                                setPendingDelete(project);
                              }}
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
        )}
      </div>

      <UiAttributes
        messages={{ title: { id: "ui.projects.deleteThisProject" } }}
      >
        <ConfirmDialog
          title={"Delete this project?"}
          open={pendingDelete !== null}
          onOpenChange={(open) => {
            if (!open) setPendingDelete(null);
          }}

          description={
            pendingDelete && (
              <>
                <span className="font-medium text-foreground">
                  “{pendingDelete.title}”
                </span>{" "}
                <UiMessage id="ui.projects.andAllOfItsDatasetsWillBePermanentlyRemovedThisCannotBeUndone" />{" "}
              </>
            )
          }
          confirmLabel="Delete project"
          isLoading={deleteMutation.isPending}
          onConfirm={() => {
            if (pendingDelete) deleteMutation.mutate(pendingDelete.id);
          }}
        />
      </UiAttributes>
    </div>
  );
}

function ProjectsTableSkeleton() {
  return (
    <div
      className="animate-pulse overflow-hidden rounded-lg border bg-card"
      aria-hidden
    >
      <div className="h-11 border-b bg-muted/50" />
      {Array.from({ length: 5 }).map((_, index) => (
        <div
          key={index}
          className="flex items-center gap-6 border-b px-4 py-4 last:border-b-0"
        >
          <div
            className={cn(
              "h-4 rounded bg-muted",
              index % 2 ? "w-1/3" : "w-1/4",
            )}
          />
          <div className="h-4 w-1/5 rounded bg-muted/70" />
          <div className="h-5 w-16 rounded-full bg-muted/70" />
          <div className="ml-auto h-4 w-24 rounded bg-muted/60" />
        </div>
      ))}
    </div>
  );
}
