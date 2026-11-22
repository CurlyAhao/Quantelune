"use client";

import { SearchSelect } from "@/components/ui/search-select";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  PlusIcon,
  Trash2Icon,
  ExternalLinkIcon,
} from "lucide-react";
import { projectsApi } from "@/lib/api-endpoints";
import { useReportDocument } from "@/lib/use-report-document";
import {
  projectResearchApi,
  historyKindLabel,
  sourceHistoryHref,
  type HistoryItem,
} from "@/lib/project-research";
import type {
  ReportBlock,
  ReportDocument,
  ReportSourceKind,
} from "@/lib/workspace-api";
import { workspaceApi } from "@/lib/workspace-api";
import { saveGeneratedFile } from "@/lib/api-download";
import { reportFigure, reportHtml, reportMarkdown } from "@/lib/report-output";
import {
  displayReportBlock,
  externalReportFrame,
} from "@/lib/report-source-frame";
import { useLanguage } from "@/providers/language-provider";
import { useWorkspacePreferences } from "@/providers/workspace-preferences-provider";
import { usePreviewPublication } from "./preview-workspace";
import { ProjectShell } from "./project-shell";
import { Button } from "./ui/button";
import { DesktopLink } from "./desktop-link";
import { DownloadButton } from "./download-button";
import { ReportSourceView } from "./report-source-view";
import { ResearchFailure } from "./formal-research-common";

export function ProjectResults({
  projectId,
  initialId,
}: {
  projectId: string;
  initialId?: string | null;
}) {
  const { locale } = useLanguage();
  const router = useRouter();
  const preferences = useWorkspacePreferences();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const report = useReportDocument(projectId, initialId);
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const undo = useRef<ReportDocument[]>([]);
  const assets = useQuery({
    queryKey: ["report-assets", projectId, filter, offset],
    queryFn: ({ signal }) =>
      projectResearchApi.history(
        projectId,
        { q: filter, offset, limit: 20 },
        signal,
        true,
      ),
    retry: false,
  });
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: ({ signal }) => projectsApi.get(projectId, signal),
  });
  const sources = useQuery({
    queryKey: [
      "report-sources",
      projectId,
      report.saved?.id,
      JSON.stringify(
        report.saved?.payload.blocks.map((block) => [block.id, block.source]),
      ),
    ],
    queryFn: ({ signal }) =>
      projectResearchApi.reportSources(
        projectId,
        report.saved!.id,
        report.saved!.revision,
        signal,
      ),
    enabled: !!report.saved,
    retry: false,
  });
  function change(next: ReportDocument, remember = false) {
    if (remember) {
      undo.current = [...undo.current.slice(-19), report.peek()];
    }
    report.update(next);
  }
  function patch(id: string, value: Partial<ReportBlock>) {
    const current = report.peek();
    change({
      ...current,
      blocks: current.blocks.map((block) =>
        block.id === id ? { ...block, ...value } : block,
      ),
    });
  }
  async function perform(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      await work();
    } catch (error) {
      setFailure(error);
    } finally {
      setBusy(false);
    }
  }
  async function add(item: HistoryItem) {
    const existing = report.value.blocks.find(
      (block) =>
        block.source?.kind === item.kind && block.source?.id === item.id,
    );
    if (existing) {
      document
        .getElementById(`report-block-${existing.id}`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    await perform(async () => {
      const source = await projectResearchApi.source(
        projectId,
        item.kind,
        item.id,
        item.reference?.revision,
      );
      if (source.issue_code)
        throw new Error(
          copy("这份旧结果需要重新计算", "Recalculate this legacy result"),
        );
      if (source.reference.kind === "figure")
        reportFigure(
          {
            id: source.reference.id,
            title: "",
            body: "",
            caption: "",
            source: source.reference,
            source_snapshot: source.payload,
          },
          locale,
        );
      const current = report.peek();
      change(
        {
          ...current,
          blocks: [
            ...current.blocks,
            {
              id: crypto.randomUUID(),
              title: item.title,
              body: "",
              caption: "",
              source: source.reference,
              source_snapshot: source.payload,
            },
          ],
        },
        true,
      );
    });
  }
  function move(index: number, step: number) {
    const blocks = [...report.value.blocks];
    [blocks[index], blocks[index + step]] = [
      blocks[index + step],
      blocks[index],
    ];
    change({ ...report.value, blocks }, true);
  }
  async function exportReport(
    format: "html" | "md" | "json",
    signal: AbortSignal,
  ) {
    const saved = await report.flush();
    if (!saved) throw new Error("Report is empty");
    const resolved = await projectResearchApi.reportSources(
      projectId,
      saved.id,
      saved.revision,
      signal,
    );
    const snapshot = {
      ...saved.payload,
      blocks: saved.payload.blocks.map((block) => {
        const visible = displayReportBlock(block, resolved[block.id]);
        if (
          block.source &&
          externalReportFrame(block) &&
          visible.source_snapshot === null
        )
          throw new Error(
            copy(
              "来源暂时无法读取，请核对后再导出。",
              "A source is unavailable. Check it before exporting.",
            ),
          );
        return visible;
      }),
    };
    const text =
      format === "html"
        ? reportHtml(snapshot, locale, project.data?.title ?? "")
        : format === "md"
          ? reportMarkdown(snapshot, locale)
          : JSON.stringify(
              {
                project_id: projectId,
                record_id: saved.id,
                revision: saved.revision,
                document: snapshot,
              },
              null,
              2,
            );
    return saveGeneratedFile(
      new Blob([text], {
        type:
          format === "html"
            ? "text/html;charset=utf-8"
            : format === "md"
              ? "text/markdown;charset=utf-8"
              : "application/json;charset=utf-8",
      }),
      `${snapshot.title}.${format}`,
      signal,
      async () => {
        const fresh = await workspaceApi.read(saved.id, signal, saved.revision);
        if (JSON.stringify(fresh.payload) !== JSON.stringify(saved.payload))
          throw new Error("Saved report changed");
      },
      locale,
      { browserDownload: true },
    );
  }
  const assetsPanel = (
    <section
      className="research-assets"
      aria-label={copy("研究素材", "Research sources")}
    >
      <h2>{copy("研究素材", "Research sources")}</h2>
      <input
        type="search"
        aria-label={copy("查找研究素材", "Find sources")}
        placeholder={copy("查找结果或图形…", "Find results or figures…")}
        value={filter}
        onChange={(event) => {
          setFilter(event.target.value);
          setOffset(0);
        }}
      />
      {assets.isPending && (
        <p role="status">{copy("读取素材…", "Loading sources…")}</p>
      )}
      {assets.isError && (
        <ResearchFailure
          error={assets.error}
          retry={() => {
            void assets.refetch();
          }}
        />
      )}
      {assets.isSuccess && !assets.data.items.length && (
        <p>
          {copy(
            "分析完成后，可在这里选入结果。",
            "Select completed results here.",
          )}
        </p>
      )}
      <div className="research-asset-list">
        {assets.data?.items.map((item) => {
          const selected = report.value.blocks.some(
            (block) =>
              block.source?.kind === item.kind && block.source.id === item.id,
          );
          return (
            <div
              key={`${item.kind}:${item.id}`}
              className={selected ? "is-selected" : ""}
            >
              <button
                disabled={busy || !report.ready || !item.available}
                onClick={() => void add(item)}
                aria-pressed={selected}
              >
                <strong>{item.title}</strong>
                <small>
                  {historyKindLabel(item.kind, locale)}
                  {item.filename ? ` · ${item.filename}` : ""}
                </small>
                {item.archived && (
                  <small>{copy("项目存档", "Project archive")}</small>
                )}
                {!item.available && (
                  <small>
                    {copy(
                      item.issue_code ? "需要重新计算" : "来源暂不可用",
                      item.issue_code
                        ? "Recalculate this result"
                        : "Source unavailable",
                    )}
                  </small>
                )}
              </button>
              <DesktopLink
                href={sourceHistoryHref(projectId, {
                  kind: item.kind as ReportSourceKind,
                  id: item.id,
                  revision: item.revision,
                  sha256: item.reference?.sha256 ?? "",
                })}
              >
                {copy("查看来源", "View source")}
                <ExternalLinkIcon className="size-3" />
              </DesktopLink>
            </div>
          );
        })}
      </div>
      {assets.data && assets.data.total > 20 && (
        <div className="research-pagination">
          <Button
            variant="ghost"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            {copy("上一页", "Previous")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={offset + 20 >= assets.data.total}
            onClick={() => setOffset(offset + 20)}
          >
            {copy("下一页", "Next")}
          </Button>
        </div>
      )}
      <DesktopLink
        className="entry-manual"
        href={`/projects/${projectId}/materials`}
      >
        {copy("材料与引用", "Materials and citations")}
      </DesktopLink>
    </section>
  );
  return (
    <ProjectShell projectId={projectId} explorerContent={assetsPanel}>
      <section className="results-workbench">
        <div className="results-toolbar">
          <h2>{copy("研究成果", "Research report")}</h2>
          <SearchSelect
            aria-label={copy("选择成果文档", "Choose a report")}
            value={report.saved?.id ?? ""}
            disabled={busy || !report.ready}
            onChange={(event) =>
              void perform(async () => {
                await report.choose(event.target.value);
                undo.current = [];
              })
            }
          >
            <option value="">{copy("新的成果草稿", "New report draft")}</option>
            {report.choices.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </SearchSelect>
          <Button
            variant="ghost"
            disabled={busy || !report.ready}
            onClick={() =>
              void perform(async () => {
                await report.choose("");
                undo.current = [];
              })
            }
          >
            <PlusIcon className="size-4" />
            {copy("新建成果", "New report")}
          </Button>
          <Button
            variant="outline"
            onClick={() => setEditing(!editing)}
            aria-pressed={!editing}
          >
            {copy(editing ? "预览" : "编辑", editing ? "Preview" : "Edit")}
          </Button>
        </div>
        <div className="report-document-tools">
          <span role="status">
            {report.error
              ? copy("未保存", "Not saved")
              : report.dirty
                ? copy("保存中…", "Saving…")
                : report.saved
                  ? copy("已保存", "Saved")
                  : copy("成果草稿", "Report draft")}
          </span>
          <Button
            variant="ghost"
            disabled={!report.ready || busy}
            onClick={() =>
              change(
                {
                  ...report.value,
                  blocks: [
                    ...report.value.blocks,
                    {
                      id: crypto.randomUUID(),
                      title: copy("新的段落", "New section"),
                      body: "",
                      caption: "",
                      source: null,
                      source_snapshot: null,
                    },
                  ],
                },
                true,
              )
            }
          >
            {copy("添加段落", "Add section")}
          </Button>
          <Button
            variant="ghost"
            disabled={!undo.current.length || busy}
            onClick={() => {
              const previous = undo.current.pop();
              if (previous) report.update(previous);
            }}
          >
            {copy("撤销", "Undo")}
          </Button>
          {report.ready && report.value.blocks.length > 0 && (
            <DownloadButton
              identity={`${projectId}:report:preferred:${preferences.value.reportFormat}`}
              download={(signal) =>
                exportReport(preferences.value.reportFormat, signal)
              }
            >
              {copy("导出", "Export")}{" "}
              {preferences.value.reportFormat === "md"
                ? "Markdown"
                : preferences.value.reportFormat.toUpperCase()}
            </DownloadButton>
          )}
          {report.ready && report.value.blocks.length > 0 && (
            <details className="report-export">
              <summary>{copy("其他格式", "Other formats")}</summary>
              <div>
                {(["html", "md", "json"] as const)
                  .filter((format) => format !== preferences.value.reportFormat)
                  .map((format) => (
                    <DownloadButton
                      key={format}
                      identity={`${projectId}:report:${format}`}
                      download={(signal) => exportReport(format, signal)}
                    >
                      {format === "json"
                        ? copy("成果与来源 JSON", "Report and sources JSON")
                        : format.toUpperCase()}
                    </DownloadButton>
                  ))}
              </div>
            </details>
          )}
        </div>
        {!report.ready && !report.error && (
          <p role="status">{copy("打开成果…", "Opening report…")}</p>
        )}
        {report.error != null && (
          <ResearchFailure
            error={report.error}
            retry={() => {
              if (!report.ready) window.location.reload();
              else void report.flush().catch(() => undefined);
            }}
          />
        )}
        {failure != null && <ResearchFailure error={failure} />}
        {report.ready && (
          <article className="report-paper" inert={busy}>
            <header>
              {editing ? (
                <>
                  <input
                    className="report-title"
                    aria-label={copy("成果标题", "Report title")}
                    value={report.value.title}
                    maxLength={160}
                    onChange={(event) =>
                      change({ ...report.value, title: event.target.value })
                    }
                  />
                  <textarea
                    className="report-subtitle"
                    aria-label={copy("导语", "Introduction")}
                    placeholder={copy("添加导语…", "Add an introduction…")}
                    value={report.value.subtitle}
                    maxLength={2000}
                    onChange={(event) =>
                      change({ ...report.value, subtitle: event.target.value })
                    }
                  />
                </>
              ) : (
                <>
                  <h1>{report.value.title}</h1>
                  {report.value.subtitle && <p>{report.value.subtitle}</p>}
                </>
              )}
            </header>
            {!report.value.blocks.length && (
              <div className="report-empty">
                <h3>{copy("从一项发现开始", "Start with a finding")}</h3>
                <p>
                  {copy(
                    "选入左侧素材，或添加一段正文。",
                    "Choose a source or add a section.",
                  )}
                </p>
              </div>
            )}
            {report.value.blocks.map((block, index) => {
              const meta = sources.data?.[block.id];
              const visible = displayReportBlock(block, meta);
              const sourcePending =
                report.dirty || sources.isPending || sources.isFetching;
              const hasUpdate =
                meta && meta.latest_revision > (block.source?.revision ?? 0);
              return (
                <section
                  className="report-block"
                  id={`report-block-${block.id}`}
                  key={block.id}
                >
                  {editing && (
                    <div className="report-block-tools">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={copy("上移段落", "Move section up")}
                        disabled={index === 0}
                        onClick={() => move(index, -1)}
                      >
                        <ArrowUpIcon className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={copy("下移段落", "Move section down")}
                        disabled={index === report.value.blocks.length - 1}
                        onClick={() => move(index, 1)}
                      >
                        <ArrowDownIcon className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={copy("移除段落", "Remove section")}
                        onClick={() =>
                          change(
                            {
                              ...report.value,
                              blocks: report.value.blocks.filter(
                                (item) => item.id !== block.id,
                              ),
                            },
                            true,
                          )
                        }
                      >
                        <Trash2Icon className="size-4" />
                      </Button>
                    </div>
                  )}
                  {editing ? (
                    <input
                      className="report-block-title"
                      aria-label={copy(
                        `第${index + 1}段标题`,
                        `Section ${index + 1} title`,
                      )}
                      value={block.title}
                      maxLength={200}
                      onChange={(event) =>
                        patch(block.id, { title: event.target.value })
                      }
                    />
                  ) : (
                    <h2>{block.title}</h2>
                  )}
                  {editing ? (
                    <textarea
                      className="report-prose"
                      aria-label={copy(
                        `第${index + 1}段正文`,
                        `Section ${index + 1} text`,
                      )}
                      placeholder={copy(
                        "写下这项发现…",
                        "Write about this finding…",
                      )}
                      value={block.body}
                      maxLength={30000}
                      onChange={(event) =>
                        patch(block.id, { body: event.target.value })
                      }
                    />
                  ) : (
                    block.body && <p className="report-prose">{block.body}</p>
                  )}
                  {block.source && (
                    <>
                      {externalReportFrame(block) &&
                      !visible.source_snapshot ? (
                        <p role={sourcePending ? "status" : "alert"}>
                          {sourcePending
                            ? copy("读取来源…", "Loading source…")
                            : copy(
                                "来源暂时无法读取，请刷新重试。",
                                "Source unavailable. Refresh to retry.",
                              )}
                        </p>
                      ) : (
                        <ReportSourceView block={visible} />
                      )}
                      {editing ? (
                        <input
                          className="report-caption"
                          aria-label={copy(
                            `第${index + 1}段图注`,
                            `Section ${index + 1} caption`,
                          )}
                          placeholder={copy(
                            "添加图注或注释…",
                            "Add a caption or note…",
                          )}
                          value={block.caption}
                          maxLength={2000}
                          onChange={(event) =>
                            patch(block.id, { caption: event.target.value })
                          }
                        />
                      ) : (
                        block.caption && (
                          <p className="report-caption">{block.caption}</p>
                        )
                      )}
                      <div className="report-provenance">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              const saved = await report.flush();
                              const savedBlock = saved?.payload.blocks.find(
                                (item) => item.id === block.id,
                              );
                              if (!saved || !savedBlock?.source)
                                throw new Error(
                                  copy(
                                    "请保存成果后重试。",
                                    "Save the report and try again.",
                                  ),
                                );
                              navigateToAppRoute(
                                router,
                                sourceHistoryHref(
                                  projectId,
                                  savedBlock.source,
                                  {
                                    reportId: saved.id,
                                    reportRevision: saved.revision,
                                    blockId: savedBlock.id,
                                  },
                                ),
                              );
                            })
                          }
                        >
                          {copy("查看来源", "View source")}
                          <ExternalLinkIcon className="size-3" />
                        </Button>
                        {meta?.archived && (
                          <span>{copy("项目存档", "Project archive")}</span>
                        )}
                        {meta?.retained && (
                          <span>
                            {copy("已保留来源快照", "Retained source snapshot")}
                          </span>
                        )}
                        {hasUpdate && (
                          <span>
                            {copy("来源有新版本", "New source version")}
                          </span>
                        )}
                        {(hasUpdate || meta?.retained) && (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busy}
                            onClick={() =>
                              void perform(async () => {
                                const fresh = await projectResearchApi.source(
                                  projectId,
                                  block.source!.kind,
                                  block.source!.id,
                                );
                                if (fresh.reference.kind === "figure")
                                  reportFigure(
                                    {
                                      ...block,
                                      source: fresh.reference,
                                      source_snapshot: fresh.payload,
                                    },
                                    locale,
                                  );
                                patch(block.id, {
                                  source: fresh.reference,
                                  source_snapshot: fresh.payload,
                                });
                              })
                            }
                          >
                            {copy("使用新版本", "Use new version")}
                          </Button>
                        )}
                      </div>
                    </>
                  )}
                </section>
              );
            })}
          </article>
        )}
      </section>
      <ReportPublication projectId={projectId} title={report.value.title} />
    </ProjectShell>
  );
}

function ReportPublication({
  projectId,
  title,
}: {
  projectId: string;
  title: string;
}) {
  usePreviewPublication({
    page: title,
    draft: "",
    scope: { project_id: projectId },
  });
  return null;
}
