"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRightIcon, RefreshCwIcon, SquareIcon, XIcon } from "lucide-react";
import { projectResearchApi, historyKindLabel, type HistoryItem, type ResolvedSource } from "@/lib/project-research";
import { workspaceApi, type ReportBlock, type ReportDocument } from "@/lib/workspace-api";
import { analysisJobApi, projectRunsApi, isActiveRun, runStatusLabel, type ProjectRun, type RunKind } from "@/lib/analysis-jobs";
import type { TaskRecord } from "@/lib/use-workspace-task";
import type { Dataset, StatisticalRun } from "@/lib/types";
import { downloadApiFile, saveGeneratedFile } from "@/lib/api-download";
import { reportHtml, reportMarkdown, reportTables } from "@/lib/report-output";
import { displayReportBlock, externalReportFrame } from "@/lib/report-source-frame";
import { displayValue, methodNames } from "@/lib/research-results";
import { plotsApi } from "@/lib/plots-api";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { DownloadButton } from "./download-button";
import { ReportSourceView } from "./report-source-view";
import { StatisticsResultView } from "@/app/projects/[projectId]/statistics/statistics-result";
import styles from "./workspace-history.module.css";

export type HistoryTarget = { taskId?: string; analysisId?: string; plotRunId?: string; datasetId?: string; materialId?: string; kind?: string; sourceId?: string };
type ObjectValue = Record<string, unknown>;
type Entry = { item: HistoryItem; body?: ObjectValue; run?: ProjectRun };
type Group = { key: string; title: string; entries: Entry[]; primary: Entry; status: string; at: string; taskIds: string[] };
type HistoryData = { entries: Entry[]; records: Map<string, ObjectValue>; total: number; loaded: number; unread: number };
const object = (value: unknown): ObjectValue => value && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : {};
const string = (value: unknown) => typeof value === "string" ? value : "";
const normalized = (id: string) => id.replaceAll("-", "").toLowerCase();
const identity = (kind: string, id: string) => `${kind}:${normalized(id)}`;
const entryKey = (entry: Entry) => identity(entry.item.kind, entry.item.id);
const recordKinds = new Set(["media_run", "analysis", "analysis_run", "plot", "plot_run", "figure", "report", "research_input", "plan", "template", "session"]);
const runKinds = new Set(["media_run", "analysis_run", "plot_run", "statistics", "ml", "quality", "workflow", "research"]);
const reportable = new Set(["statistics", "ml", "figure", "plot_run", "citation", "quality", "workflow"]);
const continuable = new Set(["statistics", "ml", "analysis", "plot", "plot_run", "figure"]);
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const status = (value: string) => ({ saved: "已保存", reviewed: "已标注" })[value as "saved" | "reviewed"] || runStatusLabel(value, "zh-CN");

async function loadHistory(projectId: string, pages: number, signal: AbortSignal): Promise<HistoryData> {
  const history: HistoryItem[] = [];
  let total = 0;
  for (let page = 0; page < pages; page++) {
    const result = await projectResearchApi.history(projectId, { offset: page * 100, limit: 100 }, signal);
    history.push(...result.items); total = result.total;
    if (history.length >= total || result.items.length < 100) break;
  }
  const running = await projectRunsApi.list(projectId, { limit: 100 }, signal);
  const runs = new Map(running.items.map(run => [identity(run.kind, run.id), run]));
  for (const run of running.items) if (!history.some(item => identity(item.kind, item.id) === identity(run.kind, run.id))) history.push({ kind: run.kind, id: run.id, title: run.title, original_title: run.title, created_at: run.created_at, status: run.status, dataset_id: run.dataset_id, filename: run.filename, revision: run.revision, detail_id: null, archived: run.archived, label_revision: 0 });
  const records = new Map<string, ObjectValue>();
  const failed = new Set<string>();
  const loading = new Map<string, Promise<void>>();
  const read = (kind: string, id: string) => {
    const key = identity(kind, id);
    if (!loading.has(key)) loading.set(key, workspaceApi.read(id, signal).then(record => {
      if (record.project_id !== projectId || record.payload.kind !== kind) throw new Error("记录与项目不一致。");
      records.set(key, object(record.payload));
    }).catch(reason => { if (signal.aborted) throw reason; failed.add(key); }));
    return loading.get(key)!;
  };
  const queue = history.filter(item => recordKinds.has(item.kind));
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(6, queue.length) }, async () => {
    while (next < queue.length) { const item = queue[next++]; await read(item.kind, item.id); }
  }));
  // Style runs retain their actual parent run even when it is beyond this page.
  for (;;) {
    const parents = [...records.values()].filter(body => body.kind === "plot_run").map(body => string(object(body.metadata).style_parent_id)).filter(id => id && !loading.has(identity("plot_run", id)));
    if (!parents.length) break;
    await Promise.all(parents.map(id => read("plot_run", id)));
  }
  return { entries: history.map(item => ({ item, body: records.get(identity(item.kind, item.id)), run: runs.get(identity(item.kind, item.id)) })), records, total, loaded: Math.min(pages * 100, total), unread: failed.size };
}

function groupedHistory(data: HistoryData, tasks: TaskRecord[]): Group[] {
  const parents = new Map<string, string>();
  const find = (key: string): string => { const parent = parents.get(key); if (!parent) { parents.set(key, key); return key; } if (parent === key) return key; const root = find(parent); parents.set(key, root); return root; };
  const join = (left: string, right: string) => { const a = find(left), b = find(right); if (a !== b) parents.set(a < b ? b : a, a < b ? a : b); };
  for (const [key, body] of data.records) {
    const scope = object(body.scope);
    if (body.kind === "analysis") {
      if (string(scope.statistical_run_id)) join(key, identity("statistics", string(scope.statistical_run_id)));
      if (string(scope.ml_run_id)) join(key, identity("ml", string(scope.ml_run_id)));
    }
    if (body.kind === "analysis_run" && string(body.native_id) && ["statistics", "ml"].includes(string(body.operation))) join(key, identity(string(body.operation), string(body.native_id)));
    if (body.kind === "plot_run") {
      if (string(body.draft_id)) join(key, identity("plot", string(body.draft_id)));
      const parent = string(object(body.metadata).style_parent_id);
      if (parent) join(key, identity("plot_run", parent));
    }
  }
  const taskGroups = new Map<string, Set<string>>();
  function assign(key: string, task: string) { const root = find(key); if (!taskGroups.has(root)) taskGroups.set(root, new Set()); taskGroups.get(root)!.add(task); }
  for (const task of tasks) {
    for (const id of [...task.payload.analysis_ids, ...(task.payload.archived_analysis_ids ?? [])]) assign(identity("analysis", id), task.id);
    for (const id of [...task.payload.figure_ids, ...(task.payload.archived_figure_ids ?? [])]) assign(identity("figure", id), task.id);
    for (const id of task.payload.plot_run_ids ?? []) assign(identity("plot_run", id), task.id);
  }
  const byTaskId = new Map(tasks.map(task => [normalized(task.id), task.id]));
  for (const [key, body] of data.records) {
    const taskId = byTaskId.get(normalized(string(body.task_id || body.context_key)));
    if (taskId) assign(key, taskId);
    if (body.kind === "report" && Array.isArray(body.blocks)) for (const block of body.blocks) {
      const source = object(object(block).source);
      if (string(source.kind) && string(source.id)) for (const task of taskGroups.get(find(identity(string(source.kind), string(source.id)))) ?? []) assign(key, task);
    }
  }
  const groups = new Map<string, Entry[]>();
  for (const entry of data.entries) { const key = find(entryKey(entry)); if (!groups.has(key)) groups.set(key, []); groups.get(key)!.push(entry); }
  return [...groups].map(([key, values]) => {
    const entries = values.sort((a, b) => Date.parse(b.item.created_at) - Date.parse(a.item.created_at));
    const active = entries.find(entry => isActiveRun(entry.run?.status || entry.item.status));
    const native = entries.find(entry => ["statistics", "ml"].includes(entry.item.kind));
    const completedPlot = entries.find(entry => entry.item.kind === "plot_run" && entry.item.status === "completed");
    const primary = active || native || completedPlot || entries.find(entry => !["plot", "analysis"].includes(entry.item.kind)) || entries[0];
    const latestRun = active || entries.find(entry => runKinds.has(entry.item.kind));
    const named = entries.find(entry => ["analysis", "plot", "report"].includes(entry.item.kind));
    const title = named?.item.title || methodNames[primary.item.original_title] || primary.item.title;
    return { key, title, entries, primary, status: latestRun?.run?.status || latestRun?.item.status || primary.item.status, at: entries[0].item.created_at, taskIds: [...(taskGroups.get(key) ?? [])] };
  }).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

export function WorkspaceHistory({ projectId, tasks, datasets, currentTaskId, onClose, onOpen, initialSourceId }: {
  projectId: string; tasks: TaskRecord[]; datasets: Dataset[]; currentTaskId?: string; onClose: () => void; onOpen: (target: HistoryTarget) => void; initialSourceId?: string;
}) {
  const client = useQueryClient();
  const [pages, setPages] = useState(1);
  const [scope, setScope] = useState(currentTaskId || "all");
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState("");
  const [version, setVersion] = useState("");
  const [savedRevision, setSavedRevision] = useState<{ key: string; value: number }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [continueTask, setContinueTask] = useState(currentTaskId || "");
  const [pendingRun, setPendingRun] = useState("");
  const listing = useQuery({ queryKey: ["workspace-history", projectId, pages], queryFn: ({ signal }) => loadHistory(projectId, pages, signal), retry: false, refetchInterval: query => query.state.data?.entries.some(entry => isActiveRun(entry.run?.status || entry.item.status)) ? 3000 : false });
  const groups = useMemo(() => listing.data ? groupedHistory(listing.data, tasks) : [], [listing.data, tasks]);
  const visible = groups.filter(group => (scope === "all" || scope === "unassigned" ? scope !== "unassigned" || !group.taskIds.length : group.taskIds.includes(scope)) && (!search || `${group.title} ${group.entries.map(entry => entry.item.filename || "").join(" ")}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())));
  const selected = visible.find(group => group.key === chosen || group.entries.some(entry => entryKey(entry) === chosen));
  const entry = selected?.entries.find(item => entryKey(item) === version) || selected?.primary;
  const selectedIdentity = entry ? entryKey(entry) : "";
  const revision = savedRevision?.key === selectedIdentity ? savedRevision.value : undefined;
  useEffect(() => {
    if (pendingRun) {
      const found = groups.find(group => group.entries.some(item => normalized(item.item.id) === normalized(pendingRun)));
      if (found) { setScope("all"); setChosen(entryKey(found.primary)); setVersion(identity("analysis_run", pendingRun)); setPendingRun(""); return; }
    }
    if (initialSourceId && !chosen) {
      const found = groups.find(group => group.entries.some(item => normalized(item.item.id) === normalized(initialSourceId)));
      if (found) { if (scope !== "all" && !found.taskIds.includes(scope)) setScope("all"); setChosen(entryKey(found.primary)); setVersion(entryKey(found.entries.find(item => normalized(item.item.id) === normalized(initialSourceId))!)); return; }
    }
    if (!selected && visible.length) { setChosen(entryKey(visible[0].primary)); setVersion(""); }
  }, [groups, visible, initialSourceId, chosen, selected, scope, pendingRun]);
  const source = useQuery({ queryKey: ["workspace-history-source", projectId, entry?.item.kind, entry?.item.id, revision ?? entry?.item.revision], queryFn: ({ signal }) => projectResearchApi.source(projectId, entry!.item.kind, entry!.item.id, revision ?? entry!.item.revision, signal), enabled: !!entry, retry: false, refetchInterval: () => entry && isActiveRun(entry.run?.status || entry.item.status) ? 3000 : false });
  const run = useQuery({ queryKey: ["workspace-history-run", projectId, entry?.item.kind, entry?.item.id], queryFn: ({ signal }) => projectRunsApi.detail(projectId, entry!.item.kind as RunKind, entry!.item.id, signal), enabled: !!entry && runKinds.has(entry.item.kind), retry: false, refetchInterval: query => isActiveRun(query.state.data?.status || "") ? 2000 : false });
  const refresh = async () => { await client.invalidateQueries({ queryKey: ["workspace-history", projectId] }); await source.refetch(); if (entry && runKinds.has(entry.item.kind)) await run.refetch(); };
  async function perform(action: () => Promise<void>) { if (busy) return; setBusy(true); setError(""); try { await action(); await refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "操作未完成，请重试。"); } finally { setBusy(false); } }
  function openEntry() {
    if (!entry || !source.data || !selected) return;
    const taskId = selected.taskIds.includes(currentTaskId || "") ? currentTaskId : selected.taskIds[0];
    const analysis = selected.entries.find(item => item.item.kind === "analysis");
    onOpen({ taskId, analysisId: analysis?.item.id, plotRunId: entry.item.kind === "plot_run" ? entry.item.id : undefined, datasetId: source.data.dataset_id || undefined, materialId: source.data.material?.id, kind: entry.item.kind, sourceId: entry.item.id });
  }
  async function continueFrom() {
    if (!entry || !source.data || !continueTask) return;
    const copy = await projectResearchApi.continue(projectId, entry.item.kind, source.data, crypto.randomUUID());
    const record = await workspaceApi.read(copy.record_id);
    if (record.project_id !== projectId) throw new Error("副本与当前项目不一致。");
    onOpen({ taskId: continueTask, kind: record.payload.kind, sourceId: record.id, analysisId: record.payload.kind === "analysis" ? record.id : undefined, datasetId: source.data.dataset_id || undefined });
  }
  const payload = source.data?.payload ?? {};
  const body = object(object(payload.record).payload);
  const configuration = payload.input_config || body.configuration || body.parameters || body.statistics || body.ml || body.scope;
  const versions = selected?.entries.filter(item => item.item.kind === "plot_run") ?? [];
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent showCloseButton={false} className={`rw-shell rw-history-dialog ${styles.drawer}`}>
      <header className={styles.header}><DialogTitle>历史</DialogTitle><div><button type="button" aria-label="刷新历史" disabled={listing.isFetching || busy} onClick={() => void refresh()}><RefreshCwIcon size={16} /></button><button type="button" aria-label="关闭历史" onClick={onClose}><XIcon size={18} /></button></div></header>
      <div className={styles.layout}>
        <section className={styles.list} aria-label="历史记录">
          <div className={styles.filters}><SearchSelect aria-label="历史研究问题筛选" value={scope} onChange={event => { setScope(event.target.value); setChosen(""); setVersion(""); }}><option value="all">全项目</option>{tasks.map(task => <option key={task.id} value={task.id}>{task.payload.title}</option>)}<option value="unassigned">未关联研究问题</option></SearchSelect><input type="search" aria-label="搜索历史" placeholder="搜索历史" value={search} onChange={event => setSearch(event.target.value)} /></div>
          {listing.isPending && <p role="status">正在读取历史…</p>}
          {listing.isError && <p role="alert">{listing.error.message}<button onClick={() => void listing.refetch()}>重试</button></p>}
          {!listing.isPending && !listing.isError && !visible.length && <p className={styles.empty}>没有找到记录。{scope !== "all" && <button type="button" onClick={() => setScope("all")}>查看全项目</button>}</p>}
          {scope !== "all" && groups.some(group => !group.taskIds.length && isActiveRun(group.status)) && <button type="button" className={styles.otherRuns} onClick={() => setScope("all")}>查看项目中的其他运行</button>}
          <ol>{visible.map(group => <li key={group.key}><button type="button" aria-pressed={selected?.key === group.key} onClick={() => { setChosen(entryKey(group.primary)); setVersion(""); setError(""); setContinueTask(group.taskIds.includes(currentTaskId || "") ? currentTaskId! : group.taskIds[0] || currentTaskId || ""); }}><strong>{group.title}</strong><span>{status(group.status)}<time dateTime={group.at}>{date(group.at)}</time></span></button></li>)}</ol>
          {listing.data && listing.data.loaded < listing.data.total && <button type="button" disabled={listing.isFetching} onClick={() => setPages(pages + 1)}>加载更多</button>}
        </section>
        <section className={styles.detail} aria-label="历史详情">
          {selected && entry && <>
            <header className={styles.detailHeader}><h2>{selected.title}</h2><p>{status(run.data?.status || entry.item.status)} · {date(entry.item.created_at)}</p></header>
            {error && <p role="alert" className={styles.error}>{error}</p>}
            {source.isPending && <p role="status">正在打开记录…</p>}
            {source.isError && <p role="alert">{source.error.message}<button type="button" onClick={() => void source.refetch()}>重试</button></p>}
            {source.data && <>
              <div className={styles.actions}>
                {selected.taskIds.length > 0 && <button type="button" onClick={openEntry}><ArrowUpRightIcon size={15} />在研究问题中查看</button>}
                {source.data.dataset_id && <button type="button" onClick={() => onOpen({ taskId: selected.taskIds[0], datasetId: source.data!.dataset_id!, kind: "data" })}>查看数据</button>}
                {run.data?.can_cancel && <button type="button" disabled={busy || ["cancel_requested", "cancel_pending"].includes(run.data.status)} onClick={() => void perform(async () => { await projectRunsApi.cancel(projectId, run.data!.kind, run.data!.id); })}><SquareIcon size={13} />{["cancel_requested", "cancel_pending"].includes(run.data.status) ? "取消中…" : "取消运行"}</button>}
                {run.data?.can_retry && entry.item.kind === "analysis_run" && <button type="button" disabled={busy} onClick={() => void perform(async () => { const job = await analysisJobApi.retry(entry.item.id); setPendingRun(job.id); })}>重新运行</button>}
              </div>
              {(run.data?.error_message || string(body.error_message)) && <p role="alert" className={styles.error}>{run.data?.error_message || string(body.error_message)}</p>}
              <HistoryPreview projectId={projectId} entry={entry} source={source.data} />
              <HistoryExports projectId={projectId} entry={entry} source={source.data} title={selected.title} />
              {versions.length > 1 && <details className={styles.versions}><summary>绘图版本 · {versions.length}</summary><ol>{versions.map((item, index) => <li key={entryKey(item)}><button type="button" aria-pressed={entryKey(entry) === entryKey(item)} onClick={() => { setVersion(entryKey(item)); setError(""); }}><span>版本 {versions.length - index} · {status(item.item.status)}</span><time>{date(item.item.created_at)}</time></button></li>)}</ol></details>}
              {continuable.has(entry.item.kind) && source.data.available !== false && <details className={styles.continue}><summary>从这里继续</summary><label>研究问题<SearchSelect aria-label="继续到研究问题" value={continueTask} onChange={event => setContinueTask(event.target.value)}><option value="">选择研究问题</option>{tasks.map(task => <option key={task.id} value={task.id}>{task.payload.title}</option>)}</SearchSelect></label><button type="button" disabled={busy || !continueTask} onClick={() => void perform(continueFrom)}>创建可编辑副本</button></details>}
              {!!configuration && <details><summary>保存参数</summary><ParameterValues value={configuration} /></details>}
              <details><summary>记录详情</summary><dl className={styles.metadata}><dt>类型</dt><dd>{historyKindLabel(entry.item.kind, "zh-CN")}</dd><dt>数据</dt><dd>{datasets.find(dataset => dataset.id === source.data!.dataset_id)?.original_filename || source.data.filename || "—"}</dd><dt>保存版本</dt><dd>{recordKinds.has(entry.item.kind) && !["analysis_run", "plot_run"].includes(entry.item.kind) && source.data.latest_revision > 1 ? <SearchSelect aria-label="查看历史修订" value={source.data.reference.revision} onChange={event => setSavedRevision({ key: selectedIdentity, value: Number(event.target.value) })}>{Array.from({ length: source.data.latest_revision }, (_, index) => source.data!.latest_revision - index).map(value => <option key={value} value={value}>版本 {value}{value === source.data!.latest_revision ? "（最新）" : ""}</option>)}</SearchSelect> : source.data.reference.revision}</dd>{selected.taskIds.length > 0 && <><dt>研究问题</dt><dd>{selected.taskIds.map(id => tasks.find(task => task.id === id)?.payload.title || "").join("、")}</dd></>}</dl><DownloadButton identity={`${entryKey(entry)}:${source.data.reference.sha256}:record`} download={signal => exportRecord(projectId, entry, source.data!, selected.title, signal)}>导出完整记录 JSON</DownloadButton></details>
            </>}
          </>}
          {!selected && !listing.isPending && <p className={styles.empty}>选择一条记录查看。</p>}
        </section>
      </div>
    </DialogContent>
  </Dialog>;
}

function ParameterValues({ value }: { value: unknown }) {
  const labels: Record<string, string> = { method_id: "方法", variables: "变量", categorical_variables: "类别变量", group_variable: "分组", target: "目标", features: "特征", feature_variables: "特征", target_variable: "目标", missing_value_policy: "缺失值处理", missing_policy: "缺失值处理", confidence_level: "置信水平", alternative: "备择假设", seed: "随机种子", random_seed: "随机种子", test_size: "测试集比例", models: "模型", columns: "字段", chart_type: "图表类型", width: "宽度", height: "高度", font_size: "字号", dpi: "DPI" };
  const values = Object.entries(object(value)).filter(([key, item]) => item !== null && item !== undefined && !["project_id", "dataset_id", "dataset_sha256", "schema_sha256", "request_id", "schema_version", "kind", "code", "statistical_run_id", "ml_run_id", "material_references"].includes(key));
  return <dl className={styles.parameters}>{values.map(([key, item]) => <div key={key}><dt>{labels[key] || key.replaceAll("_", " ")}</dt><dd>{typeof item === "object" && !Array.isArray(item) ? <ParameterValues value={item} /> : Array.isArray(item) ? item.map(value => typeof value === "object" ? string(object(value).name) || string(object(value).id) || "已保存设置" : String(value)).join("、") || "—" : typeof item === "boolean" ? item ? "是" : "否" : String(item)}</dd></div>)}</dl>;
}

function HistoryPreview({ projectId, entry, source }: { projectId: string; entry: Entry; source: ResolvedSource }) {
  const body = object(object(source.payload.record).payload);
  const reportSources = useQuery({ queryKey: ["workspace-history-report-sources", projectId, source.reference.id, source.reference.revision], queryFn: ({ signal }) => projectResearchApi.reportSources(projectId, source.reference.id, source.reference.revision, signal), enabled: entry.item.kind === "report", retry: false });
  if (entry.item.kind === "statistics") {
    const run = source.payload as unknown as StatisticalRun;
    return run.result ? <StatisticsResultView result={run.result} inputConfig={run.input_config} /> : <p>本次运行未生成结果。</p>;
  }
  if (entry.item.kind === "report") return <article className={styles.report}><h3>{string(body.title)}</h3>{string(body.subtitle) && <p>{string(body.subtitle)}</p>}{reportSources.isError && <p role="alert">{reportSources.error.message}</p>}{(Array.isArray(body.blocks) ? body.blocks as ReportBlock[] : []).map(block => {
    const displayed = displayReportBlock(block, reportSources.data?.[block.id]);
    return <section key={block.id}><h3>{block.title}</h3>{block.body && <p>{block.body}</p>}{externalReportFrame(block) && !displayed.source_snapshot ? <p role="status">{reportSources.isPending ? "正在读取保存的结果…" : "该结果暂时无法读取，请重试。"}</p> : <ReportSourceView block={displayed} />}{block.caption && <p>{block.caption}</p>}</section>;
  })}</article>;
  if (reportable.has(entry.item.kind)) {
    if (entry.item.kind === "plot_run" && source.payload.status !== "completed") return <p>{string(body.error_message) || status(string(body.status) || entry.item.status)}</p>;
    return <ReportSourceView block={{ id: entry.item.id, title: entry.item.title, body: "", caption: "", source: source.reference, source_snapshot: source.payload }} />;
  }
  const question = string(body.question || body.research_question || body.confirmed_information);
  return <div className={styles.draft}>{question && <p>{question}</p>}{entry.item.kind === "analysis_run" ? <p>{status(string(body.status) || entry.item.status)}</p> : <p>{historyKindLabel(entry.item.kind, "zh-CN")}已保存。</p>}</div>;
}

async function exportRecord(projectId: string, entry: Entry, source: ResolvedSource, title: string, signal: AbortSignal) {
  const verify = async () => { const fresh = await projectResearchApi.source(projectId, entry.item.kind, entry.item.id, source.reference.revision, signal); if (fresh.reference.sha256 !== source.reference.sha256) throw new Error("记录已变化，请重新打开后导出。"); };
  await verify();
  return saveGeneratedFile(new Blob([JSON.stringify(source, null, 2)], { type: "application/json;charset=utf-8" }), `${title}-记录.json`, signal, verify, "zh-CN");
}

function HistoryExports({ projectId, entry, source, title }: { projectId: string; entry: Entry; source: ResolvedSource; title: string }) {
  const kind = entry.item.kind;
  const identityKey = `${entryKey(entry)}:${source.reference.sha256}`;
  async function document(signal: AbortSignal) {
    const fresh = await projectResearchApi.source(projectId, kind, entry.item.id, source.reference.revision, signal);
    if (fresh.reference.sha256 !== source.reference.sha256) throw new Error("记录已变化，请重新打开后导出。");
    if (kind === "report") {
      const saved = object(object(fresh.payload.record).payload) as unknown as ReportDocument;
      const sources = await projectResearchApi.reportSources(projectId, fresh.reference.id, fresh.reference.revision, signal);
      return { ...saved, blocks: saved.blocks.map(block => { const displayed = displayReportBlock(block, sources[block.id]); if (externalReportFrame(block) && !displayed.source_snapshot) throw new Error("有保存的结果暂时无法读取，请重试后导出。"); return displayed; }) };
    }
    return { schema_version: 1 as const, kind: "report" as const, title, subtitle: "", blocks: [{ id: entry.item.id, title, body: "", caption: "", source: fresh.reference, source_snapshot: fresh.payload }] };
  }
  async function exportDocument(format: "html" | "md" | "csv", signal: AbortSignal) {
    const saved = await document(signal);
    const csvCell = (value: string) => { const safe = /^[=+\-@\t\r]/.test(value.trimStart()) && !Number.isFinite(Number(value.replaceAll(",", ""))) ? "'" + value : value; return `"${safe.replaceAll('"', '""')}"`; };
    const text = format === "html" ? reportHtml(saved, "zh-CN", "") : format === "md" ? reportMarkdown(saved, "zh-CN") : "\uFEFF" + saved.blocks.flatMap(block => reportTables(block, "zh-CN")).map(table => `${table.columns.map(column => csvCell(column.label)).join(",")}\r\n${table.rows.map(row => table.columns.map(column => csvCell(displayValue(row[column.key], column.kind, "zh-CN"))).join(",")).join("\r\n")}`).join("\r\n\r\n");
    return saveGeneratedFile(new Blob([text], { type: format === "html" ? "text/html;charset=utf-8" : format === "md" ? "text/markdown;charset=utf-8" : "text/csv;charset=utf-8" }), `${title}.${format}`, signal, async () => { await document(signal); }, "zh-CN");
  }
  return <div className={styles.exports}>
    {kind === "plot_run" && source.payload.status === "completed" && (["png", "svg", "pdf"] as const).map(format => <DownloadButton key={format} identity={`${identityKey}:${format}`} download={signal => downloadApiFile(plotsApi.file(projectId, entry.item.id, format), `${title}.${format}`, signal)}>导出 {format.toUpperCase()}</DownloadButton>)}
    {["statistics", "ml", "quality", "workflow"].includes(kind) && <DownloadButton identity={`${identityKey}:csv`} download={signal => exportDocument("csv", signal)}>导出结果 CSV</DownloadButton>}
    {(reportable.has(kind) || kind === "report") && <><DownloadButton identity={`${identityKey}:html`} download={signal => exportDocument("html", signal)}>导出 HTML</DownloadButton>{kind === "report" && <DownloadButton identity={`${identityKey}:md`} download={signal => exportDocument("md", signal)}>导出 Markdown</DownloadButton>}</>}
  </div>;
}
