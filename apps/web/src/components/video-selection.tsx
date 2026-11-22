"use client";

import { useState } from "react";
import { ArrowLeftToLine, ArrowRightToLine, ChevronDown, Pause, Play, Tag } from "lucide-react";
import { useLanguage } from "@/providers/language-provider";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import styles from "./video-selection.module.css";
import annotations from "./workspace-annotations.module.css";

const time = (value: number) => Number(value.toFixed(3)).toString();

type SelectionProps = {
  duration: number | null; start: number; end: number; cursor: number; playing: boolean;
  onSelect: (start: number, end: number) => void; onPlay: () => void;
  readPosition: () => number; onAnnotate?: (point: boolean) => void;
};

export function VideoSelection({duration, start, end, cursor, playing, onSelect, onPlay, readPosition, onAnnotate}: SelectionProps) {
  const zh = useLanguage().locale === "zh-CN";
  const [problem, setProblem] = useState("");
  const [draft, setDraft] = useState<{edge: "start" | "end"; text: string} | null>(null);
  const ready = duration != null && duration > 0;
  const valid = ready && start >= 0 && end > start && end <= duration + 1e-6;
  const unit = zh ? "秒" : "s";

  function setEdge(edge: "start" | "end", value: number) {
    if (!ready) return;
    const from = edge === "start" ? value : start;
    // A duration displayed to milliseconds may round up by half a millisecond.
    const to = edge === "end" && value === Number(time(duration)) ? duration : edge === "end" ? value : end;
    if (!(from >= 0 && to > from && to <= duration + 1e-6)) {
      setProblem(zh ? "起点须早于终点，且两者均在材料时长内。" : "Set the start before the end, within the recording.");
      return;
    }
    setProblem("");
    if (from !== start || to !== end) onSelect(from, to);
  }
  function field(edge: "start" | "end") {
    const first = edge === "start";
    const label = first ? (zh ? "起点" : "Start") : (zh ? "终点" : "End");
    const at = zh ? "取播放位置为" + label : "Set " + label.toLowerCase() + " to playhead";
    const Icon = first ? ArrowRightToLine : ArrowLeftToLine;
    return <div className={styles.field}>
      <label>{label}<input aria-label={label + " (" + unit + ")"} inputMode="decimal" type="number" step="0.001" min="0" max={duration ?? undefined}
        disabled={!ready} value={draft?.edge === edge ? draft.text : time(first ? start : end)}
        onChange={e => setDraft({edge, text:e.target.value})}
        onBlur={() => {if (draft?.edge === edge) setEdge(edge, draft.text.trim() ? Number(draft.text) : NaN); setDraft(null);}}
        onKeyDown={e => {if (e.key === "Enter") e.currentTarget.blur();}}/><small>{unit}</small></label>
      <button type="button" title={at} aria-label={at} disabled={!ready} onClick={() => setEdge(edge, readPosition())}><Icon size={14}/></button>
    </div>;
  }
  const selection = time(start) + "–" + time(end) + " " + unit;
  return <section className={styles.selection} aria-label={zh ? "媒体选段" : "Media selection"}>
    <div className={styles.heading}><strong>{zh ? "当前选段" : "Selection"}</strong>
      <span>{ready ? selection + " · " + (zh ? "长度 " : "Length ") + time(end-start) + " " + unit : "—"}</span>
      <small>{zh ? "播放位置" : "Playhead"} {time(cursor)} / {ready ? time(duration) : "—"} {unit}</small></div>
    {ready && <div className={annotations.timeline} aria-label={zh ? "选段时间轴" : "Selection timeline"}>
      <div style={{left:(start/duration*100)+"%",width:(Math.max(0,end-start)/duration*100)+"%"}}/>
      <i className={styles.cursor} aria-hidden="true" style={{left:Math.min(100,cursor/duration*100)+"%"}}/>
      <input type="range" aria-label={zh ? "拖动选段开始" : "Drag selection start"} min="0" max={duration} step="0.001"
        value={start} onChange={e=>setEdge("start",Math.min(Number(e.target.value),Math.max(0,end-.001)))}/>
      <input type="range" aria-label={zh ? "拖动选段结束" : "Drag selection end"} min="0" max={duration} step="0.001"
        value={end} onChange={e=>setEdge("end",Math.min(duration,Math.max(Number(e.target.value),start+.001)))}/>
    </div>}
    <div className={styles.toolbar}>
      <div className={styles.fields}>{field("start")}<span aria-hidden="true">—</span>{field("end")}
        <button type="button" className={styles.full} disabled={!ready || (start===0 && end===duration)} onClick={()=>{if (ready) {setProblem("");onSelect(0,duration);}}}>{zh ? "全段" : "Full clip"}</button></div>
      <div className={styles.actions}>
        <button type="button" className="rw-primary" disabled={!valid} onClick={onPlay}>{playing ? <Pause size={14}/> : <Play size={14}/>}
          {playing ? (zh ? "暂停" : "Pause") : (zh ? "播放选段" : "Play selection")}</button>
        {onAnnotate && <DropdownMenu><DropdownMenuTrigger disabled={!ready}><Tag size={14}/>{zh ? "标注" : "Annotate"}<ChevronDown size={13}/></DropdownMenuTrigger>
          <DropdownMenuContent align="end" className={styles.menu}>
            <DropdownMenuItem disabled={!valid} onClick={()=>onAnnotate(false)}>{zh ? "标注选段" : "Annotate selection"}<small>{selection}</small></DropdownMenuItem>
            <DropdownMenuItem onClick={()=>onAnnotate(true)}>{zh ? "标记当前时间点" : "Mark current time"}<small>{time(cursor)} {unit}</small></DropdownMenuItem>
          </DropdownMenuContent></DropdownMenu>}
      </div>
    </div>
    {problem && <p className="rw-error" role="alert">{problem}</p>}
  </section>;
}
