"use client";

import { ConfirmDialog } from "./confirm-dialog";
import { WorkspaceToolDialog } from "./workspace-tool-dialog";
import { WorkspaceTranscription } from "./workspace-transcription";
import { SearchSelect } from "@/components/ui/search-select";
import {watchProcessing} from "./workspace-run-notice";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { materialApi } from "@/lib/material-api";
import type { Material, Location } from "@/lib/material-types";
import type { Dataset } from "@/lib/types";
import { MaterialViewer } from "./material-viewer";
import { AudioInspector } from "./workspace-audio-inspector";
import { MediaCurves } from "./workspace-media-results";
import { CaseMembersEditor } from "./case-members-editor";
import { CaseMaterialPicker } from "./case-material-picker";
import { WorkspaceSourceFile } from "./workspace-source-file";
import styles from "./workspace-media-samples.module.css";
import layout from "./workspace-media-layout.module.css";
import { MediaAssociationEditor, MediaProcessingOptions, defaultMediaOptions, mediaOptionErrors, type MediaOptions } from "./workspace-media-processing";

type ObjectRegion = { id: number; category: string; bbox: [number, number, number, number]; width: number; height: number };
type Sample = { attributes?:Record<string,string|number|null>; audio_sample_rate?:number; sample_id: string; title: string; case_title?:string; text_range?:{start:number;end:number|null}; primary_id?:string; members?:{id:string;sha256:string}[]; display_title?:string; actor_id?: string; emotion?: string; transcript?: string; video_id?: string; audio_id?: string; text_id?: string; audio_alignment?: {offset_seconds: number | null; content_reviewed: boolean}; image_id?: number; image_dataset_id?: string; captions?: { id: number; caption: string }[]; objects?: ObjectRegion[] };
type Run = { started_at?: string; dataset_id:string; title:string; failure_reason?:string; id: string; status: string; progress: string; parameters?: Record<string,unknown>; reused?:boolean; error_message?: string | null; result_ids: string[]; summary_id?: string | null; package_id?: string | null };
type Result = { kind: "ravdess" | "coco" | "media"; samples: Sample[]; runs: Run[]; selection?: { processing?:MediaOptions; sample_id: string; audio_source?: "independent" | "video"; start?: number | null; end?: number | null } | null };
type Match = { sample_id: string; title: string; score: number };
type Props = { mode?:"processing"; initialSourceId?:string; initialRange?:{start:number;end:number}; suggestions?:React.ReactNode; versionPicker?:React.ReactNode; dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; taskId?: string; onSaved?: () => Promise<void>; initialSampleId?: string; initialObjectId?: number };
const active = (status: string) => ["queued", "pending", "running", "processing"].includes(status.toLowerCase());

export function WorkspaceMediaSamples({ dataset, datasets, onChoose, taskId, onSaved, initialSampleId, initialObjectId, suggestions, mode, initialSourceId, initialRange, versionPicker }: Props) {
  const client = useQueryClient();
  const [toolOpen,setToolOpen]=useState(false);
  const [caseName,setCaseName]=useState("");
  const [editingCase,setEditingCase]=useState(false);
  const [confirmCaseDelete,setConfirmCaseDelete]=useState(false);
  const appliedRange=useRef("");
  const [sampleId, setSampleId] = useState("");
  const [objectId, setObjectId] = useState<number | undefined>(initialObjectId);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [searching, setSearching] = useState(false);
  const [matches, setMatches] = useState<Match[]>([]);
  const [audioOpen, setAudioOpen] = useState(false);
  const [mediaView, setMediaView] = useState<"video"|"audio">("video");
  const [processFull, setProcessFull] = useState(false);
  const cursor = useRef<number | undefined>(undefined);
  const pendingSelection = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queuedSelection = useRef<(() => void) | null>(null);
  useEffect(()=>()=>{if (pendingSelection.current) clearTimeout(pendingSelection.current);queuedSelection.current?.();},[]);
  const [draftOptions, setProcessing] = useState<MediaOptions>(defaultMediaOptions);
  const manualCountFor = useRef("");
  function countKey(id: string, from: string, to: string, options: MediaOptions) {
    return JSON.stringify([id, number(from) ?? 0, number(to), options.audio_source, options.speech_count_unit]);
  }
  function forSelection(id: string, from: string, to: string, options: MediaOptions) {
    if (options.speech_count == null || manualCountFor.current === countKey(id, from, to, options)) return options;
    return {...options, speech_count: undefined};
  }
  const processing = forSelection(sampleId, processFull ? "" : start, processFull ? "" : end, draftOptions);
  const viewKey = "statforge:material-results:" + dataset.id + ":" + (taskId || "project");
  const memoryKey = "statforge:sample-view:" + dataset.id + ":" + dataset.sha256 + ":" + (taskId || "project");
  const views=useRef<Record<string,{start:string;end:string;cursor?:number}>>({});
  const memoryLoaded=useRef("");
  if(typeof window!=="undefined" && memoryLoaded.current!==memoryKey) {
    memoryLoaded.current=memoryKey;
    try { views.current=JSON.parse(sessionStorage.getItem(memoryKey) || "{}"); } catch { views.current={}; }
  }
  function rememberView(id:string,from:string,to:string,position=cursor.current) {
    if(!id)return;
    views.current[id]={start:from,end:to,cursor:position};
    sessionStorage.setItem(memoryKey,JSON.stringify(views.current));
  }
  const cursorSavedAt=useRef(0);
  function rememberCursor(position:number) {
    cursor.current=position;
    if(Date.now()-cursorSavedAt.current>600) {cursorSavedAt.current=Date.now();rememberView(sampleId,start,end,position);}
  }
  const [panel, setPanel] = useState<"materials"|"results">("materials");
  const [resultId, setResultId] = useState("");
  function openPanel(view:"materials"|"results", runId = resultId) {
    setPanel(view); setResultId(runId);
    sessionStorage.setItem(viewKey,JSON.stringify({view,runId}));
  }
  const [notice, setNotice] = useState("");
  const initialized = useRef("");
  const lastSavedSelection = useRef("");
  const initialTarget = useRef("");
  const writes = useRef(Promise.resolve(true));
  const endpoint = `/api/v1/datasets/${dataset.id}`;
  const samples = useQuery({
    queryKey: ["media-samples", dataset.id, taskId],
    queryFn: ({ signal }) => apiFetch<Result>(`${endpoint}/samples${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`, { signal }),
  });
  useEffect(() => {
    if (!samples.data || initialized.current === `${dataset.id}:${taskId || ""}`) return;
    initialized.current = `${dataset.id}:${taskId || ""}`;
    const saved = samples.data.selection;
    const priorView = JSON.parse(sessionStorage.getItem(viewKey) || "{}");
    setPanel(priorView.view === "results" ? "results" : "materials"); setResultId(priorView.runId || "");
    lastSavedSelection.current = JSON.stringify([saved?.sample_id || "", saved?.start ?? null, saved?.end ?? null, saved?.audio_source ?? null]);
    let reveal:{sample_id?:string;start?:number;end?:number}|null=null;
    try {reveal=JSON.parse(sessionStorage.getItem("statforge:reveal-media:"+dataset.id+":"+(taskId||"project")) || "null");} catch {reveal=null;}
    const revealedSample=reveal?.sample_id && samples.data.samples.some(s=>s.sample_id===reveal?.sample_id) ? reveal.sample_id : undefined;
    const chosen = revealedSample || (initialSampleId && samples.data.samples.some(s => s.sample_id === initialSampleId) ? initialSampleId : saved?.sample_id || samples.data.samples[0]?.sample_id || "");
    if (samples.data.kind === "media") {
      const audioOnly = samples.data.samples.every(s => !s.video_id);
      const lastRun = samples.data.runs.find(run=>run.dataset_id === dataset.id && Array.isArray(run.parameters?.steps));
      const draft = saved?.processing || lastRun?.parameters || {};
      const restored = {...defaultMediaOptions, ...(audioOnly ? {steps:["pitch","intensity"]} : {})};
      for (const field of Object.keys(restored)) if (field in draft) Object.assign(restored,{[field]:draft[field as keyof typeof draft]});
      restored.audio_source = audioOnly ? "independent" : saved?.audio_source ?? restored.audio_source;
      manualCountFor.current = "";
      if (saved?.processing?.speech_count != null && chosen === saved.sample_id) {
        restored.speech_count = saved.processing.speech_count;
        manualCountFor.current = countKey(chosen, String(saved.start ?? ""), String(saved.end ?? ""), restored);
      }
      setProcessing(restored);
    }
    const remembered = !revealedSample && !initialSourceId && !initialRange ? views.current[chosen] : undefined;
    cursor.current=revealedSample ? reveal?.start : remembered?.cursor;
    setSampleId(chosen); setObjectId(initialObjectId); setMatches([]); setError("");
    setStart(revealedSample && reveal?.start != null ? String(reveal.start) : remembered?.start ?? (chosen === saved?.sample_id && saved.start != null ? String(saved.start) : ""));
    setEnd(revealedSample && reveal?.end != null ? String(reveal.end) : remembered?.end ?? (chosen === saved?.sample_id && saved.end != null ? String(saved.end) : ""));
  }, [samples.data, dataset.id, taskId, initialSampleId, initialObjectId]);
  useEffect(() => {
    const target = `${dataset.id}:${initialSampleId || ""}:${initialObjectId ?? ""}`;
    if (target !== initialTarget.current && initialSampleId && samples.data?.samples.some(s => s.sample_id === initialSampleId)) {
      initialTarget.current = target; setSampleId(initialSampleId); setObjectId(initialObjectId);
    }
  }, [dataset.id, initialSampleId, initialObjectId, samples.data]);
  useEffect(()=>{
    if(!samples.data||!initialSourceId)return;
    const target=samples.data.samples.find(item=>item.video_id===initialSourceId||item.audio_id===initialSourceId);
    if(!target)return;
    const key=JSON.stringify([dataset.id,target.sample_id,initialRange]);
    if(appliedRange.current===key)return;
    appliedRange.current=key;setSampleId(target.sample_id);cursor.current=initialRange?.start;
    const fromAudio=target.audio_id===initialSourceId && target.video_id!==initialSourceId;
    const offset=fromAudio && target.video_id ? target.audio_alignment?.offset_seconds ?? 0 : 0;
    setProcessing(old=>({...old,audio_source:fromAudio?"independent":"video"}));
    setStart(initialRange?String(initialRange.start-offset):"");setEnd(initialRange?String(initialRange.end-offset):"");
  },[samples.data,initialSourceId,initialRange,dataset.id]);
  const caseOnly = !!samples.data && samples.data.kind === "media" && samples.data.samples.every(item=>!item.video_id && !item.audio_id);
  const selected = samples.data?.samples.find(item => item.sample_id === sampleId);
  async function changeCase(remove:boolean) {
    if (!selected || busy) return;
    if (!remove && !caseName.trim()) { setError("请填写案例名称。"); return; }
    setBusy(true); setError("");
    try {
      const result = await apiFetch<{group_empty:boolean}>(`${endpoint}/cases/${encodeURIComponent(selected.sample_id)}`, remove ? {method:"DELETE"} : {method:"PATCH",body:JSON.stringify({name:caseName.trim()})});
      await Promise.all([client.invalidateQueries({queryKey:["datasets"]}),client.invalidateQueries({queryKey:["workspace-files"]}),client.invalidateQueries({queryKey:["media-samples"]}),client.invalidateQueries({queryKey:["coding"]})]);
      setEditingCase(false); setConfirmCaseDelete(false);
      if (remove && result.group_empty) {
        const original=selected.primary_id || selected.video_id || selected.audio_id || selected.text_id;
        if(original) onChoose(original); else await onSaved?.();
      } else await samples.refetch();
    } catch(reason) {setError(reason instanceof Error ? reason.message : "无法修改案例，请重试。");} finally {setBusy(false);}
  }
  const [videoLength, setVideoLength] = useState<{id:string;seconds:number} | null>(null);
  const primaryId=selected?.video_id || selected?.audio_id;
  const sourceDuration=useQuery({
    queryKey:["source-material",primaryId,datasets.find(d=>d.id===primaryId)?.sha256],
    enabled:!!primaryId,
    queryFn:({signal})=>apiFetch<Material>(`/api/v1/datasets/${primaryId}/source-material`,{method:"POST",signal}),
  });
  const videoDuration = videoLength && videoLength.id === selected?.video_id ? videoLength.seconds : sourceDuration.data?.metadata.duration_seconds;
  const clipStart = number(start) ?? 0;
  const clipEnd = number(end) ?? videoDuration ?? null;
  const offset = selected?.audio_alignment?.offset_seconds ?? 0;
  const singleClock = !selected?.video_id || processing.audio_source === "video";
  const partialAudio = singleClock && (clipStart > 0.0005 || (videoDuration != null && clipEnd != null && clipEnd < videoDuration - 0.0005));
  const needsClipCount = !processFull && partialAudio && !selected?.text_range;
  function selectRange(a:number,b:number) {
    if (!selected || a < 0 || b <= a || (videoDuration != null && b > videoDuration + .001)) {
      setError("选段超出当前视频，请调整起止秒数。"); return;
    }
    const first = String(Number(a.toFixed(6))), last = String(Number(b.toFixed(6)));
    if (first === start && last === end) return;
    setStart(first); setEnd(last); rememberView(selected.sample_id,first,last);
    if (pendingSelection.current) clearTimeout(pendingSelection.current);
    const id=selected.sample_id;
    queuedSelection.current=()=>{queuedSelection.current=null;void saveSelection(id,first,last);};
    pendingSelection.current=setTimeout(()=>queuedSelection.current?.(),300);
  }
  const running = (samples.data?.runs || []).some(run => active(run.status));
  function number(value: string): number | null { return value.trim() ? Number(value) : null; }
  async function saveSelection(id: string, from: string, to: string, audioSource = processing.audio_source, requested = processing) {
    const options = forSelection(id, from, to, {...requested, audio_source:audioSource});
    if (samples.data?.kind === "media" && Object.keys(mediaOptionErrors(options, options.audio_source==="independent"?samples.data?.samples.find(s=>s.sample_id===sampleId)?.audio_sample_rate:undefined)).length) return false;
    const a = number(from), b = number(to);
    if ((a !== null && (!Number.isFinite(a) || a < 0)) || (b !== null && (!Number.isFinite(b) || b < 0)) || (a !== null && b !== null && b <= a)) {
      setError("请填写有效的开始和结束秒数，结束应晚于开始。"); return false;
    }
    const identity = JSON.stringify([id, a, b, audioSource, options]);
    const write = async () => {
      if (identity === lastSavedSelection.current) return true;
      try {
        const selection = await apiFetch<NonNullable<Result["selection"]>>(`${endpoint}/sample-selection`, {
          method: "POST", body: JSON.stringify({task_id: taskId, sample_id: id, start: a, end: b, audio_source:audioSource, ...(samples.data?.kind === "media" ? {processing:options} : {})}) });
        lastSavedSelection.current = identity;
        client.setQueryData<Result>(["media-samples",dataset.id,taskId], old => old ? {...old,selection} : old);
        window.dispatchEvent(new CustomEvent("statforge:material-context-changed", {detail:{project:dataset.project_id,task:taskId || "project",dataset:dataset.id}}));
        setError(""); return true;
      } catch (reason) { setError(reason instanceof Error ? reason.message : "无法保存选段。"); return false; }
    };
    writes.current = writes.current.then(write, write);
    return writes.current;
  }
  function chooseSample(id: string) {
    if(pendingSelection.current) clearTimeout(pendingSelection.current);
    queuedSelection.current=null;
    rememberView(sampleId,start,end);
    const previous=views.current[id];
    cursor.current=previous?.cursor; setMediaView("video");
    setSampleId(id); setObjectId(undefined); setStart(previous?.start ?? ""); setEnd(previous?.end ?? ""); setMatches([]); setAudioOpen(false);
    void saveSelection(id, previous?.start ?? "", previous?.end ?? "");
  }
  async function process(all: boolean) {
    if (busy || !selected) return;
    setBusy(true); setError("");
    try {
      if (samples.data?.kind === "media" && Object.keys(mediaOptionErrors(processing, processing.audio_source==="independent"?samples.data?.samples.find(s=>s.sample_id===sampleId)?.audio_sample_rate:undefined)).length) return;
      if (processing.steps.includes("speech_rate")) {
        if (needsClipCount && processing.speech_count == null && (!all || samples.data?.samples.every(s=>!s.text_range))) {
          setError("关联台词没有时间标记。请填写这一选段的词数或字数，或关联同一时段的转录。");
          return;
        }
        if (all && (samples.data?.samples.length ?? 0) > 1 && processing.speech_count != null) {
          setError("人工计数属于当前样本，请点击「处理当前样本」。");
          return;
        }
      }
      if (!(await saveSelection(selected.sample_id, start, end))) return;
      const result = await apiFetch<Run>(`${endpoint}/media-process`, { method: "POST", body: JSON.stringify({ ...(samples.data?.kind === "media" ? processing : {}), task_id: taskId, sample_ids: all ? [] : [selected.sample_id], start: processFull ? null : number(start), end: processFull || (all && clipStart === 0 && videoDuration != null && clipEnd != null && Math.abs(clipEnd-videoDuration)<0.001) ? null : number(end) }) });
      if(mode==="processing"||toolOpen)setResultId(result.id);else openPanel("results",result.id);
      watchProcessing({project:dataset.project_id,task:taskId,source:dataset.id,url:endpoint+"/media-runs/"+result.id,title:"媒体处理"});
      setNotice(result.reused ? "已找到相同参数的结果。" : "");
      await samples.refetch();
      if (result.reused) await finished();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "处理失败，请重试。"); }
    finally { setBusy(false); }
  }
  async function search(captionId?: number) {
    const run = samples.data?.runs.find(item => item.status.toLowerCase() === "completed");
    if (!selected || !run) { setError("请先完成一次特征处理，再检索相似描述。"); return; }
    setSearching(true); setError("");
    try {
      const result = await apiFetch<{ matches: Match[] }>(`${endpoint}/media-search`, { method: "POST", body: JSON.stringify({ run_id: run.id, sample_id: selected.sample_id, ...(captionId === undefined ? {} : { caption_id: captionId }) }) });
      setMatches(result.matches);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "检索失败，请重试。"); }
    finally { setSearching(false); }
  }
  async function finished() {
    await Promise.all([client.invalidateQueries({ queryKey: ["datasets"] }), samples.refetch()]);
    await onSaved?.();
  }
  const linked = (id?: string) => id ? datasets.find(item => item.id === id) : undefined;
  const video = linked(selected?.video_id), audio = linked(selected?.audio_id), image = linked(selected?.image_dataset_id);
  const processingControls=samples.data&&<div className="rw-processing-controls">
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {samples.data.kind === "media" && !caseOnly && <>
        <details><summary>材料关联</summary><MediaAssociationEditor embedded key={dataset.id} beforeSave={()=>saveSelection(sampleId,start,end)} dataset={dataset} datasets={datasets} taskId={taskId} onChoose={id=>{if(id===dataset.id)void samples.refetch();else onChoose(id);}} initial={samples.data.samples.map(s=>({sample_id:s.sample_id,video_id:s.video_id || "",audio_id:s.audio_id || "",text_id:s.text_id || "",case_title:s.case_title || "",member_ids:(s.members || []).map(item=>item.id),audio_offset_seconds:s.audio_alignment?.offset_seconds == null ? "" : String(s.audio_alignment.offset_seconds),content_reviewed:!!s.audio_alignment?.content_reviewed}))}/></details>
        <div className={layout.processingRange}>
          <strong>处理范围</strong>
          {clipStart > 0 || clipEnd != null && videoDuration != null && clipEnd < videoDuration-.001
            ? <><label><input type="radio" name={"scope-"+dataset.id} checked={!processFull} onChange={()=>setProcessFull(false)}/>当前选段 {clipStart}–{clipEnd ?? "结尾"} 秒</label>
              <label><input type="radio" name={"scope-"+dataset.id} checked={processFull} onChange={()=>setProcessFull(true)}/>全段{videoDuration!=null && " · 0–"+Number(videoDuration.toFixed(3))+" 秒"}</label></>
            : <span>全段{videoDuration!=null && " · 0–"+Number(videoDuration.toFixed(3))+" 秒"}</span>}
        </div>
        <MediaProcessingOptions sourceRate={processing.audio_source==="independent"?samples.data.samples.find(s=>s.sample_id===sampleId)?.audio_sample_rate:undefined} audioOnly={samples.data.samples.every(s=>!s.video_id)} needsClipCount={needsClipCount} value={processing} onChange={value => {
          const next = value.audio_source === processing.audio_source && value.speech_count_unit === processing.speech_count_unit
            ? value : {...value, speech_count: undefined};
          manualCountFor.current = next.speech_count == null ? "" : countKey(sampleId,processFull?"":start,processFull?"":end,next);
          setProcessing(next);
          if (selected) void saveSelection(selected.sample_id,start,end,next.audio_source,next);
        }} disabled={busy || running}/>
        {!processFull && <div className={styles.controls}><label>开始秒<input aria-label="处理开始秒" type="number" min={0} step="0.1" value={start} placeholder="0" onChange={e=>setStart(e.target.value)} onBlur={e=>{if (selected) void saveSelection(selected.sample_id,e.currentTarget.value,end);}}/></label><label>结束秒<input aria-label="处理结束秒" type="number" min={0} step="0.1" value={end} placeholder="到结尾" onChange={e=>setEnd(e.target.value)} onBlur={e=>{if (selected) void saveSelection(selected.sample_id,start,e.currentTarget.value);}}/></label></div>}
      </>}
      <div className={styles.controls+" rw-processing-sample"}><label>当前样本 <SearchSelect value={sampleId} onChange={event => chooseSample(event.target.value)}>{samples.data.samples.map(item => <option key={item.sample_id} value={item.sample_id}>{item.case_title && item.case_title!==item.title ? item.case_title + " · " + item.title : (item.display_title && item.display_title!==item.title ? item.display_title + " · " + item.title : item.title)}</option>)}</SearchSelect></label>
        {!caseOnly && <><button type="button" disabled={busy || running || !selected} onClick={() => void process(false)}>{samples.data.kind === "ravdess" ? "提取当前片段" : "处理当前样本"}</button>
        <button type="button" disabled={busy || running || !selected} onClick={() => void process(true)}>处理全部</button></>}
      </div>

    {!!samples.data.runs.length&&<section className="rw-processing-status"><label>处理记录 <SearchSelect value={resultId || samples.data.runs[0].id} onChange={event=>setResultId(event.target.value)}>
      {samples.data.runs.map(run=><option key={run.id} value={run.id}>{runLabel(run)}</option>)}
    </SearchSelect></label>
    <RunStatus key={(samples.data.runs.find(run=>run.id===resultId)||samples.data.runs[0]).id} initial={samples.data.runs.find(run=>run.id===resultId)||samples.data.runs[0]}
      endpoint={`/api/v1/datasets/${(samples.data.runs.find(run=>run.id===resultId)||samples.data.runs[0]).dataset_id}`} taskId={taskId} onFinished={finished} onChoose={id=>{setToolOpen(false);onChoose(id);}} onStarted={setResultId}/>
    </section>}
  </div>;
  return <section className={styles.root}>
    {!mode && !samples.data && versionPicker}
    {samples.isPending && <p role="status">正在读取样本…</p>}
    {samples.isError && <p role="alert" className="rw-error">{samples.error.message} <button onClick={() => void samples.refetch()}>重试</button></p>}
    {!mode&&!toolOpen&&error && <p role="alert" className="rw-error">{error}</p>}
    {!mode&&!toolOpen&&notice && <p role="status">{notice}</p>}
    {samples.data && <>
      {panel==="materials"&&suggestions}
      {!mode&&<div className={layout.sectionHeader}><div className={styles.panelTabs} aria-label="材料组"><button aria-pressed={panel==="materials"} onClick={()=>openPanel("materials")}>材料与处理</button>{!caseOnly && <button aria-pressed={panel==="results"} onClick={()=>openPanel("results")}>处理结果{samples.data.runs.length ? " · "+samples.data.runs.length : ""}</button>}</div>{versionPicker}</div>}
      {(mode==="processing" || panel === "materials") && <>{caseOnly && !dataset.media_case?.survey && <CaseMembersEditor key={dataset.id} dataset={dataset} datasets={datasets} taskId={taskId} cases={samples.data.samples} onChoose={onChoose}/>}
      {mode==="processing"&&processingControls}
      {!mode&&<div className={styles.controls+" rw-processing-sample"}><label>当前样本 <SearchSelect value={sampleId} onChange={event=>chooseSample(event.target.value)}>
        {samples.data.samples.map(item=><option key={item.sample_id} value={item.sample_id}>{item.case_title && item.case_title!==item.title ? item.case_title+" · "+item.title : (item.display_title&&item.display_title!==item.title?item.display_title+" · "+item.title:item.title)}</option>)}
      </SearchSelect></label></div>}
      {!mode && selected && <>
        {caseOnly && samples.data.kind === "media" && selected.case_title && dataset.media_case?.purpose !== "temporary" && <div className="rw-inline-actions">
          {editingCase ? <><label>案例名称 <input aria-label="案例名称" value={caseName} onChange={event=>setCaseName(event.target.value)}/></label><button disabled={busy || !caseName.trim()} onClick={()=>void changeCase(false)}>保存名称</button><button disabled={busy} onClick={()=>setEditingCase(false)}>取消</button></> : <><button onClick={()=>{setCaseName(selected.case_title || "");setEditingCase(true);}}>重命名案例</button><button disabled={busy} onClick={()=>setConfirmCaseDelete(true)}>删除案例</button></>}
        </div>}
        <ConfirmDialog open={confirmCaseDelete} onOpenChange={setConfirmCaseDelete} title="删除案例" description="删除案例并解除标注的案例关联，标注内容和原始材料保留。已确认独属且未被引用的处理记录和结果将移出列表；共享、历史及无法确认归属的成果保留。" confirmLabel="删除案例" cancelLabel="取消" isLoading={busy} onConfirm={()=>void changeCase(true)}/>
        {caseOnly && <p className="rw-muted">{selected.case_title || selected.title}</p>}
        {samples.data.kind !== "coco" && !caseOnly && <>
          <div className={layout.materialGrid}><div className={layout.materialMain}>
            {video && <div className={layout.viewTabs} aria-label="材料查看方式"><button type="button" aria-pressed={mediaView==="video"} onClick={()=>setMediaView("video")}>视频查看</button><button type="button" aria-pressed={mediaView==="audio"} onClick={()=>setMediaView("audio")}>声音查看</button></div>}
            {video && mediaView==="video" ? <SampleVideo key={video.id} dataset={video} taskId={taskId} sampleId={selected.sample_id} sampleCollectionId={dataset.id} start={number(start)} end={number(end)} onRange={selectRange} onDuration={seconds=>setVideoLength({id:video.id,seconds})} initialCursor={cursor.current} onCursorChange={rememberCursor} /> :
              (video || audio) ? <SampleAudio key={`${selected.sample_id}:${processing.audio_source}`} dataset={processing.audio_source === "independent" && audio ? audio : (video || audio)!} taskId={taskId} sampleId={selected.sample_id} sampleCollectionId={dataset.id} origin={video && audio && processing.audio_source === "independent" ? -offset : 0} start={number(start)} end={number(end)} onRange={selectRange} initialCursor={cursor.current} onCursorChange={rememberCursor} onReviewClip={video ? (a,b)=>{selectRange(a,b);cursor.current=a;setMediaView("video");} : undefined}/> : <p className="rw-muted">找不到此样本的音视频文件。</p>}
            {video && audio && samples.data.kind === "media" && <details><summary>视频与独立音频对照</summary><PairedPreview video={video} audio={audio} alignment={selected.audio_alignment} start={clipStart} end={clipEnd} /></details>}
            {video && audio && <details open={audioOpen} onToggle={event=>setAudioOpen(event.currentTarget.open)}><summary>独立 WAV 音频</summary>
              {audioOpen && <WorkspaceSourceFile sampleId={selected.sample_id} sampleCollectionId={dataset.id} key={audio.id} dataset={audio} datasets={datasets} onChoose={onChoose} taskId={taskId} />}
            </details>}
          </div><aside className={layout.materialAside}>
            {selected.transcript && <section><h3>关联台词</h3><p>{selected.transcript}</p>{selected.text_id && <details><summary>查看与标注台词</summary>{linked(selected.text_id) && <WorkspaceSourceFile sampleId={selected.sample_id} sampleCollectionId={dataset.id} dataset={linked(selected.text_id)!} datasets={datasets} onChoose={onChoose} taskId={taskId}/>}</details>}</section>}
            <section><h3>处理</h3><div className={layout.sideActions}>{(video||audio)&&<WorkspaceTranscription key={(video||audio)!.id} dataset={(video||audio)!} taskId={taskId} onChoose={onChoose} initialRange={clipEnd!=null?{start:clipStart,end:clipEnd}:undefined}/>}<button onClick={()=>{setProcessFull(false);setToolOpen(true);}}>{video?"声音特征与取帧":"声音特征"}</button></div></section>
            {(video || audio) && <CaseMaterialPicker key={(video || audio)!.id} compact dataset={(video || audio)!} datasets={datasets} taskId={taskId} currentRange={clipEnd!=null?{start:clipStart,end:clipEnd}:undefined} onOpenCase={(id,caseSample)=>{if(id===dataset.id)chooseSample(caseSample);else onChoose(id);}}>{()=>null}</CaseMaterialPicker>}
            {selected.case_title && dataset.media_case?.purpose !== "temporary" && <details><summary>管理案例 · {selected.case_title}</summary><div className={layout.sideActions}>{editingCase ? <><label>案例名称 <input aria-label="案例名称" value={caseName} onChange={event=>setCaseName(event.target.value)}/></label><button disabled={busy || !caseName.trim()} onClick={()=>void changeCase(false)}>保存名称</button><button disabled={busy} onClick={()=>setEditingCase(false)}>取消</button></> : <><button onClick={()=>{setCaseName(selected.case_title || "");setEditingCase(true);}}>重命名案例</button><button disabled={busy} onClick={()=>setConfirmCaseDelete(true)}>删除案例</button></>}</div></details>}
            {samples.data.kind === "media" && <details><summary>材料关联</summary><MediaAssociationEditor embedded key={dataset.id} beforeSave={()=>saveSelection(sampleId,start,end)} dataset={dataset} datasets={datasets} taskId={taskId} onChoose={id=>{if(id===dataset.id)void samples.refetch();else onChoose(id);}} initial={samples.data.samples.map(s=>({sample_id:s.sample_id,video_id:s.video_id || "",audio_id:s.audio_id || "",text_id:s.text_id || "",case_title:s.case_title || "",member_ids:(s.members || []).map(item=>item.id),audio_offset_seconds:s.audio_alignment?.offset_seconds == null ? "" : String(s.audio_alignment.offset_seconds),content_reviewed:!!s.audio_alignment?.content_reviewed}))}/></details>}
          </aside></div>
        </>}
        {selected.attributes&&<p className="rw-muted">{Object.entries(selected.attributes).map(([name,value])=>`${name}：${value??"—"}`).join(" · ")}</p>}
        {!!selected.members?.length && <section><h3>{dataset.media_case?.survey?"开放回答":"案例其他材料"}</h3>{!dataset.media_case?.survey&&<p>这些材料属于同一案例，不表示时间同步。</p>}{selected.members.map(member=>{const item=linked(member.id);return item && item.sha256===member.sha256 ? <details key={member.id}><summary>{item.display_name || item.original_filename}</summary><WorkspaceSourceFile dataset={item} datasets={datasets} onChoose={onChoose} taskId={taskId} sampleId={selected.sample_id} sampleCollectionId={dataset.id}/></details> : <p key={member.id}>关联材料已变化或不可用，请重新选择。</p>;})}</section>}
        {samples.data.kind === "coco" && <>
          <div className="rw-inline-actions"><button type="button" onClick={()=>setToolOpen(true)}>处理图像与描述</button></div>
          {image ? <SampleImage sampleId={selected.sample_id} sampleCollectionId={dataset.id} dataset={image} taskId={taskId} region={selected.objects?.find(object => object.id === objectId)} /> : <p className="rw-muted">图像文件不可用。</p>}
          <button type="button" disabled={searching} onClick={() => void search()}>查找相似图像</button><div className={styles.columns}><section><h3>描述</h3>{selected.captions?.map(caption => <div className={styles.item} key={caption.id}><span>{caption.caption}</span><button type="button" disabled={searching} onClick={() => void search(caption.id)}>查找相似描述</button></div>)}</section>
            <section><h3>对象</h3>{selected.objects?.map(object => <button className={styles.object} key={object.id} aria-pressed={objectId === object.id} onClick={() => setObjectId(object.id)}>{object.category} · #{object.id}</button>)}</section></div>
          {!!matches.length && <section><h3>相似结果</h3>{matches.map(match => <button className={styles.object} key={`${match.sample_id}:${match.title}`} onClick={() => chooseSample(match.sample_id)}>{match.title} · {match.score.toFixed(3)}</button>)}</section>}
        </>}
      </>}
      </>}
      {!mode && panel === "results" && (() => {
        const runs = samples.data.runs;
        if (!runs.length) return <p>还没有处理结果。</p>;
        const picked = runs.find(run=>run.id===resultId) || runs[0];
        return <section className={styles.results}>
          <label>处理记录 <SearchSelect value={picked.id} onChange={e=>openPanel("results",e.target.value)}>
            {runs.map(item=><option key={item.id} value={item.id}>{runLabel(item)}</option>)}
          </SearchSelect></label>
          <RunStatus key={picked.id} initial={picked} endpoint={`/api/v1/datasets/${picked.dataset_id}`} taskId={taskId}
            onFinished={finished} onChoose={onChoose} onStarted={id=>openPanel("results",id)} />
          {picked.status === "completed" && picked.package_id && <MediaCurves packageId={picked.package_id} datasets={datasets} onChoose={onChoose} taskId={taskId} onRange={async(id,a,b,sound)=>{
            sessionStorage.setItem("statforge:reveal-media:"+picked.dataset_id+":"+(taskId||"project"),JSON.stringify({sample_id:id,start:a,end:b}));
            if (picked.dataset_id !== dataset.id) {
              try {
                await apiFetch(`/api/v1/datasets/${picked.dataset_id}/sample-selection`,{method:"POST",body:JSON.stringify({task_id:taskId,sample_id:id,start:a,end:b,audio_source:sound})});
                client.removeQueries({queryKey:["media-samples",picked.dataset_id,taskId]});
                sessionStorage.removeItem("statforge:material-results:"+picked.dataset_id+":"+(taskId||"project"));onChoose(picked.dataset_id);
              } catch(reason){setError(reason instanceof Error?reason.message:"无法打开原材料组。");}
              return;
            }
            const next={...processing,audio_source:sound};
            if (await saveSelection(id,String(a),String(b),sound,next)) {
              cursor.current=a;rememberView(id,String(a),String(b),a);setMediaView("video");setProcessing(next);setSampleId(id);setStart(String(a));setEnd(String(b));openPanel("materials");
            }
          }}/>}
        </section>;
      })()}
      {!mode&&<WorkspaceToolDialog open={toolOpen} onClose={()=>setToolOpen(false)} title={samples.data.kind==="coco"?"处理图像与描述":video?"声音特征与取帧":"声音特征"} subtitle={selected?.display_title || selected?.title}>{toolOpen && processingControls}</WorkspaceToolDialog>}
    </>}
  </section>;
}


function PairedPreview({video, audio, alignment, start, end}: {video: Dataset; audio: Dataset; alignment?: {offset_seconds: number | null; content_reviewed: boolean}; start: number | null; end: number | null}) {
  const player = useRef<HTMLVideoElement | null>(null), sound = useRef<HTMLAudioElement | null>(null);
  const [urls, setUrls] = useState<{video: string; audio: string} | null>(null);
  const [error, setError] = useState("");
  const offset = alignment?.offset_seconds ?? 0;
  useEffect(() => {
    const controller = new AbortController(); let videoUrl = "", audioUrl = "";
    setUrls(null); setError("");
    void Promise.all([video, audio].map(async item => {
      const material = await apiFetch<Material>(`/api/v1/datasets/${item.id}/source-material`, {method:"POST", signal:controller.signal});
      return materialApi.blob(material.id, controller.signal);
    })).then(([videoBlob, audioBlob]) => {
      if (controller.signal.aborted) return;
      videoUrl = URL.createObjectURL(videoBlob); audioUrl = URL.createObjectURL(audioBlob);
      setUrls({video:videoUrl, audio:audioUrl});
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法读取配对媒体。"); });
    return () => { controller.abort(); if (videoUrl) URL.revokeObjectURL(videoUrl); if (audioUrl) URL.revokeObjectURL(audioUrl); };
  }, [video.id, video.sha256, audio.id, audio.sha256]);
  function align(): boolean {
    const v = player.current, a = sound.current;
    if (!v || !a || !Number.isFinite(a.duration)) return false;
    const target = v.currentTime + offset;
    if (target < 0 || target > a.duration + .001) { v.pause(); a.pause(); setError("当前视频时间映射到 WAV 后超出范围，请调整选段或偏移。"); return false; }
    if (Math.abs(a.currentTime - target) > 0.15) a.currentTime = target;
    return true;
  }
  async function playSelection() {
    const v = player.current, a = sound.current;
    if (!v || !a || !Number.isFinite(v.duration) || !Number.isFinite(a.duration)) { setError("请等待配对媒体加载完成。"); return; }
    const first = start ?? 0;
    const videoEnd = Math.min(end ?? v.duration, v.duration);
    const last = alignment?.offset_seconds == null ? Math.min(videoEnd, a.duration) : videoEnd;
    if (first < 0 || last <= first || first + offset < 0 || last + offset > a.duration + 1e-6) {
      setError("所选视频范围映射到 WAV 后超出范围，请调整选段或偏移。"); return;
    }
    setError(""); v.currentTime = first; a.currentTime = first + offset;
    try { await Promise.all([a.play(), v.play()]); } catch { v.pause(); a.pause(); setError("无法同时播放，请重试。"); }
  }
  const seconds = (n:number) => Number(n.toFixed(3));
  return <section><h3>配对预览</h3><p>{end != null
    ? `视频 ${seconds(start ?? 0)}–${seconds(end)} 秒 · 独立 WAV ${seconds((start ?? 0) + offset)}–${seconds(end + offset)} 秒`
    : `独立 WAV 偏移 ${offset >= 0 ? "+" : ""}${seconds(offset)} 秒`}</p>
    {urls && <><video controls muted style={{maxWidth:"100%"}} src={urls.video} ref={player} onPlay={() => { if (align()) void sound.current?.play().catch(() => { player.current?.pause(); setError("无法播放独立音频。"); }); }} onPause={() => sound.current?.pause()} onSeeking={() => { align(); }} onTimeUpdate={() => { const v=player.current; if (!v) return; const a=sound.current; const limit=alignment?.offset_seconds == null && a && Number.isFinite(a.duration) ? Math.min(end ?? v.duration, a.duration) : (end ?? v.duration); if (Number.isFinite(limit) && v.currentTime >= limit) { v.pause(); v.currentTime=limit; if (a && Number.isFinite(a.duration)) { a.pause(); a.currentTime=Math.max(0,Math.min(a.duration,limit+offset)); } } else if (!v.paused) align(); }} /><audio ref={sound} src={urls.audio} preload="metadata" /></>}
    <button type="button" disabled={!urls} onClick={() => void playSelection()}>播放所选视频与独立音频</button>{error && <p role="alert">{error}</p>}
  </section>;
}

function SampleImage({ dataset, taskId, region, sampleId,sampleCollectionId }: { sampleId:string;sampleCollectionId:string; dataset: Dataset; taskId?: string; region?: ObjectRegion }) {
  const material = useQuery({ queryKey: ["source-material", dataset.id, dataset.sha256], queryFn: ({ signal }) => apiFetch<Material>(`/api/v1/datasets/${dataset.id}/source-material`, { method: "POST", signal }), retry: false });
  let location: Location | null = null;
  if (region && region.width > 0 && region.height > 0) {
    const [x, y, w, h] = region.bbox;
    location = { kind: "region", x: x / region.width, y: y / region.height, width: w / region.width, height: h / region.height, note: `${region.category} #${region.id}` };
  }
  return <>{material.isPending && <p role="status">正在读取图像…</p>}{material.isError && <p role="alert" className="rw-error">{material.error.message} <button onClick={() => void material.refetch()}>重试</button></p>}{material.data && <MaterialViewer material={material.data} page={1} location={location} sampleId={sampleId} sampleCollectionId={sampleCollectionId} taskId={taskId} />}</>;
}

const runState:Record<string,string> = {queued:"等待处理",running:"正在处理",completed:"已完成",failed:"处理失败",interrupted:"已中断"};
function runLabel(run:Run) {
  const time = run.started_at ? new Date(run.started_at).toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "";
  return [time,run.title.match(/\d+ 个样本/)?.[0],runState[run.status]].filter(Boolean).join(" · ");
}
function RunStatus({ initial, endpoint, taskId, onFinished, onChoose, onStarted }: {onStarted:(id:string)=>void; initial: Run; endpoint: string; taskId?:string; onFinished: () => Promise<void>; onChoose: (id: string) => void }) {
  const [error,setError]=useState(""),[busy,setBusy]=useState(false);
  const wasActive = useRef(active(initial.status));
  const run = useQuery({ queryKey: ["media-run", endpoint, initial.id], initialData: initial,
    queryFn: ({ signal }) => apiFetch<Run>(`${endpoint}/media-runs/${initial.id}`, { signal }),
    refetchInterval: query => active(query.state.data?.status || "") ? 1500 : false });
  useEffect(() => {
    if (wasActive.current && !active(run.data.status)) { wasActive.current = false; void onFinished(); }
  }, [run.data.status, onFinished]);
  async function rerun(){setBusy(true);setError("");try{const {case:_,signature:__,input_files:___,...parameters}=run.data.parameters || {};const next=await apiFetch<Run>(`${endpoint}/media-process`,{method:"POST",body:JSON.stringify({...parameters,task_id:taskId,force:true})});await onFinished();onStarted(next.id);}catch(reason){setError(reason instanceof Error?reason.message:"无法重新处理。");}finally{setBusy(false);}}
  const params=run.data.parameters || {};
  const labels:Record<string,string>={extract_audio:"音轨",pitch:"F0",formants:"F1–F4",intensity:"强度",speech_rate:"语速",mfcc:"MFCC "+params.n_mfcc,rms:"RMS",frames:params.frames_per_video+" 帧",resnet:"ResNet18",minilm:"MiniLM",tfidf:"TF-IDF"};
  const pitchMethods:Record<string,string>={raw_autocorrelation:"原始自相关",filtered_autocorrelation:"滤波自相关",raw_cross_correlation:"原始互相关",filtered_cross_correlation:"滤波互相关"};
  const steps=Array.isArray(params.steps)?params.steps.map(s=>s==="pitch" && params.pitch_method ? `F0 ${pitchMethods[String(params.pitch_method)] || "原始自相关"}` : labels[String(s)]).filter(Boolean):[];
  const status = run.data.status;
  return <div className={styles.runDetails}>
    <h3 className={status === "failed" ? "rw-error" : ""}>{runState[status] || status}</h3>
    {active(status) && <p role="status">{run.data.progress}</p>}
    {status === "failed" && <p role="alert">{run.data.failure_reason}</p>}
    <p>{[params.audio_source==="independent"?"独立 WAV":params.audio_source==="video"?"视频音轨":"",...steps].filter(Boolean).join(" · ")}</p>
    {run.isError && <p role="alert">读取处理记录失败。 <button onClick={()=>void run.refetch()}>重试</button></p>}
    {status === "completed" && <div className={styles.controls}>
      {run.data.summary_id && <button className="rw-primary" onClick={()=>onChoose(run.data.summary_id!)}>打开特征摘要</button>}
      {run.data.package_id && <button onClick={()=>onChoose(run.data.package_id!)}>完整特征与派生媒体</button>}
    </div>}
    {run.data.parameters && <details><summary>本次参数</summary><p>{[params.sample_rate && String(params.sample_rate)+" Hz",params.window_ms && "窗口 "+params.window_ms+" ms",params.hop_ms && "步长 "+params.hop_ms+" ms",params.start!=null && "从 "+params.start+" 秒",params.end!=null && "至 "+params.end+" 秒"].filter(Boolean).join(" · ")}</p>
      <button disabled={busy || active(status)} onClick={()=>void rerun()}>按原参数重新执行</button></details>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

function SampleVideo({dataset,taskId,start,end,onRange,onDuration,sampleId,sampleCollectionId,initialCursor,onCursorChange}: {onDuration?:(seconds:number)=>void;sampleId:string;sampleCollectionId:string;dataset:Dataset;taskId?:string;start:number|null;end:number|null;onRange:(a:number,b:number)=>void;initialCursor?:number;onCursorChange?:(seconds:number)=>void}) {

  const anchor=useRef<HTMLDivElement>(null);
  const [reveal]=useState<Location|null>(()=>{
    const key="statforge:reveal-media:"+sampleCollectionId+":"+(taskId||"project");
    const value=JSON.parse(sessionStorage.getItem(key)||"null");
    if(value?.sample_id!==sampleId||!(value.end>value.start))return null;
    sessionStorage.removeItem(key);
    return {kind:"time",start_seconds:value.start,end_seconds:value.end,note:""};
  });
  const material = useQuery({queryKey:["source-material",dataset.id,dataset.sha256],queryFn:({signal})=>apiFetch<Material>(`/api/v1/datasets/${dataset.id}/source-material`,{method:"POST",signal}),retry:false});
  useEffect(()=>{if(material.data&&reveal)anchor.current?.scrollIntoView({block:"start"});},[material.data,reveal]);
  const durationCallback = useRef(onDuration); durationCallback.current = onDuration;
  const seconds = material.data?.metadata.duration_seconds;
  useEffect(() => { if (seconds && seconds > 0) durationCallback.current?.(seconds); }, [dataset.id,seconds]);
  return <div ref={anchor}>{material.isPending && <p role="status">正在读取媒体…</p>}{material.isError && <p role="alert">{material.error.message}</p>}{material.data && <MaterialViewer material={material.data} page={1} location={reveal} sharedRange={{ start,end }} sampleId={sampleId} sampleCollectionId={sampleCollectionId} taskId={taskId} onTimeRangeChange={r=>onRange(r.start_seconds,r.end_seconds)} initialCursor={initialCursor} onCursorChange={onCursorChange} />}</div>;
}

function SampleAudio({dataset,taskId,sampleId,sampleCollectionId,origin,start,end,onRange,initialCursor,onCursorChange,onReviewClip}: {dataset:Dataset;taskId?:string;sampleId:string;sampleCollectionId:string;origin:number;start:number|null;end:number|null;onRange:(a:number,b:number)=>void;initialCursor?:number;onCursorChange?:(seconds:number)=>void;onReviewClip?:(a:number,b:number)=>void}) {
  const material=useQuery({queryKey:["source-material",dataset.id,dataset.sha256],queryFn:({signal})=>apiFetch<Material>(`/api/v1/datasets/${dataset.id}/source-material`,{method:"POST",signal}),retry:false});
  return <>{material.isPending && <p role="status">正在读取声音…</p>}{material.isError && <p role="alert">{material.error.message}</p>}{material.data && <AudioInspector source={dataset} materialContext={{material:material.data,taskId,sampleId,sampleCollectionId}} origin={origin} initialStart={start} initialEnd={end} onViewRange={onRange} hideRangeAction initialCursor={initialCursor} onCursorChange={onCursorChange} onReviewClip={onReviewClip}/>}</>;
}
