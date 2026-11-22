"use client";

import { projectDirectory } from "@/components/project-disambiguation";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRightIcon,
  MoreHorizontalIcon,
  FolderOpenIcon,
  PlusIcon,
  SearchIcon,
} from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { DesktopLink } from "@/components/desktop-link";
import { ErrorState } from "@/components/error-state";
import { ProjectFormDialog } from "@/components/project-form-dialog";
import { FileImport } from "@/components/file-import";
import { Button } from "@/components/ui/button";
import { projectsApi } from "@/lib/api-endpoints";
import { friendlyApiError } from "@/lib/format";
import { useLanguage } from "@/providers/language-provider";
import { useAuthStore } from "@/stores/auth-store";
import { useRouter } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";

export function ResearchHome() {
  const { t, locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const router = useRouter();
  const userId = useAuthStore((s) => s.user?.id);
  const [recent, setRecent] = useState<Record<string, number>>({});
  const [search, setSearch] = useState("");
  const [create, setCreate] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const [projectError, setProjectError] = useState("");
  const [updating, setUpdating] = useState("");
  async function setListed(id: string, listed: boolean) {
    setUpdating(id); setProjectError("");
    try { await projectsApi.update(id, {status:listed?"active":"archived"}); await projects.refetch(); }
    catch(reason) { setProjectError(reason instanceof Error ? reason.message : "无法更新项目列表，请重试。"); }
    finally { setUpdating(""); }
  }
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => projectsApi.list(signal),
  });
  useEffect(() => {
    setRecent({});
    try {
      const value = JSON.parse(
        localStorage.getItem(`statforge.recent-projects.v1:${userId}`) ?? "{}",
      );
      if (value && typeof value === "object" && !Array.isArray(value))
        setRecent(
          Object.fromEntries(
            Object.entries(value).filter(
              ([, time]) => typeof time === "number" && Number.isFinite(time),
            ) as [string, number][],
          ),
        );
    } catch {
      /* The project list remains usable without device storage. */
    }
  }, [userId]);
  const items = [...(projects.data ?? [])].sort(
    (a, b) =>
      (recent[b.id] ?? Date.parse(b.updated_at)) -
      (recent[a.id] ?? Date.parse(a.updated_at)),
  );
  const filtered = items.filter((p) =>
    (showRemoved ? p.status === "archived" : p.status !== "archived") && p.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  return (
    <div className="research-home local-project-home">
      <section className="entry-research" aria-labelledby="entry-heading">
        <h1 id="entry-heading">
          {copy(
            "今天从哪个项目开始？",
            "Which project will you work on today?",
          )}
        </h1>
        <div className="home-open-card">
          <div className="home-folder-mark" aria-hidden>
            <FolderOpenIcon />
            <span>
              <PlusIcon />
            </span>
          </div>
          <h2>{copy("选择数据文件", "Choose your data")}</h2>
          <FileImport
            home
            onImported={(projectId, datasetId) =>
              navigateToAppRoute(
                router,
                `/projects/${projectId}/workspace${datasetId ? `?datasetId=${datasetId}` : ""}`,
              )
            }
          />
          <Button
            className="home-new-button"
            variant="ghost"
            onClick={() => setCreate(true)}
          >
            {copy("新建项目", "New project")}
          </Button>
        </div>
        <section className="home-file-guide" aria-labelledby="home-file-guide-heading">
          <h2 id="home-file-guide-heading">{copy("常见文件与分析", "What you can do with your files")}</h2>
          <dl>
            {[
              [
                copy("表格与记录", "Tables and records"),
                copy("CSV、Excel（.xlsx）、Parquet、记录型 JSON", "CSV, Excel (.xlsx), Parquet, record-oriented JSON"),
                copy("描述统计、组间比较、回归、机器学习与绘图", "Run descriptive statistics, group comparisons, regression and machine learning, or create plots"),
              ],
              [
                copy("文本与文档", "Text and documents"),
                copy("TXT、Markdown、带文字层的 PDF", "TXT, Markdown, PDFs with selectable text"),
                copy("标注原文，按主题编码并汇总", "Annotate passages, code them by theme and review coded excerpts"),
              ],
              [
                copy("音频与视频", "Audio and video"),
                "WAV · MP4",
                copy("转录与校对、分析音高与强度、视频抽帧", "Transcribe and review speech, measure pitch and intensity, and extract video frames"),
              ],
              [
                copy("图片", "Images"), "PNG · JPEG",
                copy("标注图像区域，比较图片相似度", "Annotate regions and compare image similarity"),
              ],
              [
                copy("多路信号", "Signals"),
                copy("BIDMC 数据文件夹、已打包的信号窗口（.pkl）", "BIDMC folders, packed signal windows (.pkl)"),
                copy("对照多路波形，按时间切窗并查看摘要", "Compare waveforms, split by time and inspect window summaries"),
              ],
            ].map(([type, formats, analyses]) => (
              <div key={type}>
                <dt>{type}<span>{formats}</span></dt>
                <dd>{analyses}</dd>
              </div>
            ))}
          </dl>
        </section>
      </section>
      <section
        className="entry-projects"
        aria-labelledby="entry-projects-heading"
      >
        <div className="entry-projects-heading">
          <h2 id="entry-projects-heading">
            {copy("现有项目", "Existing projects")}
          </h2>
          <span className="entry-help">
            {projects.isSuccess
              ? `${items.length} ${copy("个项目", "projects")}`
              : ""}
          </span>
        </div>
        <label className="entry-search">
          <SearchIcon className="size-4" aria-hidden />
          <span className="sr-only">{t("entry.searchProjects")}</span>
          <input
            id="home-project-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("entry.searchProjects")}
          />
        </label>
        {projects.isPending && (
          <p role="status">{t("entry.loadingProjects")}</p>
        )}
        {projects.isError && (
          <ErrorState
            {...friendlyApiError(projects.error, t)}
            onRetry={() => void projects.refetch()}
          />
        )}
        {projects.isSuccess && filtered.length === 0 && (
          <div className="home-empty">
            <p>
              {copy(
                items.length ? "没有找到这个项目" : "还没有项目",
                items.length ? "No matching projects" : "No projects yet",
              )}
            </p>
            {!items.length && (
              <Button variant="outline" onClick={() => setCreate(true)}>
                {copy("新建第一个项目", "Create your first project")}
              </Button>
            )}
          </div>
        )}
        <ul className="entry-project-list">
          {filtered.map((p) => (
            <li key={p.id}>
              <DesktopLink
                href={`/projects/${p.id}/workspace`}
                className="entry-project-continue"
              >
                <FolderOpenIcon
                  className="size-5 text-primary shrink-0"
                  aria-hidden
                />
                <span>
                  <strong title={p.title}>{p.title}</strong>
                  <time
                    className="entry-help"
                    style={projectDirectory(p, items) ? { whiteSpace: "normal", overflowWrap: "anywhere" } : undefined}
                    dateTime={new Date(
                      recent[p.id] ?? p.updated_at,
                    ).toISOString()}
                  >
                    {projectDirectory(p, items) && `${projectDirectory(p, items)} · `}
                    {copy(
                      recent[p.id] ? "最近打开 " : "更新于 ",
                      recent[p.id] ? "Last opened " : "Updated ",
                    )}
                    {new Date(recent[p.id] ?? p.updated_at).toLocaleDateString(
                      locale,
                    )}
                  </time>
                </span>
                <ArrowRightIcon className="size-4 shrink-0" aria-hidden />
              </DesktopLink>
              <DropdownMenu>
                <DropdownMenuTrigger aria-label={copy("管理项目", "Manage project") + " · " + p.title} title={copy("管理项目", "Manage project")} className="entry-project-options"><MoreHorizontalIcon size={17}/></DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-36">
                  <DropdownMenuItem disabled={!!updating} onClick={() => void setListed(p.id,showRemoved)} title={showRemoved ? undefined : copy("保留本地文件和研究结果", "Keep local files and results")}>
                    {showRemoved ? copy("恢复到项目列表", "Restore to list") : copy("从项目列表移除", "Remove from list")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ))}
        </ul>
        {projectError && <p role="alert">{projectError}</p>}
        <button type="button" className="entry-project-remove" onClick={()=>setShowRemoved(!showRemoved)}>{showRemoved ? copy("返回现有项目", "Back to projects") : copy("已移除的项目", "Removed projects")}</button>
      </section>
      <ProjectFormDialog
        open={create}
        onOpenChange={setCreate}
        onCreated={(p) =>
          navigateToAppRoute(router, `/projects/${p.id}/workspace`)
        }
      />
    </div>
  );
}
