"use client";

import { useLanguage as useArrayLanguage } from "@/providers/language-provider";
import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3Icon,
  BotIcon,
  BracesIcon,
  DatabaseIcon,
  FileCheck2Icon,
  FolderKanbanIcon,
  GaugeIcon,
  GitBranchIcon,
  LayoutPanelTopIcon,
  MicroscopeIcon,
  ShieldCheckIcon,
  SparklesIcon,
  SearchIcon,
  WorkflowIcon,
} from "lucide-react";

import { DesktopLink } from "@/components/desktop-link";
import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { StatusBadge } from "@/components/status-badge";
import { PreviewWorkspace } from "@/components/preview-workspace";
import { ProjectRunLink } from "@/components/project-run-link";
import { ProjectFileControls } from "@/components/project-file-controls";
import { ModelSettingsLink } from "@/components/model-settings-link";
import { projectsApi, datasetsApi } from "@/lib/api-endpoints";
import { workspaceApi } from "@/lib/workspace-api";
import { useAuthStore } from "@/stores/auth-store";
import { PROJECT_STATUS_META, friendlyApiError } from "@/lib/format";
import { cn } from "@/lib/utils";

const _PROJECT_SECTIONS = [
  {
    href: "overview",
    label: "ui.profiling.overview",
    icon: LayoutPanelTopIcon,
  },
  { href: "data", label: "ui.common.data", icon: DatabaseIcon },
  { href: "materials", label: "ui.common.materials", icon: FileCheck2Icon },
  { href: "profiling", label: "ui.common.profiling", icon: MicroscopeIcon },
  { href: "quality", label: "ui.profiling.quality", icon: ShieldCheckIcon },
  { href: "workflow", label: "ui.common.workflow", icon: WorkflowIcon },
  { href: "statistics", label: "ui.common.statistics", icon: BarChart3Icon },
  { href: "machine-learning", label: "ui.common.ml", icon: GaugeIcon },
  {
    href: "evaluation",
    label: "workflow.node.evaluation.label",
    icon: FileCheck2Icon,
  },
  { href: "ai-review", label: "ui.common.aiReview", icon: SparklesIcon },
  { href: "agent", label: "ui.common.agent", icon: BotIcon },
  { href: "report", label: "workflow.node.report.label", icon: BracesIcon },
  { href: "versions", label: "ui.common.versions", icon: GitBranchIcon },
] as const;

interface ProjectShellProps {
  projectId: string;
  children: React.ReactNode;
  workspaceMode?: boolean;
  explorerContent?: React.ReactNode;
  explorerSummary?: React.ReactNode;
}

/**
 * Shared chrome for project sub-pages: breadcrumbs, project identity bar and
 * the section tab strip. Fetches the project once via react-query.
 */
export function ProjectShell({
  projectId,
  children,
  workspaceMode = false,
  explorerContent,
  explorerSummary,
}: ProjectShellProps) {
  const { t: arrayT } = useArrayLanguage();
  const PROJECT_SECTIONS = _PROJECT_SECTIONS.map((item) => ({
    ...item,
    label: arrayT(item.label),
  }));

  const { t: uiCopyT } = useCopyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const pathname = usePathname();
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [fileSearch, setFileSearch] = useState("");
  const userId = useAuthStore((s) => s.user?.id);
  const { locale } = useDomainLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const actorEpoch = useAuthStore((s) => s.actorEpoch);
  const objects = useQuery({
    queryKey: ["workspace-objects", actorEpoch, projectId],
    queryFn: ({ signal }) => workspaceApi.list(projectId, undefined, signal),
  });
  const datasets = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: ({ signal }) => projectsApi.get(projectId, signal),
  });

  useEffect(() => {
    if (!projectQuery.data || !userId) return;
    try {
      const key = `statforge.recent-projects.v1:${userId}`;
      const previous = JSON.parse(localStorage.getItem(key) ?? "{}");
      const entries =
        previous && typeof previous === "object" && !Array.isArray(previous)
          ? previous
          : {};
      localStorage.setItem(
        key,
        JSON.stringify({ ...entries, [projectQuery.data.id]: Date.now() }),
      );
    } catch {
      /* Recent ordering must not prevent opening a project. */
    }
  }, [projectQuery.data, userId]);

  if (projectQuery.isLoading) {
    return (
      <UiAttributes messages={{ label: { id: "ui.projects.loadingProject" } }}>
        <PageLoader label={"Loading project…"} />
      </UiAttributes>
    );
  }

  if (projectQuery.isError || !projectQuery.data) {
    const friendly = friendlyApiError(projectQuery.error, uiCopyT);
    return (
      <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
        <ErrorState
          title={friendly.title}
          message={friendly.message}
          onRetry={() => void projectQuery.refetch()}
        />
      </div>
    );
  }

  const project = projectQuery.data;
  const preparedParents = new Set(
    (datasets.data ?? []).map((d) => d.preparation_parent_id).filter(Boolean),
  );
  const datasetItems = Array.isArray(datasets.data)
    ? datasets.data.filter(
        (d) =>
          d &&
          typeof d.id === "string" &&
          d.project_id === projectId &&
          typeof d.original_filename === "string" &&
          (!d.preparation_steps || !preparedParents.has(d.id)),
      )
    : [];
  const status = PROJECT_STATUS_META[project.status];
  const base = `/projects/${project.id}`;

  return (
    <div className="project-workspace">
      {/* Breadcrumb */}
      <UiAttributes messages={{ "aria-label": { id: "ui.data.breadcrumb" } }}>
        <nav aria-label={"Breadcrumb"} className="workspace-breadcrumb">
          <Link
            href="/projects"
            className="flex items-center gap-1 rounded-sm transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <FolderKanbanIcon className="size-3.5" />{" "}
            <UiMessage id="ui.dashboard.projects" />{" "}
          </Link>
        </nav>
      </UiAttributes>

      {/* Identity bar */}
      <div className="workspace-identity">
        <h1 title={project.title}>{project.title}</h1>
        <StatusBadge
          label={localizedMetaLabel(status, uiLabelT)}
          tone={status.tone}
          pulse={project.status === "active"}
        />
      </div>

      <nav
        className="project-primary-nav"
        aria-label={copy("项目导航", "Project navigation")}
      >
        {[
          {
            href: "data",
            label: copy("数据", "Data"),
            active: /\/(data|prepare|profiling|quality|materials)(\/|$)/.test(
              pathname,
            ),
          },
          {
            href: "workspace",
            label: copy("分析", "Analysis"),
            active:
              /\/(workspace|statistics|machine-learning|workflow|evaluation|ai-review|agent)(\/|$)/.test(
                pathname,
              ),
          },
          {
            href: "results",
            label: copy("成果", "Results"),
            active: /\/(results|report)(\/|$)/.test(pathname),
          },
          {
            href: "history",
            label: copy("历史", "History"),
            active: /\/(history|versions)(\/|$)/.test(pathname),
          },
        ].map((item) => (
          <DesktopLink
            key={item.href}
            href={`${base}/${item.href}`}
            aria-current={item.active ? "page" : undefined}
          >
            {item.label}
          </DesktopLink>
        ))}
        <div className="project-run-utility">
          <ProjectRunLink
            projectId={projectId}
            active={/\/runs(\/|$)/.test(pathname)}
          />
        </div>
        <ProjectFileControls projectId={projectId} />
        <ModelSettingsLink
          className="project-settings-link"
          projectId={projectId}
        >
          {copy("设置", "Settings")}
        </ModelSettingsLink>
      </nav>
      <div className="project-workspace-grid">
        <UiAttributes
          messages={{
            "aria-label": { id: "ui.common.projectObjectsAndTools" },
          }}
        >
          <nav aria-label={"项目对象与工具"} className="object-explorer">
            <div className="project-explorer-heading">
              <FolderKanbanIcon className="size-5" aria-hidden />
              <div>
                <strong title={project.title}>{project.title}</strong>
                <span>
                  {explorerSummary ?? (
                    <>
                      {datasetItems.length} {copy("个数据文件", "data files")}
                    </>
                  )}
                </span>
              </div>
            </div>
            {!explorerContent && (
              <label className="explorer-search">
                <SearchIcon className="size-4" aria-hidden />
                <input
                  type="search"
                  aria-label={copy("查找文件", "Find a file")}
                  placeholder={copy("查找文件", "Find a file")}
                  value={fileSearch}
                  onChange={(event) => setFileSearch(event.target.value)}
                />
              </label>
            )}
            <button
              type="button"
              className="explorer-toggle explorer-entry"
              aria-expanded={explorerOpen}
              aria-controls="project-explorer-contents"
              onClick={() => setExplorerOpen(!explorerOpen)}
            >
              <FolderKanbanIcon className="size-3.5" />
              {explorerContent ? (
                explorerOpen ? (
                  copy("收起列表", "Collapse list")
                ) : (
                  copy("显示列表", "Show list")
                )
              ) : explorerOpen ? (
                <UiMessage id="ui.displayBranch.project.collapseObjectsTools" />
              ) : (
                <UiMessage id="ui.displayBranch.project.showObjectsTools" />
              )}
            </button>
            <div
              id="project-explorer-contents"
              className={cn("explorer-contents", explorerOpen && "is-open")}
            >
              {explorerContent ?? (
                <>
                  <DesktopLink
                    className="explorer-entry"
                    href={`${base}/workspace`}
                  >
                    {" "}
                    <UiMessage id="ui.common.continueInWorkspace" />{" "}
                  </DesktopLink>
                  <details open>
                    <summary>
                      <UiMessage id="ui.common.data" />
                    </summary>
                    <DesktopLink
                      className="explorer-entry"
                      href={`${base}/data`}
                    >
                      {" "}
                      <UiMessage id="ui.common.importManageData" />{" "}
                    </DesktopLink>
                    {datasetItems
                      .filter((d) =>
                        d.original_filename
                          .toLocaleLowerCase()
                          .includes(fileSearch.trim().toLocaleLowerCase()),
                      )
                      .map((d) => (
                        <DesktopLink
                          key={d.id}
                          className="explorer-entry"
                          href={`${base}/data?datasetId=${d.id}`}
                          title={d.original_filename}
                        >
                          <DatabaseIcon
                            className="size-4 shrink-0"
                            aria-hidden
                          />
                          <span className="explorer-file-text">
                            <strong>
                              {d.original_filename.split(/[\\/]/).pop()}
                              {(d.source_parent_id ||
                                datasetItems.some(
                                  (other) =>
                                    other.id !== d.id &&
                                    other.source_root_id === d.source_root_id,
                                )) &&
                                ` · v${d.source_version || 1}`}
                            </strong>
                            <small>
                              {d.row_count === null
                                ? d.file_format.toUpperCase()
                                : `${d.row_count} ${copy("行", "rows")} · ${d.column_count ?? "—"} ${copy("列", "columns")}`}
                            </small>
                          </span>
                        </DesktopLink>
                      ))}
                  </details>
                  <DesktopLink
                    className="explorer-entry"
                    href={`${base}/workspace?view=plot`}
                  >
                    {copy("绘图与代码", "Figure and code")}
                  </DesktopLink>
                  {(["analysis", "figure", "plot"] as const).map((kind) => (
                    <details key={kind} open>
                      <summary>
                        {kind === "plot" ? (
                          copy("Python 图形", "Python figures")
                        ) : kind === "analysis" ? (
                          <UiMessage id="ui.displayBranch.workspace.analysis" />
                        ) : (
                          <UiMessage id="ui.displayBranch.workspace.works" />
                        )}
                      </summary>
                      {(objects.data ?? [])
                        .filter((o) => o.kind === kind && o.available)
                        .map((o) => (
                          <DesktopLink
                            className="explorer-entry"
                            key={o.id}
                            href={
                              kind === "plot"
                                ? `${base}/workspace?view=plot&plotId=${o.id}`
                                : `${base}/workspace?object=${o.id}`
                            }
                            title={o.title}
                          >
                            {o.title}
                          </DesktopLink>
                        ))}
                      {!(objects.data ?? []).some(
                        (o) => o.kind === kind && o.available,
                      ) && (
                        <p className="explorer-empty">
                          <UiMessage id="ui.common.noSavedObjectsYet" />
                        </p>
                      )}
                    </details>
                  ))}
                  <DesktopLink
                    className="explorer-entry"
                    href={`${base}/materials`}
                  >
                    {" "}
                    <UiMessage id="ui.common.materialsAndCitations" />{" "}
                  </DesktopLink>
                  <details>
                    <summary>
                      <UiMessage id="ui.common.methodsAndProjectTools" />
                    </summary>
                    {PROJECT_SECTIONS.map(({ href, label, icon: Icon }) => {
                      const target = `${base}/${href}`;
                      const isActive = pathname.startsWith(target);
                      return (
                        <DesktopLink
                          key={href}
                          href={target}
                          aria-current={isActive ? "page" : undefined}
                          className={cn(
                            "explorer-entry",
                            isActive && "text-primary",
                          )}
                        >
                          <Icon className="size-3.5" />
                          {label}
                        </DesktopLink>
                      );
                    })}
                  </details>
                </>
              )}
            </div>
          </nav>
        </UiAttributes>
        <div className="workspace-content">
          {workspaceMode ? (
            children
          ) : (
            <PreviewWorkspace
              key={`${actorEpoch}:${projectId}:${pathname}`}
              projectId={projectId}
              route={pathname}
            >
              {children}
            </PreviewWorkspace>
          )}
        </div>
      </div>
    </div>
  );
}
