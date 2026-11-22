"use client";

import { useContext, useState, type ReactNode } from "react";
import { ChartColumn, Check, Clock3, Columns2, Download, Ellipsis, ExternalLink, FileText, Link2, Plus, SlidersHorizontal, Table2 } from "lucide-react";
import { useLanguage } from "@/providers/language-provider";
import type { AnalysisDraft, WorkspaceRecord } from "@/lib/workspace-api";
import type { SaveOutcome } from "@/lib/api-download";
import { DownloadButton } from "./download-button";
import { ResultNavigation } from "./workspace-result-navigation";
import { PlotMenu } from "./task-plot-view";

type Analysis = WorkspaceRecord<AnalysisDraft>;
type Actions = { onAdd: () => void; onReference: () => void; canReference: boolean };

export function ResultActions({ onAdd, onReference, canReference }: Actions) {
  const { locale } = useLanguage();
  const zh = locale === "zh-CN";
  return <PlotMenu title={zh ? "更多分析操作" : "More analysis actions"} label={<Ellipsis size={19}/>} chevron={false}>
    {close => <div className="qr-menu-list">
      <button onClick={() => { close(); onAdd(); }}><Plus size={15}/>{zh ? "添加分析" : "Add analysis"}</button>
      <button disabled={!canReference} onClick={() => { close(); onReference(); }}><Link2 size={15}/>{zh ? "引用已有运行" : "Link existing run"}</button>
    </div>}
  </PlotMenu>;
}

export function ResultSource({ datasetId, name, createdAt }: { datasetId?: string | null; name?: string; createdAt?: string | null }) {
  const nav = useContext(ResultNavigation);
  const { locale } = useLanguage();
  const label = name || (locale === "zh-CN" ? "查看分析数据" : "View analysis data");
  return <div className="qr-result-meta">
    {datasetId && nav && <button title={label} onClick={() => nav.open({ datasetId })}><Table2 size={15}/><span>{label}</span><ExternalLink size={13}/></button>}
    {createdAt && <time dateTime={createdAt}><Clock3 size={15}/>{new Date(createdAt).toLocaleString(locale)}</time>}
  </div>;
}

type PickerProps = Actions & {
  rows: Analysis[]; current: Analysis; adoptedId?: string | null; comparison?: Analysis;
  onChoose: (id: string) => void; onCompare: (id: string) => void;
  close: () => void;
};
function ResultPicker({ rows, current, adoptedId, comparison, onChoose, onCompare, close, ...actions }: PickerProps) {
  const { locale } = useLanguage();
  const zh = locale === "zh-CN";
  const [comparing, setComparing] = useState(false);
  const [search, setSearch] = useState("");
  const candidates = comparing ? rows.filter(row => row.id !== current.id) : rows;
  const options = candidates.filter(row => row.payload.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  function toggleMode() { setComparing(!comparing); setSearch(""); }
  function choose(id: string) { (comparing ? onCompare : onChoose)(id); close(); setComparing(false); setSearch(""); }
  return <div className="qr-menu-list qr-result-picker">
    <strong>{comparing ? (zh ? "选择对照结果" : "Choose a comparison") : (zh ? "此研究问题的结果" : "Results in this task")}</strong>
    {candidates.length > 1 && <input type="search" aria-label={zh ? "查找结果" : "Find result"} placeholder={zh ? "查找结果…" : "Find result…"} value={search} onChange={e => setSearch(e.target.value)}/>}
    {options.map(row => <button key={row.id} aria-pressed={row.id === (comparing ? comparison?.id : current.id)} onClick={() => choose(row.id)}>
      <span>{row.payload.title}{row.id === adoptedId && <small>{zh ? "当前采用" : "Adopted"}</small>}</span>
      {row.id === (comparing ? comparison?.id : current.id) && <Check size={15}/>}
    </button>)}
    {options.length === 0 && (candidates.length ? <p>{zh ? "没有匹配的结果" : "No matching results"}</p> : <>
      <p>{zh ? "此研究问题只有一份结果。添加分析或引用另一份运行后即可比较。" : "Add an analysis or link another run to compare two results."}</p>
      <button onClick={() => { close(); actions.onAdd(); }}><Plus size={15}/>{zh ? "添加分析" : "Add analysis"}</button>
      <button disabled={!actions.canReference} onClick={() => { close(); actions.onReference(); }}><Link2 size={15}/>{zh ? "引用已有运行" : "Link existing run"}</button>
    </>)}
    <hr/>
    {comparing && comparison && <button onClick={() => choose("")}>{zh ? "结束比较" : "End comparison"}</button>}
    <button onClick={toggleMode}><Columns2 size={15}/>{comparing ? (zh ? "返回结果列表" : "Back to results") : (zh ? "并排比较…" : "Compare side by side…")}</button>
  </div>;
}

type ToolbarProps = Omit<PickerProps, "close"> & {
  heading: string; canEdit: boolean; canAdopt: boolean; canPlot: boolean; onEdit: () => void; onAdopt: () => void; onPlot: () => void;
  onDownload: (format: "csv" | "html", signal: AbortSignal) => Promise<void | SaveOutcome>; children: ReactNode;
};
export function TaskResultToolbar({ heading, canEdit, canAdopt, canPlot, onEdit, onAdopt, onPlot, onDownload, children, ...picker }: ToolbarProps) {
  const { locale } = useLanguage();
  const zh = locale === "zh-CN";
  const { current, rows, adoptedId } = picker;
  return <>
    <div className="qr-result-toolbar">
      <div className="qr-result-name"><h2>{heading}</h2>
        <PlotMenu label={(zh ? "结果 " : "Result ") + (rows.indexOf(current) + 1)} title={zh ? "查看结果与并排比较" : "Choose or compare results"}>
          {close => <ResultPicker {...picker} close={close}/>}
        </PlotMenu>
        {adoptedId === current.id ? (
          <span className="qr-adopted">
            <Check size={15}/>
            <span className="qr-adopt-label" data-action-label={zh ? "采用此结果" : "Adopt result"}>
              <span>{zh ? "当前采用" : "Adopted"}</span>
            </span>
          </span>
        ) : (
          <button className="qr-adopt" disabled={!canAdopt} onClick={onAdopt}>
            <Check size={15}/>{zh ? "采用此结果" : "Adopt result"}
          </button>
        )}
      </div>
      <div className="qr-result-tools">
        <button disabled={!canEdit} onClick={onEdit}><SlidersHorizontal size={15}/>{zh ? "修改设置" : "Edit settings"}</button>
        <button disabled={!canPlot} onClick={onPlot}><ChartColumn size={15}/>{zh ? "绘图" : "Plot"}</button>
        {canPlot && <div className="qr-result-export"><PlotMenu title={zh ? "导出结果" : "Export result"} label={<><Download size={15}/>{zh ? "导出" : "Export"}</>}>
          {() => <div className="qr-menu-list">
            <DownloadButton identity={current.id + ":csv"} download={s => onDownload("csv", s)}><Table2 size={15}/>{zh ? "导出统计表 CSV" : "Export table CSV"}</DownloadButton>
            <DownloadButton identity={current.id + ":html"} download={s => onDownload("html", s)}><FileText size={15}/>{zh ? "导出结果 HTML" : "Export result HTML"}</DownloadButton>
          </div>}
        </PlotMenu></div>}
        <ResultActions {...picker}/>
      </div>
    </div>
    {children}
  </>;
}
