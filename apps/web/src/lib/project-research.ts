import { apiFetch } from "./api-client";
import type { HistoryKind, ReportSource } from "./workspace-api";
import { sameReportSource } from "./report-source-frame";

export type ReportSourceContext = {
  reportId: string;
  reportRevision: number;
  blockId: string;
  sha256: string;
};

export type HistoryItem = {
  kind: HistoryKind;
  id: string;
  title: string;
  original_title: string;
  created_at: string;
  status: string;
  dataset_id: string | null;
  filename: string | null;
  revision: number;
  detail_id: string | null;
  archived: boolean;
  label_revision: number;
  available?: boolean;
  reference?: ReportSource;
  issue_code?: string | null;
};
export type HistoryPage = {
  items: HistoryItem[];
  total: number;
  offset: number;
  limit: number;
};
export type ResolvedSource = {
  material?: {
    id: string;
    sha256: string;
    version: number;
    filename: string;
  } | null;
  source_version?: number | null;
  available?: boolean;
  retained?: boolean;
  reference: ReportSource;
  payload: Record<string, unknown>;
  archived: boolean;
  latest_revision: number;
  dataset_id: string | null;
  dataset_sha256: string | null;
  filename: string | null;
  issue_code?: string | null;
  error_code?: string;
};
const base = (project: string) =>
  `/api/v1/projects/${encodeURIComponent(project)}/research`;
export const projectResearchApi = {
  history: (
    project: string,
    options: {
      q?: string;
      kind?: string;
      offset?: number;
      limit?: number;
    } = {},
    signal?: AbortSignal,
    assets = false,
  ) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(options))
      if (value !== undefined && value !== "") params.set(key, String(value));
    return apiFetch<HistoryPage>(
      `${base(project)}/${assets ? "assets" : "history"}?${params}`,
      { signal },
    );
  },
  source: (
    project: string,
    kind: string,
    id: string,
    revision?: number,
    signal?: AbortSignal,
    context?: ReportSourceContext,
  ) =>
    apiFetch<ResolvedSource>(
      context
        ? `${base(project)}/reports/${encodeURIComponent(context.reportId)}/source/${encodeURIComponent(context.blockId)}?revision=${context.reportRevision}`
        : `${base(project)}/source/${encodeURIComponent(kind)}/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ""}`,
      { signal },
    ).then((value) => {
      if (
        context &&
        !sameReportSource(value.reference, {
          kind: kind as ReportSource["kind"],
          id,
          revision: revision ?? 0,
          sha256: context.sha256,
        })
      )
        throw new Error("来源链接与保存的成果不一致，请从成果重新打开。");
      return value;
    }),
  rename: (
    project: string,
    item: HistoryItem,
    title: string,
    signal?: AbortSignal,
  ) =>
    apiFetch(`${base(project)}/history/${item.kind}/${item.id}/title`, {
      method: "PUT",
      signal,
      body: JSON.stringify({ title, expected_revision: item.label_revision }),
    }),
  continue: (
    project: string,
    kind: HistoryKind,
    source: ResolvedSource,
    requestId: string,
    signal?: AbortSignal,
  ) =>
    apiFetch<{ href: string; record_id: string }>(
      `${base(project)}/history/${kind}/${source.reference.id}/continue`,
      {
        method: "POST",
        signal,
        body: JSON.stringify({
          request_id: requestId,
          source_sha256: source.reference.sha256,
          revision: source.reference.revision,
        }),
      },
    ),
  reportSources: (
    project: string,
    id: string,
    revision: number,
    signal?: AbortSignal,
  ) =>
    apiFetch<Record<string, ResolvedSource>>(
      `${base(project)}/reports/${id}/sources?revision=${revision}`,
      { signal },
    ),
};

export function historyKindLabel(kind: string, locale: string) {
  const names: Record<string, [string, string]> = {
    media_run: ["特征处理", "Media processing"],
    analysis_run: ["分析运行", "Analysis run"],
    plot: ["图形草稿", "Figure draft"],
    plot_run: ["Python 绘图", "Python figure"],
    statistics: ["统计分析", "Statistics"],
    ml: ["机器学习", "Machine learning"],
    quality: ["数据质量", "Data quality"],
    workflow: ["工作流", "Workflow"],
    figure: ["图形", "Figure"],
    citation: ["材料引用", "Citation"],
    analysis: ["分析草稿", "Analysis draft"],
    report: ["研究成果", "Report"],
    research_input: ["讨论草稿", "Discussion draft"],
    research: ["AI 分析", "AI analysis"],
    plan: ["研究计划", "Research plan"],
    template: ["模板", "Template"],
    session: ["会话", "Session"],
  };
  return names[kind]?.[locale === "zh-CN" ? 0 : 1] ?? kind;
}

export function sourceHistoryHref(
  project: string,
  source: ReportSource,
  context?: Omit<ReportSourceContext, "sha256">,
) {
  const params = new URLSearchParams({
    kind: source.kind,
    sourceId: source.id,
    revision: String(source.revision),
  });
  if (context) {
    params.set("reportId", context.reportId);
    params.set("reportRevision", String(context.reportRevision));
    params.set("blockId", context.blockId);
    params.set("sourceSha", source.sha256);
  }
  return `/projects/${project}/history?${params}`;
}
