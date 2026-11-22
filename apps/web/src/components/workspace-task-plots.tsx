"use client";

import { Download, Pencil, Sparkles } from "lucide-react";
import { PlotMenu, PlotViewport, PlotZoom, PlotDownloads } from "./task-plot-view";
import { AnalysisOwnership, ResultNavigation } from "./workspace-result-navigation";
import { SearchSelect } from "@/components/ui/search-select";

import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { workspaceApi, type PlotRun, type WorkspaceRecord } from "@/lib/workspace-api";
import { plotActive } from "@/lib/plots-api";
import { groupTaskPlots, plotGroupId, plotCodeKind } from "@/lib/task-plot-groups";
import { PlotWorkbench } from "./plot-workbench";
import { ConfirmDialog } from "./confirm-dialog";

type Run = WorkspaceRecord<PlotRun>;
type OpenEditor = { key: string; draftId: string; runId?: string; datasetId?: string };

function plotName(row: Run, filename?: string) {
  const kind = plotCodeKind(row.payload.code);
  return kind && filename && row.payload.title === kind + " · " + filename ? kind : row.payload.title;
}
const runStatus = (row: Run) => row.payload.status === "completed" ? "已完成" : plotActive(row.payload.status) ? "绘图中" : row.payload.status === "cancelled" ? "已取消" : "未完成";
export function WorkspaceTaskPlots({ projectId, taskId, ids, versionOrder, selectedId, onSelect, onAdded, onRemove, open, visible, onOpen, onStyle, onClose, initialDatasetId, initialDraftId, openRequest = 0 }: {
  projectId: string;
  taskId: string;
  ids: string[];
  versionOrder?: Record<string, string[]>;
  selectedId: string;
  onSelect: (id: string) => void;
  onAdded: (run: Run) => Promise<void>;
  onRemove: (id: string, group: string, order: string[]) => Promise<void>;
  open: boolean;
  visible: boolean;
  onOpen: () => void;
  onStyle: (run: Run, name: string) => void;
  onClose: () => void;
  initialDatasetId: string | null;
  initialDraftId?: string | null;
  openRequest?: number;
}) {
  const nav = useContext(ResultNavigation);
  const nameOf = (row: Run) => plotName(row, nav?.datasets.find(d=>d.id===row.payload.scope.dataset_id)?.original_filename);
  const [zoom, setZoom] = useState(100);
  const pendingActiveRun = useRef("");
  const [compare, setCompare] = useState("");
  const [removal, setRemoval] = useState<{row: Run; label: string; group: string; order: string[]} | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const [editors, setEditors] = useState<OpenEditor[]>([]);
  const [activeEditor, setActiveEditor] = useState("");
  const handledOpen = useRef(false);
  const explicitOpen = useRef(false);
  const previousDraft = useRef(initialDraftId);
  const previousRequest = useRef(openRequest);
  const runs = useQuery({
    queryKey: ["task-plots", taskId, ids],
    queryFn: ({ signal }) => Promise.all(ids.map(async (id) => {
      const record = await workspaceApi.read(id, signal);
      if (record.project_id !== projectId || record.payload.kind !== "plot_run") throw new Error("图形来源不可用");
      return record as Run;
    })),
    enabled: ids.length > 0,
    refetchInterval: (query) => query.state.data?.some((row) => plotActive(row.payload.status)) ? 1000 : false,
  });
  const rows = runs.data ?? [];
  useEffect(() => {
    if (!visible || !open) { pendingActiveRun.current = ""; return; }
    const pending = rows.find(row => row.id === pendingActiveRun.current);
    if (pending?.payload.status === "completed") { pendingActiveRun.current = ""; onSelect(pending.id); }
    else if (pending && !plotActive(pending.payload.status)) pendingActiveRun.current = "";
  }, [visible, open, rows, onSelect]);
  const groups = groupTaskPlots(rows, versionOrder);
  function versionLabel(row: Run) {
    const order = groups.find(item => item.id === plotGroupId(row, rows, versionOrder))!.order;
    return `版本 ${order.indexOf(row.id) + 1}${row.payload.metadata.style_only === true ? " · 样式修改" : ""}`;
  }
  const requested = rows.find((row) => row.id === selectedId);
  const group = (requested ? groups.find((item) => item.id === plotGroupId(requested, rows, versionOrder)) : undefined) ?? groups.at(-1);
  const selected = requested?.payload.status === "completed" ? requested : group?.current;
  const comparison = rows.find((row) => row.id === compare && row.id !== selected?.id && row.payload.status === "completed");
  useEffect(()=>{setZoom(100);},[selected?.id]);
  const activateEditor = useCallback((row?: Run, draftId?: string | null) => {
    const sourceDraft = draftId || row?.payload.draft_id || "";
    const runId = row?.payload.status === "completed" ? row.id : undefined;
    const key = sourceDraft ? `${sourceDraft}:${runId ?? "draft"}` : "new";
    setEditors((prior) => prior.some((editor) => editor.key === key) ? prior : [...prior, {
      key, draftId: sourceDraft, runId,
      datasetId: row?.payload.scope.dataset_id ?? initialDatasetId ?? undefined,
    }]);
    setActiveEditor(key);
  }, [initialDatasetId]);
  useEffect(() => {
    if (!open) {
      handledOpen.current = false;
      previousDraft.current = initialDraftId;
      return;
    }
    if (explicitOpen.current) {
      explicitOpen.current = false;
      handledOpen.current = true;
      previousDraft.current = initialDraftId;
      return;
    }
    if (handledOpen.current && previousDraft.current === initialDraftId && previousRequest.current === openRequest) return;
    if (selectedId && !requested && !initialDraftId && runs.isPending) return;
    activateEditor(initialDraftId ? undefined : requested, initialDraftId);
    handledOpen.current = true;
    previousDraft.current = initialDraftId;
    previousRequest.current = openRequest;
  }, [open, selectedId, requested, initialDraftId, openRequest, activateEditor, runs.isPending]);
  const edit = (row: Run) => {
    explicitOpen.current = !open;
    activateEditor(row);
    onSelect(row.id);
    onOpen();
  };
  async function removeVersion() {
    if (!removal || removing) return;
    const id = removal.row.id;
    setRemoving(true);
    setRemoveError("");
    try {
      await onRemove(id, removal.group, removal.order);
      let nextId = selected?.id;
      if (id === selected?.id || id === selectedId) {
        const remaining = groupTaskPlots(rows.filter(row => row.id !== id), versionOrder);
        const next = remaining.find(item => item.id === removal.group) ?? remaining.at(-1);
        nextId = next?.current.id;
        onSelect(nextId ?? "");
      }
      setCompare(prior => prior === id || prior === nextId ? "" : prior);
      setEditors(prior => prior.filter(editor => editor.runId !== id));
      if (pendingActiveRun.current === id) pendingActiveRun.current = "";
      setRemoval(null);
    } catch (reason) {
      setRemoveError(reason instanceof Error ? reason.message : "删除未保存，请重试。");
    } finally {
      setRemoving(false);
    }
  }
  if (!ids.length && !open && !editors.length) return null;
  return <section className={`rw-task-plots ${open ? "is-open" : ""}`} hidden={!open && !ids.length}>
    {open && <div className="rw-content-tools"><h3>绘图工作台</h3><button onClick={onClose}>返回图表</button></div>}
    {!!ids.length && runs.isPending && <p role="status">正在读取图形…</p>}
    {runs.isError && <p role="alert" className="rw-error">{runs.error.message}<button onClick={() => void runs.refetch()}>重试</button></p>}
    {!open && selected && group && <div className="qp-reader">
      <div className="qp-toolbar">
        <div className="qp-identity">{groups.length===1 ? <h2 title={selected.payload.title}>{nameOf(selected)}</h2> :
          <SearchSelect aria-label="当前图形" value={group.id} onChange={e=>{const next=groups.find(g=>g.id===e.target.value);if(next)onSelect(next.current.id);}}>
            {groups.map(g=><option key={g.id} value={g.id}>{g.current.payload.title}{!g.completed ? " · "+runStatus(g.current):""}</option>)}
          </SearchSelect>}
          <PlotMenu title="版本历史与比较" label={versionLabel(selected)}>{close=><>
            <h4>版本历史</h4>
            {group.versions.slice().reverse().map(row=><div className="qp-version" key={row.id}>
              <div><button disabled={row.payload.status!=="completed"} aria-pressed={selected.id===row.id} onClick={()=>{onSelect(row.id);close();}}>{versionLabel(row)}</button>
              <button onClick={()=>{close();edit(row);}}>编辑</button>
              <button className="qp-delete-version" aria-label={`删除${versionLabel(row)}`} disabled={plotActive(row.payload.status)}
                title={plotActive(row.payload.status) ? "绘图结束后可删除" : undefined}
                onClick={()=>{close();setRemoveError("");setRemoval({row,label:versionLabel(row),group:group.id,order:group.order});}}>删除</button></div>
              <small>{runStatus(row)}{row.payload.started_at && " · "+new Date(row.payload.started_at).toLocaleString("zh-CN")}</small>
              {row.payload.error_message&&<p role="alert">{row.payload.error_message}</p>}
            </div>)}
            {rows.some(row=>row.id!==selected.id&&row.payload.status==="completed")&&<label>并排比较<SearchSelect aria-label="并排比较绘图版本" value={comparison?.id||""} onChange={e=>{setCompare(e.target.value);close();}}>
              <option value="">不比较</option>{rows.filter(row=>row.id!==selected.id&&row.payload.status==="completed").map(row=><option key={row.id} value={row.id}>{row.payload.title} · {versionLabel(row)}</option>)}
            </SearchSelect></label>}
          </>}</PlotMenu>
        </div>
        <div className="qp-actions"><button onClick={()=>edit(selected)}><Pencil size={15}/>编辑图形</button>
          {selected.payload.status==="completed"&&<><button onClick={()=>onStyle(selected,nameOf(selected))}><Sparkles size={15}/>AI 调图</button>
            <PlotMenu title="导出当前图形" label={<><Download size={15}/>导出</>}>{()=> <PlotDownloads project={projectId} run={selected}/>}</PlotMenu></>}
        </div>
      </div>
      <div className="qp-meta"><AnalysisOwnership compact taskId={taskId} title={selected.payload.title} scope={selected.payload.scope} view="图表"/>
        {selected.payload.status==="completed"&&<PlotZoom zoom={zoom} change={setZoom}/>}
      </div>
      <div className={comparison?"qp-images qp-comparing":"qp-images"}>
        <div className="qp-column">{comparison&&<strong>{nameOf(selected)} · {versionLabel(selected)}</strong>}
          <PlotViewport key={selected.id+":"+selected.revision} project={projectId} run={selected} zoom={zoom}/></div>
        {comparison&&<div className="qp-column"><div className="qp-comparison-head"><strong>{nameOf(comparison)} · {versionLabel(comparison)}</strong>
          <PlotMenu title="导出对照图" label="导出">{()=> <PlotDownloads project={projectId} run={comparison}/>}</PlotMenu>
          <button onClick={()=>setCompare("")}>结束比较</button></div><PlotViewport key={comparison.id+":"+comparison.revision} project={projectId} run={comparison} zoom={zoom}/></div>}
      </div>
    </div>}
    {removal && <ConfirmDialog open onOpenChange={next => {if (!next && !removing) setRemoval(null);}}
      title={`删除「${nameOf(removal.row)}」的${removal.label}？`}
      description={<>从当前研究问题的图表中移除。项目绘图记录和其他版本保留。{removeError && <span role="alert" className="block rw-error">{removeError}</span>}</>}
      confirmLabel="删除版本" cancelLabel="取消" isLoading={removing} onConfirm={()=>void removeVersion()}/>}
    {editors.map((editor) => <div key={editor.key} hidden={!open || editor.key !== activeEditor}>
      <PlotWorkbench
        projectId={projectId} taskId={taskId}
        initialId={editor.draftId}
        initialRunId={editor.runId}
        initialDatasetId={editor.datasetId}
        embedded
        onRunCreated={async row => { if (visible && open && plotActive(row.payload.status)) pendingActiveRun.current = row.id; await onAdded(row); }}
      />
    </div>)}
  </section>;
}
