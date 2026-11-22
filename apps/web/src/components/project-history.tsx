"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, PencilIcon, SearchIcon } from "lucide-react";
import { useLanguage } from "@/providers/language-provider";
import {
  projectResearchApi,
  historyKindLabel,
  type HistoryItem,
  type ResolvedSource,
  type ReportSourceContext,
  sourceHistoryHref,
} from "@/lib/project-research";
import {
  displayReportBlock,
  externalReportFrame,
} from "@/lib/report-source-frame";
import type {
  HistoryKind,
  ReportBlock,
  ReportDocument,
} from "@/lib/workspace-api";
import type { StatisticalRun } from "@/lib/types";
import { saveGeneratedFile } from "@/lib/api-download";
import { ProjectShell } from "./project-shell";
import { DesktopLink } from "./desktop-link";
import { Button } from "./ui/button";
import { DownloadButton } from "./download-button";
import {
  ResearchFailure,
  ResearchStatus,
  ResearchActionNotice,
} from "./formal-research-common";
import { ReportSourceView } from "./report-source-view";
import { useResearchAction } from "./use-research-action";
import { StatisticsResultView } from "@/app/projects/[projectId]/statistics/statistics-result";
import { usePreviewPublication } from "./preview-workspace";

const kinds: HistoryKind[] = [
  "analysis_run",
  "plot_run",
  "plot",
  "statistics",
  "ml",
  "figure",
  "analysis",
  "report",
  "citation",
  "research",
  "research_input",
  "quality",
  "workflow",
  "plan",
  "template",
  "session",
];
const reportable = new Set([
  "plot_run",
  "figure",
  "ml",
  "quality",
  "workflow",
  "citation",
]);
const activeStates = new Set([
  "queued",
  "running",
  "waiting_approval",
  "cancel_requested",
  "cancel_pending",
]);

export function ProjectHistory({ projectId }: { projectId: string }) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const params = useSearchParams();
  const router = useRouter();
  const linkedId = params.get("sourceId");
  const linkedKind = params.get("kind");
  const linkedRevision = params.get("revision");
  const linkedReport = params.get("reportId");
  const linkedReportRevision = params.get("reportRevision");
  const linkedBlock = params.get("blockId");
  const linkedSha = params.get("sourceSha");
  const linkedContext =
    linkedReport || linkedReportRevision || linkedBlock || linkedSha
      ? {
          reportId: linkedReport ?? "",
          reportRevision: Number(linkedReportRevision),
          blockId: linkedBlock ?? "",
          sha256: linkedSha ?? "",
        }
      : undefined;
  const contextKey = JSON.stringify(linkedContext);
  const initialKind = kinds.includes(params.get("kind") as HistoryKind)
    ? params.get("kind")!
    : "";
  const [kind, setKind] = useState(initialKind);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [chosen, setChosen] = useState<{
    kind: HistoryKind;
    id: string;
    revision?: number;
    context?: ReportSourceContext;
  } | null>(
    params.get("sourceId") && initialKind
      ? {
          kind: initialKind as HistoryKind,
          id: params.get("sourceId")!,
          revision: Number(params.get("revision")) || undefined,
          context: linkedContext,
        }
      : null,
  );
  const [tab, setTab] = useState("result");
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState("");
  const [renameError, setRenameError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (linkedId && kinds.includes(linkedKind as HistoryKind))
      setChosen((current) =>
        current?.id === linkedId &&
        current.kind === linkedKind &&
        current.revision === (Number(linkedRevision) || undefined) &&
        JSON.stringify(current.context) === contextKey
          ? current
          : {
              kind: linkedKind as HistoryKind,
              id: linkedId,
              revision: Number(linkedRevision) || undefined,
              context: contextKey
                ? (JSON.parse(contextKey) as ReportSourceContext)
                : undefined,
            },
      );
  }, [linkedId, linkedKind, linkedRevision, contextKey]);
  const client = useQueryClient();
  const listing = useQuery({
    queryKey: ["project-history", projectId, query, kind, offset],
    queryFn: ({ signal }) =>
      projectResearchApi.history(
        projectId,
        { q: query, kind, offset, limit: 30 },
        signal,
      ),
    retry: false,
    refetchInterval: (state) =>
      state.state.data?.items.some((item) => activeStates.has(item.status))
        ? 3000
        : false,
  });
  useEffect(() => {
    if (!chosen && listing.data?.items[0]) setChosen(listing.data.items[0]);
  }, [listing.data, chosen]);
  const item = listing.data?.items.find(
    (entry) => entry.id === chosen?.id && entry.kind === chosen.kind,
  );
  const source = useQuery({
    queryKey: [
      "history-source",
      projectId,
      chosen?.kind,
      chosen?.id,
      chosen?.revision,
      chosen?.context,
    ],
    queryFn: ({ signal }) =>
      projectResearchApi.source(
        projectId,
        chosen!.kind,
        chosen!.id,
        chosen?.revision,
        signal,
        chosen?.context,
      ),
    enabled: !!chosen,
    retry: false,
    refetchInterval: () =>
      !chosen?.context && item && activeStates.has(item.status) ? 3000 : false,
  });
  const action = useResearchAction(
    `${projectId}:${chosen?.kind}:${chosen?.id}:${source.data?.reference.sha256}`,
  );
  const payload = source.data?.payload ?? {};
  const record = payload.record as
    { payload?: Record<string, unknown> } | undefined;
  const selectedTitle =
    item?.title ??
    (typeof record?.payload?.title === "string"
      ? record.payload.title
      : chosen
        ? historyKindLabel(chosen.kind, locale)
        : "");
  const originalHref =
    chosen && source.data
      ? originalLocation(projectId, chosen.kind, source.data)
      : null;
  const canCopy =
    chosen &&
    ["plot", "plot_run", "statistics", "figure", "analysis", "workflow", "ml"].includes(
      chosen.kind,
    );
  async function rename() {
    if (!item || saving || !title.trim()) return;
    setSaving(true);
    setRenameError(null);
    try {
      await projectResearchApi.rename(projectId, item, title.trim());
      await client.invalidateQueries({
        queryKey: ["project-history", projectId],
      });
      setRenaming(false);
    } catch (error) {
      setRenameError(error);
    } finally {
      setSaving(false);
    }
  }
  function select(entry: HistoryItem) {
    setChosen({ kind: entry.kind, id: entry.id });
    setTab("result");
    setRenaming(false);
    setRenameError(null);
    const url = new URL(window.location.href);
    url.searchParams.set("kind", entry.kind);
    url.searchParams.set("sourceId", entry.id);
    url.searchParams.delete("revision");
    for (const key of ["reportId", "reportRevision", "blockId", "sourceSha"])
      url.searchParams.delete(key);
    window.history.replaceState(null, "", url.href);
  }
  const sidebar = (
    <section className="research-history-list">
      <h2>{copy("本项目记录", "Project records")}</h2>
      <label className="research-history-search">
        <SearchIcon className="size-4" />
        <input
          type="search"
          aria-label={copy("搜索记录", "Search records")}
          placeholder={copy("搜索记录…", "Search records…")}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOffset(0);
            setChosen(null);
          }}
        />
      </label>
      <SearchSelect
        aria-label={copy("记录类型", "Record type")}
        value={kind}
        onChange={(event) => {
          setKind(event.target.value);
          setOffset(0);
          setChosen(null);
        }}
      >
        <option value="">{copy("全部记录", "All records")}</option>
        {kinds.map((value) => (
          <option key={value} value={value}>
            {historyKindLabel(value, locale)}
          </option>
        ))}
      </SearchSelect>
      {listing.isPending && (
        <p role="status">{copy("读取记录…", "Loading records…")}</p>
      )}
      {listing.isError && (
        <ResearchFailure
          error={listing.error}
          retry={() => {
            void listing.refetch();
          }}
        />
      )}
      {listing.isSuccess && !listing.data.items.length && (
        <p>{copy("没有找到记录", "No records found")}</p>
      )}
      <div className="research-history-items">
        {listing.data?.items.map((entry) => (
          <button
            key={`${entry.kind}:${entry.id}`}
            className={
              entry.id === chosen?.id && entry.kind === chosen.kind
                ? "is-selected"
                : ""
            }
            onClick={() => select(entry)}
            aria-pressed={entry.id === chosen?.id && entry.kind === chosen.kind}
          >
            <strong>{entry.title}</strong>
            <small>
              {historyKindLabel(entry.kind, locale)} ·{" "}
              <HistoryStatus state={entry.status} />
            </small>
            {entry.filename && entry.filename !== entry.title && (
              <small>{entry.filename}</small>
            )}
            <small>
              {new Date(entry.created_at).toLocaleString(locale)}
              {entry.archived ? ` · ${copy("项目存档", "Archive")}` : ""}
            </small>
          </button>
        ))}
      </div>
      {listing.data && listing.data.total > 30 && (
        <div className="research-pagination">
          <Button
            variant="ghost"
            size="sm"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            {copy("上一页", "Previous")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={offset + 30 >= listing.data.total}
            onClick={() => setOffset(offset + 30)}
          >
            {copy("下一页", "Next")}
          </Button>
        </div>
      )}
      <DesktopLink
        className="entry-manual"
        href={`/projects/${projectId}/versions`}
      >
        {copy("项目版本", "Project versions")}
      </DesktopLink>
    </section>
  );
  return (
    <ProjectShell projectId={projectId} explorerContent={sidebar}>
      <section className="history-workbench">
        <header className="history-detail-heading">
          <div>
            <h2>{selectedTitle || copy("项目历史", "Project history")}</h2>
            {item && (
              <p>
                <HistoryStatus state={item.status} />
                {item.archived && ` · ${copy("项目存档", "Project archive")}`}
              </p>
            )}
          </div>
          {item && (
            <Button
              variant="ghost"
              aria-label={copy("重命名记录", "Rename record")}
              onClick={() => {
                setTitle(item.title);
                setRenaming(!renaming);
              }}
            >
              <PencilIcon className="size-4" />
            </Button>
          )}
        </header>
        {renaming && (
          <form
            className="history-rename"
            onSubmit={(event) => {
              event.preventDefault();
              void rename();
            }}
          >
            <input
              aria-label={copy("记录名称", "Record name")}
              value={title}
              maxLength={160}
              onChange={(event) => setTitle(event.target.value)}
              autoFocus
            />
            <Button type="submit" disabled={saving || !title.trim()}>
              {copy("保存名称", "Save name")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setRenaming(false)}
            >
              {copy("取消", "Cancel")}
            </Button>
          </form>
        )}
        {renameError != null && <ResearchFailure error={renameError} />}
        {chosen && (
          <div
            className="history-detail-tabs"
            aria-label={copy("记录详情", "Record details")}
          >
            {[
              ["result", copy("分析结果", "Results")],
              ["parameters", copy("保存参数", "Saved parameters")],
              ["sources", copy("数据与来源", "Data and sources")],
            ].map(([value, label]) => (
              <Button
                key={value}
                variant="ghost"
                aria-pressed={tab === value}
                onClick={() => setTab(value)}
              >
                {label}
              </Button>
            ))}
          </div>
        )}
        {source.isFetching && !source.data && (
          <p role="status">{copy("打开记录…", "Opening record…")}</p>
        )}
        {source.isError && (
          <ResearchFailure
            error={source.error}
            retry={() => {
              void source.refetch();
            }}
          />
        )}
        {chosen && source.data && (
          <div className="history-detail-body">
            {tab === "result" && (
              <HistoryResult
                projectId={projectId}
                kind={chosen.kind}
                source={source.data}
              />
            )}
            {tab === "parameters" && (
              <>
                <h3>{copy("保存的配置", "Saved configuration")}</h3>
                <pre className="history-code" data-no-translate>
                  {JSON.stringify(
                    payload.input_config ?? record?.payload ?? payload,
                    null,
                    2,
                  )}
                </pre>
                <DownloadButton
                  identity={`${projectId}:${chosen.kind}:${chosen.id}:${source.data.reference.sha256}:configuration`}
                  download={(signal) =>
                    saveGeneratedFile(
                      new Blob(
                        [
                          JSON.stringify(
                            {
                              reference: source.data!.reference,
                              configuration:
                                payload.input_config ??
                                record?.payload ??
                                payload,
                            },
                            null,
                            2,
                          ),
                        ],
                        { type: "application/json;charset=utf-8" },
                      ),
                      `${selectedTitle}-配置.json`,
                      signal,
                      async () => {
                        const fresh = await projectResearchApi.source(
                          projectId,
                          chosen.kind,
                          chosen.id,
                          chosen.revision,
                          signal,
                          chosen.context,
                        );
                        if (
                          fresh.reference.sha256 !==
                          source.data!.reference.sha256
                        )
                          throw new Error("Source changed");
                      },
                      locale,
                    )
                  }
                >
                  {copy("下载配置", "Download configuration")}
                </DownloadButton>
              </>
            )}
            {tab === "sources" && (
              <>
                <dl className="history-source-details">
                  <dt>{copy("数据文件", "Data file")}</dt>
                  <dd>
                    {source.data.filename ?? "—"}
                    {source.data.filename && source.data.source_version
                      ? ` · v${source.data.source_version}`
                      : ""}
                  </dd>
                  <dt>{copy("数据 SHA256", "Data SHA256")}</dt>
                  <dd>
                    <code>{source.data.dataset_sha256 ?? "—"}</code>
                  </dd>
                  <dt>{copy("记录版本", "Record version")}</dt>
                  <dd>r{source.data.reference.revision}</dd>
                  <dt>{copy("来源 SHA256", "Source SHA256")}</dt>
                  <dd>
                    <code>{source.data.reference.sha256}</code>
                  </dd>
                </dl>
                <div className="history-source-actions">
                  {source.data.dataset_id && (
                    <DesktopLink
                      href={`/projects/${projectId}/data?datasetId=${source.data.dataset_id}`}
                    >
                      {copy("打开数据", "Open data")}
                    </DesktopLink>
                  )}
                  {originalHref && (
                    <DesktopLink href={originalHref}>
                      {copy("打开原记录", "Open original record")}
                    </DesktopLink>
                  )}
                </div>
              </>
            )}
            <footer className="history-detail-footer">
              {canCopy && (
                <Button
                  disabled={action.pending}
                  onClick={() =>
                    action.start(
                      (signal, key) =>
                        projectResearchApi.continue(
                          projectId,
                          chosen.kind,
                          source.data!,
                          key,
                          signal,
                        ),
                      (value) => {
                        navigateToAppRoute(router, value.href);
                      },
                      { retryable: true },
                    )
                  }
                >
                  {copy("从这里继续", "Continue from here")}
                  <ArrowRightIcon className="size-4" />
                </Button>
              )}
              {!canCopy && originalHref && tab !== "sources" && (
                <DesktopLink className="entry-manual" href={originalHref}>
                  {copy("打开原记录", "Open original record")}
                  <ArrowRightIcon className="size-4" />
                </DesktopLink>
              )}
            </footer>
            <ResearchActionNotice
              action={action}
              reload={() => source.refetch()}
              resetAfterRead={false}
            />
          </div>
        )}
      </section>
      <HistoryPublication
        projectId={projectId}
        source={source.data}
        title={selectedTitle}
      />
    </ProjectShell>
  );
}

function HistoryStatus({ state }: { state: string }) {
  const { locale } = useLanguage();
  const label: Record<string, [string, string]> = {
    saved: ["已保存", "Saved"],
    reviewed: ["已核对", "Reviewed"],
    completed_with_errors: ["部分完成", "Partially completed"],
    pending: ["等待运行", "Waiting to run"],
    unreviewed: ["待核对", "Unreviewed"],
  };
  return label[state] ? (
    <span>{label[state][locale === "zh-CN" ? 0 : 1]}</span>
  ) : (
    <ResearchStatus state={state} />
  );
}

function HistoryResult({
  projectId,
  kind,
  source,
}: {
  projectId: string;
  kind: HistoryKind;
  source: ResolvedSource;
}) {
  const { locale } = useLanguage();
  const reportSources = useQuery({
    queryKey: [
      "history-report-sources",
      projectId,
      source.reference.id,
      source.reference.revision,
    ],
    queryFn: ({ signal }) =>
      projectResearchApi.reportSources(
        projectId,
        source.reference.id,
        source.reference.revision,
        signal,
      ),
    enabled: kind === "report",
    retry: false,
  });
  if (kind === "statistics") {
    const run = source.payload as unknown as StatisticalRun;
    return run.result ? (
      <StatisticsResultView
        result={run.result}
        inputConfig={run.input_config}
      />
    ) : (
      <p>
        {locale === "zh-CN" ? "这次运行没有生成结果" : "This run has no result"}
      </p>
    );
  }
  if (kind === "plot_run" && source.payload.status !== "completed") {
    const body = (
      source.payload.record as {
        payload: { error_message?: string; status: string };
      }
    ).payload;
    return <p>{body.error_message ?? body.status}</p>;
  }
  if (reportable.has(kind))
    return (
      <ReportSourceView
        block={{
          id: source.reference.id,
          title: "",
          body: "",
          caption: "",
          source: source.reference,
          source_snapshot: source.payload,
        }}
      />
    );
  const body = (
    source.payload.record as { payload: Record<string, unknown> } | undefined
  )?.payload;
  if (kind === "report" && body)
    return (
      <article className="history-report">
        <h3>{body.title as string}</h3>
        <p>{body.subtitle as string}</p>
        {reportSources.isError && (
          <ResearchFailure
            error={reportSources.error}
            retry={() => void reportSources.refetch()}
          />
        )}
        {(body as unknown as ReportDocument).blocks.map(
          (block: ReportBlock) => (
            <section key={block.id}>
              <h3>{block.title}</h3>
              <p>{block.body}</p>
              {externalReportFrame(block) &&
              !displayReportBlock(block, reportSources.data?.[block.id])
                .source_snapshot ? (
                <p role={reportSources.isPending ? "status" : "alert"}>
                  {locale === "zh-CN"
                    ? reportSources.isPending
                      ? "读取来源…"
                      : "来源暂时无法读取，请刷新重试。"
                    : reportSources.isPending
                      ? "Loading source…"
                      : "Source unavailable. Refresh to retry."}
                </p>
              ) : (
                <ReportSourceView
                  block={displayReportBlock(
                    block,
                    reportSources.data?.[block.id],
                  )}
                />
              )}
              <p>{block.caption}</p>
              {block.source && (
                <DesktopLink
                  href={sourceHistoryHref(projectId, block.source, {
                    reportId: source.reference.id,
                    reportRevision: source.reference.revision,
                    blockId: block.id,
                  })}
                >
                  {locale === "zh-CN" ? "查看来源" : "View source"}
                </DesktopLink>
              )}
            </section>
          ),
        )}
      </article>
    );
  return (
    <>
      <p className="history-draft-question">
        {String(body?.question ?? body?.research_question ?? "")}
      </p>
      <pre className="history-code">
        {JSON.stringify(body ?? source.payload, null, 2)}
      </pre>
    </>
  );
}

function originalLocation(
  project: string,
  kind: HistoryKind,
  source: ResolvedSource,
) {
  if (kind === "plot" || kind === "plot_run") {
    const record = source.payload.record as {
      id: string;
      payload: { draft_id?: string };
    };
    return `/projects/${project}/workspace?view=plot&plotId=${record.payload.draft_id ?? record.id}`;
  }
  if (kind === "citation") {
    const material = source.material;
    const locator = source.payload.locator as
      Record<string, unknown> | undefined;
    if (!material) return null;
    const params = new URLSearchParams({
      materialId: material.id,
      materialSha: material.sha256,
      materialVersion: String(material.version),
    });
    if (locator?.kind === "text") {
      for (const [key, value] of Object.entries({
        page: locator.page,
        start: locator.start,
        end: locator.end,
        textSha: locator.text_sha256,
      }))
        params.set(key, String(value));
    } else {
      if (source.archived) return null;
      params.set("view", "locations");
      params.set("citationId", source.reference.id);
    }
    return `/projects/${project}/materials?${params}`;
  }
  if (source.archived) return null;
  const base = `/projects/${project}`;
  if (kind === "analysis" && ((source.payload.record as {payload?: {ml?: unknown; ml_editor?: unknown}} | undefined)?.payload?.ml || (source.payload.record as {payload?: {ml_editor?: unknown}} | undefined)?.payload?.ml_editor))
    return `${base}/machine-learning?draftId=${source.reference.id}&datasetId=${source.dataset_id}`;
  if (kind === "analysis_run")
    return `${base}/runs?kind=analysis_run&runId=${source.reference.id}`;
  if (["analysis", "figure"].includes(kind))
    return `${base}/workspace?object=${source.reference.id}`;
  if (kind === "report")
    return `${base}/results?reportId=${source.reference.id}`;
  if (kind === "ml")
    return `${base}/machine-learning?runId=${source.reference.id}${source.dataset_id ? `&datasetId=${source.dataset_id}` : ""}`;
  if (kind === "quality" || kind === "workflow") return `${base}/${kind}`;
  if (kind === "research" || kind === "research_input")
    return `${base}/workspace`;
  return null;
}

function HistoryPublication({
  projectId,
  title,
  source,
}: {
  projectId: string;
  title: string;
  source?: ResolvedSource;
}) {
  usePreviewPublication({
    page: title,
    draft: "",
    scope: {
      project_id: projectId,
      ...(source?.dataset_id && source.dataset_sha256
        ? {
            dataset_id: source.dataset_id,
            dataset_sha256: source.dataset_sha256,
          }
        : {}),
    },
  });
  return null;
}
