"use client";

import { WorkspaceToolDialog } from "./workspace-tool-dialog";
import { SearchSelect } from "@/components/ui/search-select";
import {watchProcessing} from "./workspace-run-notice";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { datasetsApi } from "@/lib/api-endpoints";
import { materialApi } from "@/lib/material-api";
import type { Material } from "@/lib/material-types";
import type { Dataset } from "@/lib/types";
import { DownloadButton } from "./download-button";
import styles from "./workspace-transcription.module.css";
import { useAnnotationSource } from "./workspace-annotations";
import type { AnnotationLocation } from "@/lib/text-coding";

type Segment = { id: number; start: number; end: number; text: string };
type Run = {
  id: string; revision: number; title: string; status: string; progress: string;
  error_message: string | null; started_at: string; machine_segments: Segment[];
  segments: Segment[]; text_id: string | null; srt_id: string | null;
  versions: { version?: number; text_id: string; srt_id: string }[];
};
const inProgress = (run: Run) => run.status === "queued" || run.status === "running";
const clock = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(1).padStart(4, "0")}`;
};

export function WorkspaceTranscription({ dataset, taskId, onChoose, initialRange }: {
  dataset: Dataset; taskId?: string; onChoose?: (id: string) => void;
  initialRange?: {start:number;end:number};
}) {
  const endpoint = `/api/v1/datasets/${dataset.id}/transcriptions`;
  const queryClient = useQueryClient();
  const [opened, setOpened] = useState(false);
  function openEditor() {
    if(initialRange) {
      setStart(String(initialRange.start));setEnd(String(initialRange.end));
      pendingTime.current=initialRange;
    }
    setOpened(true);
  }
  const [selectedId, setSelectedId] = useState("");
  const [language, setLanguage] = useState("auto");
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("");
  const [segments, setSegments] = useState<Segment[]>([]);
  const [checked, setChecked] = useState<number[]>([]);
  const [historyId, setHistoryId] = useState("");
  const [historyAnnotatable, setHistoryAnnotatable] = useState(false);
  const [sourceRevision, setSourceRevision] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [url, setUrl] = useState("");
  const [current, setCurrent] = useState(0);
  const media = useRef<HTMLMediaElement | null>(null);
  const stopAt = useRef<number|null>(null);
  const refreshed = useRef("");
  const segmentNodes = useRef(new Map<number, HTMLElement>());
  const pendingReveal = useRef<{ materialId: string; locator: AnnotationLocation } | null>(null);
  const pendingTime = useRef<{ start: number; end: number } | null>(null);
  const pendingSegment = useRef<number | null>(null);
  const runs = useQuery({
    queryKey: ["transcriptions", dataset.id],
    enabled: opened,
    queryFn: ({ signal }) => apiFetch<Run[]>(endpoint, { signal }),
    refetchInterval: query => query.state.data?.some(inProgress) ? 1500 : false,
  });
  const material = useQuery({
    queryKey: ["source-material", dataset.id, dataset.sha256],
    queryFn: ({ signal }) => apiFetch<Material>(`/api/v1/datasets/${dataset.id}/source-material`, { method: "POST", signal }),
  });
  const selected = runs.data?.find(run => run.id === selectedId) || runs.data?.[0];
  useEffect(() => {
    if (!selected || selected.status !== "completed" || historyId) return;
    const identity = `${selected.id}:${selected.revision}`;
    if (identity !== sourceRevision) {
      setSegments(selected.segments.map(segment => ({ ...segment })));
      setSourceRevision(identity);
    }
  }, [selected, sourceRevision, historyId]);
  useEffect(() => {
    if (!selected || selected.status !== "completed" || refreshed.current === selected.id) return;
    refreshed.current = selected.id;
    void queryClient.invalidateQueries({ queryKey: ["datasets"] });
  }, [selected, queryClient]);
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = "";
    setUrl("");
    if (opened && material.data) {
      void materialApi.blob(material.data.id, controller.signal).then(blob => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
      }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法读取媒体。"); });
    }
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [opened, material.data]);

  async function showVersion(run: Run, textId: string) {
    const version = await apiFetch<{ segments: Segment[]; annotatable: boolean }>(`${endpoint}/${run.id}/versions/${textId}`);
    setSelectedId(run.id); setHistoryId(textId); setHistoryAnnotatable(version.annotatable);
    setSegments(version.segments); setChecked([]);
    return version.segments;
  }
  useEffect(()=>{
    function openSavedVersion() {
      const key="statforge:open-transcript:"+dataset.id;
      const stored=sessionStorage.getItem(key);
      if(!stored)return;
      sessionStorage.removeItem(key);setOpened(true);
      const target=JSON.parse(stored) as {runId:string;textId:string;format:string};
      void apiFetch<Run>(endpoint+"/"+target.runId).then(async run=>{
        const version=run.versions.find(item=>item.text_id===target.textId||item.srt_id===target.textId);
        if(!version)throw new Error("这份转录版本暂时不可用，请选择已保存的版本。");
        await showVersion(run,version.text_id);
      }).catch(reason=>setError(reason instanceof Error?reason.message:"无法打开转录版本。"));
    }
    openSavedVersion();
    window.addEventListener("statforge:open-transcript-version",openSavedVersion);
    return ()=>window.removeEventListener("statforge:open-transcript-version",openSavedVersion);
  },[dataset.id,endpoint]);

  async function reveal(locator: AnnotationLocation) {
    if (locator.kind !== "time") return;
    const transcript = (locator as typeof locator & { transcription?: { run_id: string; text_id: string; segment_ids: number[] } }).transcription;
    if (transcript) {
      const run = runs.data?.find(item => item.id === transcript.run_id) || await apiFetch<Run>(`${endpoint}/${transcript.run_id}`);
      await showVersion(run, transcript.text_id);
      setChecked(transcript.segment_ids);
      pendingSegment.current = transcript.segment_ids[0] ?? null;
    }
    pendingTime.current = { start: locator.start_seconds, end: locator.end_seconds };
    if (media.current) { media.current.currentTime = locator.start_seconds; stopAt.current = locator.end_seconds; pendingTime.current = null; }
  }
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<{ materialId: string; locator: AnnotationLocation }>).detail;
      if (!detail) return;
      pendingReveal.current = detail;
      if (material.data?.id === detail.materialId) {
        pendingReveal.current = null; setOpened(true);
        void reveal(detail.locator).catch(reason => setError(reason instanceof Error ? reason.message : "无法定位转录。"));
      }
    };
    window.addEventListener("statforge:reveal-transcript", listener);
    const pending = pendingReveal.current;
    if (pending && material.data?.id === pending.materialId) {
      pendingReveal.current = null; setOpened(true);
      void reveal(pending.locator).catch(reason => setError(reason instanceof Error ? reason.message : "无法定位转录。"));
    }
    return () => window.removeEventListener("statforge:reveal-transcript", listener);
  });
  useEffect(() => {
    if (material.data?.id) window.dispatchEvent(new CustomEvent("statforge:transcript-ready", { detail: { materialId: material.data.id } }));
  }, [material.data?.id]);
  useEffect(() => {
    if (url && media.current && pendingTime.current) {
      media.current.currentTime = pendingTime.current.start; setCurrent(pendingTime.current.start); stopAt.current = pendingTime.current.end; pendingTime.current = null;
    }
  }, [url]);
  const displayedVersion=selected?.versions.find(version=>version.text_id===historyId);
  const exportTextId=displayedVersion?.text_id||selected?.text_id;
  const exportSrtId=displayedVersion?.srt_id||selected?.srt_id;
  const annotation = useAnnotationSource({ filename: dataset.original_filename, task_id: taskId,
    material_id: material.data?.id, material_sha256: material.data?.sha256, material_version: material.data?.version,
    sample_id: dataset.id, sample_collection_id: dataset.id }, reveal, opened && !!material.data);
  function annotateSelected() {
    if (!selected || !material.data) return;
    const picked = segments.filter(item => checked.includes(item.id));
    const quote = picked.map(item => item.text.trim()).join("\n");
    const textId = historyId || selected.text_id;
    const version = selected.versions.findIndex(item => item.text_id === textId) + 1;
    if (!picked.length || !quote.trim() || !textId || !version) { setError("请选择有文字的转录片段。"); return; }
    if (Array.from(quote).length > 2000) { setError("所选文字超过 2000 字，请缩小选段。"); return; }
    if (!historyId && JSON.stringify(segments) !== JSON.stringify(selected.segments)) { setError("请先保存文字修订，再添加标注。"); return; }
    if (historyId && !historyAnnotatable) { setError("无法验证这份转录修订，请重新打开。"); return; }
    annotation.annotate({ kind: "time", start_seconds: Math.min(...picked.map(item => item.start)),
      end_seconds: Math.max(...picked.map(item => item.end)), note: "",
      transcription: { run_id: selected.id, text_id: textId, version, segment_ids: picked.map(item => item.id), quote }
    } as AnnotationLocation);
    setOpened(false);
  }

  async function begin() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await apiFetch<Run>(endpoint, { method: "POST", body: JSON.stringify({
        task_id: taskId || null, language, start: Number(start), end: end.trim() ? Number(end) : null,
      }) });
      watchProcessing({project:dataset.project_id,task:taskId,source:dataset.id,url:endpoint+"/"+result.id,title:"转录"});
      setSelectedId(result.id); setHistoryId(""); setChecked([]);
      await runs.refetch();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法开始转录。"); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!selected || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await apiFetch<Run>(`${endpoint}/${selected.id}`, { method: "PUT", body: JSON.stringify({
        expected_revision: selected.revision, segments, task_id: taskId || null,
      }) });
      setSourceRevision(`${result.id}:${result.revision}`);
      setSegments(result.segments); setHistoryId(""); setChecked([]);
      await Promise.all([runs.refetch(), queryClient.invalidateQueries({ queryKey: ["datasets"] }), queryClient.invalidateQueries({ queryKey: ["coding", dataset.project_id] })]);
      setNotice("修订已保存。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存修订失败。"); }
    finally { setBusy(false); }
  }
  function play(segment: Segment) {
    if (!media.current) return;
    stopAt.current = segment.end;
    media.current.currentTime = segment.start;
    void media.current.play().catch(() => setError("请使用播放器播放对应片段。"));
  }
  function updatePlayback(){
    const player=media.current;if(!player)return;
    if(stopAt.current!==null && player.currentTime>=stopAt.current){const end=stopAt.current;stopAt.current=null;player.pause();player.currentTime=end;}
    setCurrent(player.currentTime);
  }
  function locate() {
    const position = media.current?.currentTime ?? 0;
    const segment = segments.find(item => item.start <= position && position < item.end);
    if (segment) segmentNodes.current.get(segment.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  return <section className={styles.root}>
    <button type="button" onClick={openEditor}>转录与校对</button>
    <WorkspaceToolDialog open={opened} onClose={()=>setOpened(false)} title="转录与校对" subtitle={dataset.original_filename}>
    {opened && <>
      <div className={styles.controls}>
        <label>语言 <SearchSelect value={language} onChange={event => setLanguage(event.target.value)}>
          <option value="auto">自动识别</option><option value="en">英语</option><option value="zh">中文</option>
        </SearchSelect></label>
        <label>开始秒 <input type="number" min="0" step="0.1" value={start} onChange={event => setStart(event.target.value)} /></label>
        <label>结束秒 <input type="number" min="0" step="0.1" placeholder="到结尾" value={end} onChange={event => setEnd(event.target.value)} /></label>
        <button type="button" className="rw-primary" disabled={busy || !!runs.data?.some(inProgress)} onClick={() => void begin()}>开始转录</button>
      </div>
      {runs.data && runs.data.length > 1 && <label>转录记录 <SearchSelect value={selected?.id || ""} onChange={event => { setSelectedId(event.target.value); setHistoryId(""); setChecked([]); }}>
        {runs.data.map(run => <option key={run.id} value={run.id}>{new Date(run.started_at).toLocaleString()} · {run.status === "completed" ? "已完成" : run.progress}</option>)}
      </SearchSelect></label>}
      {runs.isError && <p role="alert">{runs.error.message}</p>}
      {selected && inProgress(selected) && <p role="status">{selected.progress || "正在转录…"}</p>}
      {selected?.status === "failed" && <p role="alert">{selected.error_message || "转录失败，请重试。"}</p>}
      {material.isError && <p role="alert">{material.error.message}</p>}
      {url && (dataset.file_format.toLowerCase() === "wav"
        ? <audio controls src={url} ref={node => { media.current = node; }} onTimeUpdate={updatePlayback} onSeeked={locate} onLoadedMetadata={()=>{if(media.current){media.current.currentTime=pendingTime.current?.start ?? current;stopAt.current=pendingTime.current?.end ?? null;pendingTime.current=null;}}} />
        : <video controls src={url} ref={node => { media.current = node; }} onTimeUpdate={updatePlayback} onSeeked={locate} onLoadedMetadata={()=>{if(media.current){media.current.currentTime=pendingTime.current?.start ?? current;stopAt.current=pendingTime.current?.end ?? null;pendingTime.current=null;}}} />)}
      {selected?.status === "completed" && <>
        <p>点击时间回听，可修改最新文字；选择片段后可加入主题标注。</p>
        {selected.versions.length > 1 && <label>修订版本 <SearchSelect value={historyId || selected.text_id || ""} onChange={event => {
          const id = event.target.value; if (id === selected.text_id) { setHistoryId(""); setSourceRevision(""); setChecked([]); }
          else void showVersion(selected, id).catch(reason => setError(reason instanceof Error ? reason.message : "无法读取修订。"));
        }}>{selected.versions.map((version, index) => <option key={version.text_id} value={version.text_id}>版本 {index + 1}{version.text_id === selected.text_id ? "（最新）" : ""}</option>)}</SearchSelect></label>}
        <div className={styles.segments}>
          {segments.map((segment, index) => <div key={segment.id} ref={node => { if (node) {
            segmentNodes.current.set(segment.id, node);
            if (pendingSegment.current === segment.id) { node.scrollIntoView({ block: "nearest", behavior: "smooth" }); pendingSegment.current = null; }
          } else segmentNodes.current.delete(segment.id); }}
            className={styles.segment} data-active={segment.start <= current && current < segment.end || undefined}>
            <input type="checkbox" aria-label={`选择转录片段 ${index + 1}`} checked={checked.includes(segment.id)} onChange={event => setChecked(previous => event.target.checked ? [...previous, segment.id] : previous.filter(id => id !== segment.id))} />
            <button type="button" onClick={() => play(segment)}>{clock(segment.start)}–{clock(segment.end)}</button>
            <textarea readOnly={!!historyId} aria-label={`转录片段 ${index + 1}`} value={segment.text} onChange={event => setSegments(previous => previous.map(item => item.id === segment.id ? { ...item, text: event.target.value } : item))} />
          </div>)}
        </div>
        {!segments.length && <p>这段音频没有识别到文字。</p>}
        <div className={styles.controls}>
          {historyId && <button type="button" onClick={() => { setHistoryId(""); setSourceRevision(""); setChecked([]); }}>返回最新编辑</button>}
          <button type="button" disabled={busy || !!historyId} onClick={() => void save()}>保存修订</button>
          {annotation.enabled && <button type="button" disabled={!checked.length} onClick={annotateSelected}>标注所选片段</button>}
          {exportTextId && <><DownloadButton identity={exportTextId} download={signal => datasetsApi.download(exportTextId, "转录.txt", signal)}>导出 TXT</DownloadButton>
            {onChoose && <button type="button" onClick={() => {setOpened(false);onChoose(exportTextId!);}}>打开文字</button>}</>}
          {exportSrtId && <DownloadButton identity={exportSrtId} download={signal => datasetsApi.download(exportSrtId, "转录.srt", signal)}>导出 SRT</DownloadButton>}
        </div>
        <details><summary>查看机器原始转录</summary>{selected.machine_segments.map(segment => <p key={segment.id}>{clock(segment.start)} {segment.text}</p>)}</details>
      </>}
      {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    </>}
    </WorkspaceToolDialog>
  </section>;
}
