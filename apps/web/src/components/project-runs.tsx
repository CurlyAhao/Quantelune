"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  CircleIcon,
  RefreshCwIcon,
  SquareIcon,
  SearchIcon,
  ArrowUpRightIcon,
} from "lucide-react";
import {
  analysisJobApi,
  projectRunsApi,
  isActiveRun,
  runStatusLabel,
  runPhaseLabel,
  type ProjectRun,
  type RunKind,
} from "@/lib/analysis-jobs";
import { ApiError } from "@/lib/api-client";
import { historyKindLabel } from "@/lib/project-research";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { useLanguage } from "@/providers/language-provider";
import { AuthGuard } from "./auth-guard";
import { ProjectShell } from "./project-shell";
import { useResultProjectId } from "./result-source";
import { PreviewPageContext } from "./preview-workspace";
import { Button } from "./ui/button";
import { DesktopLink } from "./desktop-link";

const kinds: RunKind[] = [
  "analysis_run",
  "plot_run",
  "research",
  "statistics",
  "ml",
  "quality",
  "workflow",
];
export function ProjectRuns({ projectId }: { projectId: string }) {
  const id = useResultProjectId(projectId);
  return id ? (
    <AuthGuard>
      <RunsBody key={id} projectId={id} />
    </AuthGuard>
  ) : (
    <p role="alert">项目不存在</p>
  );
}
function RunsBody({ projectId }: { projectId: string }) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const params = useSearchParams();
  const router = useRouter();
  const cache = useQueryClient();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [state, setState] = useState("all");
  const [offset, setOffset] = useState(0);
  const initialKind = params.get("kind") as RunKind;
  const [chosen, setChosen] = useState<{ kind: RunKind; id: string } | null>(
    params.get("runId") && kinds.includes(initialKind)
      ? { kind: initialKind, id: params.get("runId")! }
      : null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(() => Date.now());
  const listing = useQuery({
    queryKey: ["project-runs", projectId, q, kind, state, offset],
    queryFn: ({ signal }) =>
      projectRunsApi.list(
        projectId,
        { q, kind, state, offset, limit: 30 },
        signal,
      ),
    refetchInterval: 2500,
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["project-run", projectId, chosen?.kind, chosen?.id],
    queryFn: ({ signal }) =>
      projectRunsApi.detail(projectId, chosen!.kind, chosen!.id, signal),
    enabled: !!chosen,
    refetchInterval: (query) =>
      isActiveRun(query.state.data?.status || "") ? 1000 : false,
    retry: false,
  });
  useEffect(() => {
    const value = params.get("kind") as RunKind;
    const id = params.get("runId");
    if (id && kinds.includes(value)) setChosen({ kind: value, id });
  }, [params]);
  useEffect(() => {
    if (!chosen && listing.data?.items[0]) {
      const item = listing.data.items[0];
      setChosen({ kind: item.kind, id: item.id });
    }
  }, [chosen, listing.data]);
  useEffect(() => {
    if (!isActiveRun(detail.data?.status || "")) return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [detail.data?.status]);
  const current = detail.data;
  const selectedTitle = listing.data?.items.find(
    (item) => item.kind === chosen?.kind && item.id === chosen?.id,
  )?.title;
  const elapsed = current?.started_at
    ? Math.max(
        0,
        Math.floor(
          ((current.completed_at ? Date.parse(current.completed_at) : clock) -
            Date.parse(current.started_at)) /
            1000,
        ),
      )
    : null;
  function choose(item: ProjectRun) {
    setChosen({ kind: item.kind, id: item.id });
    setError("");
    navigateToAppRoute(
      router,
      `/projects/${projectId}/runs?kind=${item.kind}&runId=${item.id}`,
      "replace",
    );
  }
  async function perform(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
      await cache.invalidateQueries({ queryKey: ["project-runs", projectId] });
      await cache.invalidateQueries({
        queryKey: ["project-run-count", projectId],
      });
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.detail
          : copy(
              "操作未完成，请刷新后重试。",
              "Action incomplete. Refresh and retry.",
            ),
      );
    } finally {
      setBusy(false);
    }
  }
  const list = (
    <section
      className="run-list-panel"
      aria-label={copy("本项目运行", "Project runs")}
    >
      <div className="run-list-heading">
        <strong>{copy("本项目运行", "Project runs")}</strong>
        <Button
          variant="ghost"
          size="sm"
          aria-label={copy("刷新运行状态", "Refresh runs")}
          onClick={() => {
            void listing.refetch();
            void detail.refetch();
          }}
        >
          <RefreshCwIcon className="size-3.5" />
        </Button>
      </div>
      <label className="run-search">
        <SearchIcon className="size-4" />
        <input
          type="search"
          maxLength={200}
          aria-label={copy("查找运行", "Find runs")}
          placeholder={copy("查找运行", "Find runs")}
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setOffset(0);
          }}
        />
      </label>
      <SearchSelect
        aria-label={copy("运行类型", "Run type")}
        value={kind}
        onChange={(event) => {
          setKind(event.target.value);
          setOffset(0);
          setChosen(null);
        }}
      >
        <option value="">{copy("全部类型", "All types")}</option>
        {kinds.map((value) => (
          <option key={value} value={value}>
            {historyKindLabel(value, locale)}
          </option>
        ))}
      </SearchSelect>
      <div
        className="run-filters"
        aria-label={copy("状态筛选", "Status filter")}
      >
        {(
          [
            ["all", "全部", "All"],
            ["active", "进行中", "Active"],
            ["attention", "需处理", "Attention"],
            ["finished", "已结束", "Finished"],
          ] as const
        ).map(([value, zh, en]) => (
          <button
            key={value}
            aria-pressed={state === value}
            onClick={() => {
              setState(value);
              setOffset(0);
              setChosen(null);
            }}
          >
            {copy(zh, en)}
          </button>
        ))}
      </div>
      {listing.isLoading && (
        <p role="status">{copy("读取运行…", "Loading runs…")}</p>
      )}
      {listing.isError && (
        <p role="alert">
          {copy("运行列表暂时无法读取。", "Run list is unavailable.")}
        </p>
      )}
      {listing.data?.items.map((item) => (
        <button
          className={`run-list-item ${chosen?.id === item.id && chosen.kind === item.kind ? "is-selected" : ""}`}
          key={`${item.kind}:${item.id}`}
          onClick={() => choose(item)}
        >
          <strong>{item.title}</strong>
          <span className={`run-state is-${item.status}`}>
            {runStatusLabel(item.status, locale)}
            {item.archived ? copy(" · 存档", " · Archive") : ""}
          </span>
          <small>
            {item.filename ||
              historyKindLabel(item.operation || item.kind, locale)}
          </small>
          <time dateTime={item.created_at}>
            {new Date(item.created_at).toLocaleString(locale)}
          </time>
        </button>
      ))}
      {listing.data && !listing.data.items.length && (
        <p>
          {q || kind || state !== "all"
            ? copy("没有匹配的运行。", "No matching runs.")
            : copy("还没有运行记录。", "No runs yet.")}
        </p>
      )}
      {listing.data && listing.data.total > 30 && (
        <div className="run-pagination">
          <Button
            variant="ghost"
            size="sm"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            {copy("上一页", "Previous")}
          </Button>
          <span>
            {offset + 1}–{Math.min(offset + 30, listing.data.total)} /{" "}
            {listing.data.total}
          </span>
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
    </section>
  );
  return (
    <ProjectShell
      projectId={projectId}
      explorerContent={list}
      explorerSummary={
        listing.data
          ? copy(`${listing.data.total} 项运行`, `${listing.data.total} runs`)
          : undefined
      }
    >
      <PreviewPageContext
        value={{
          page: copy("运行状态", "Run status"),
          scope: { project_id: projectId },
          draft: current?.title || "",
        }}
      />
      <section
        className="project-runs"
        aria-label={copy("运行状态", "Run status")}
      >
        <header className="run-page-heading">
          <h2>{copy("运行状态", "Run status")}</h2>
          {!!listing.data?.active_total && (
            <span>
              {copy(
                `${listing.data.active_total} 项进行中`,
                `${listing.data.active_total} active`,
              )}
            </span>
          )}
        </header>
        {error && (
          <p className="run-error" role="alert">
            {error}
          </p>
        )}
        {detail.isError && (
          <p role="alert">
            {copy(
              "这条记录暂时无法读取，请刷新列表。",
              "This record is unavailable. Refresh the list.",
            )}
          </p>
        )}
        {detail.isLoading && chosen && (
          <p role="status">{copy("读取详情…", "Loading details…")}</p>
        )}
        {current ? (
          <article className="run-detail">
            <header>
              <div>
                <span className={`run-state is-${current.status}`}>
                  {runStatusLabel(current.status, locale)}
                </span>
                <h3>{selectedTitle || current.title}</h3>
                <p>
                  {current.filename ||
                    historyKindLabel(current.operation || current.kind, locale)}
                </p>
              </div>
              <div className="run-detail-actions">
                {current.can_cancel && (
                  <Button
                    variant="outline"
                    disabled={
                      busy ||
                      ["cancel_requested", "cancel_pending"].includes(
                        current.status,
                      )
                    }
                    onClick={() =>
                      void perform(async () => {
                        const value = await projectRunsApi.cancel(
                          projectId,
                          current.kind,
                          current.id,
                        );
                        cache.setQueryData(
                          ["project-run", projectId, current.kind, current.id],
                          value,
                        );
                      })
                    }
                  >
                    <SquareIcon className="size-3.5" />
                    {["cancel_requested", "cancel_pending"].includes(
                      current.status,
                    )
                      ? copy("取消中…", "Cancelling…")
                      : copy("取消运行", "Cancel run")}
                  </Button>
                )}
                {current.can_retry && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void perform(async () => {
                        const job = await analysisJobApi.retry(current.id);
                        setChosen({ kind: "analysis_run", id: job.id });
                        navigateToAppRoute(
                          router,
                          `/projects/${projectId}/runs?kind=analysis_run&runId=${job.id}`,
                          "replace",
                        );
                      })
                    }
                  >
                    <RefreshCwIcon className="size-4" />
                    {copy("重新运行", "Run again")}
                  </Button>
                )}
              </div>
            </header>
            <div className="run-phase-line" role="status">
              <span>
                {current.archived && current.recorded_status
                  ? copy(
                      `保存项目时：${runStatusLabel(current.recorded_status, locale)}`,
                      `At save: ${runStatusLabel(current.recorded_status, locale)}`,
                    )
                  : runPhaseLabel(current.phase, locale)}
              </span>
              {elapsed !== null && Number.isFinite(elapsed) && (
                <time>{copy(`${elapsed} 秒`, `${elapsed}s`)}</time>
              )}
            </div>
            {!!current.events.length && (
              <ol
                className="run-events"
                aria-label={copy("已记录的步骤", "Recorded steps")}
              >
                {current.events.map((event, index) => (
                  <li
                    key={`${event.phase}:${event.at}`}
                    className={
                      index === current.events.length - 1 &&
                      isActiveRun(current.status)
                        ? "is-current"
                        : ""
                    }
                  >
                    {index < current.events.length - 1 ||
                    current.status === "completed" ? (
                      <CheckIcon className="size-4" />
                    ) : (
                      <CircleIcon className="size-4" />
                    )}
                    <span>{runPhaseLabel(event.phase, locale)}</span>
                    <time dateTime={event.at}>
                      {new Date(event.at).toLocaleTimeString(locale)}
                    </time>
                  </li>
                ))}
              </ol>
            )}
            {current.error_message && (
              <p className="run-message">{current.error_message}</p>
            )}
            {current.status === "unconfirmed" && (
              <p className="run-message">
                {copy(
                  "这条旧记录未保存结束状态，可打开原记录核对。",
                  "This older record has no saved ending. Open it to check.",
                )}
              </p>
            )}
            <dl className="run-source">
              {current.started_at && (
                <div>
                  <dt>{copy("开始时间", "Started")}</dt>
                  <dd>{new Date(current.started_at).toLocaleString(locale)}</dd>
                </div>
              )}
              {current.completed_at && (
                <div>
                  <dt>{copy("结束时间", "Ended")}</dt>
                  <dd>
                    {new Date(current.completed_at).toLocaleString(locale)}
                  </dd>
                </div>
              )}
            </dl>
            <div className="run-result-links">
              <DesktopLink href={current.result_href}>
                {[
                  "completed",
                  "completed_with_errors",
                  "not_applicable",
                ].includes(current.status)
                  ? copy("查看结果", "View result")
                  : copy("查看原记录", "View record")}
                <ArrowUpRightIcon className="size-4" />
              </DesktopLink>
              {current.edit_href !== current.result_href && (
                <DesktopLink href={current.edit_href}>
                  {current.kind === "plot_run"
                    ? copy("打开图形编辑器", "Open figure editor")
                    : current.operation === "ml"
                      ? copy("调整参数", "Edit parameters")
                      : copy("返回分析", "Back to analysis")}
                </DesktopLink>
              )}
            </div>
            {current.configuration !== undefined && (
              <details className="run-parameters">
                <summary>{copy("参数与来源", "Parameters and source")}</summary>
                {current.dataset_sha256 && (
                  <p>SHA256 · {current.dataset_sha256}</p>
                )}
                <pre>{JSON.stringify(current.configuration, null, 2)}</pre>
              </details>
            )}
            {current.code !== undefined && (
              <details className="run-parameters">
                <summary>
                  {copy("本次绘图代码", "Code for this figure")}
                </summary>
                <pre>{current.code}</pre>
              </details>
            )}
          </article>
        ) : (
          !chosen &&
          !listing.isLoading && (
            <div className="run-empty">
              <h3>{copy("从数据开始一次分析", "Start with data")}</h3>
              <DesktopLink href={`/projects/${projectId}/data`}>
                {copy("打开项目数据", "Open project data")}
                <ArrowUpRightIcon className="size-4" />
              </DesktopLink>
            </div>
          )
        )}
      </section>
    </ProjectShell>
  );
}
