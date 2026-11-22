"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { datasetsApi } from "@/lib/api-endpoints";
import type { Dataset } from "@/lib/types";
import { analysisTableChoices, analysisTableVersions, tableDimensions, versionTitle } from "@/lib/analysis-tables";
import type { Location, Material } from "@/lib/material-types";
import { DownloadButton } from "./download-button";
import { WorkspacePklImport, type PklMapping } from "./workspace-pkl-import";
import { WorkspaceJsonPreview } from "./workspace-json-preview";
import { MaterialViewer } from "./material-viewer";
import { AudioInspector } from "./workspace-audio-inspector";
import { CaseMaterialPicker } from "./case-material-picker";
import { WorkspaceMaterial } from "./workspace-material-view";
import { runTime, useMediaRun } from "./workspace-result-navigation";
import type { MediaResult } from "./workspace-media-results";
import { mediaAudioClock } from "@/lib/media-audio-clock";
import type { CaseSourceContext } from "./workspace-cases";

export function WorkspaceSourceFile({ dataset, datasets, onChoose, taskId, sampleId, sampleCollectionId, onViewRange, caseSource, onCaseChange, hasFileHeading }: {
  dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; hasFileHeading?: boolean; taskId?: string; sampleId?: string; sampleCollectionId?: string; caseSource?:CaseSourceContext; onCaseChange?:(value:CaseSourceContext|undefined)=>void; onViewRange?:(start:number,end:number)=>void;
}) {
  if (/^(pdf|txt|md|markdown|png|jpg|jpeg|gif|webp|wav|mp3|mp4)$/i.test(dataset.file_format)) return sampleId && sampleCollectionId ? <SourceMaterial onViewRange={onViewRange} dataset={dataset} taskId={taskId} sampleId={sampleId} sampleCollectionId={sampleCollectionId} caseSource={caseSource} /> : <CaseMaterialPicker key={dataset.id} dataset={dataset} datasets={datasets} taskId={taskId} onCaseChange={onCaseChange}>{(selected,collection)=><SourceMaterial onViewRange={onViewRange} dataset={dataset} taskId={taskId} sampleId={selected} sampleCollectionId={collection}/>}</CaseMaterialPicker>;
  return <SourceDataFile dataset={dataset} datasets={datasets} onChoose={onChoose} hasFileHeading={hasFileHeading} />;
}

function SourceMaterial({ dataset, taskId, sampleId, sampleCollectionId, onViewRange, caseSource }: { dataset: Dataset; taskId?: string; sampleId?:string; sampleCollectionId?:string; caseSource?:CaseSourceContext; onViewRange?:(start:number,end:number)=>void }) {
  const material = useQuery({
    queryKey: ["source-material", dataset.id, dataset.sha256],
    queryFn: ({ signal }) => apiFetch<Material>(`/api/v1/datasets/${dataset.id}/source-material`, { method: "POST", signal }),
    retry: false,
  });
  return <section className="rw-source-material">
    {material.isPending && <p role="status">正在读取内容…</p>}
    {material.isError && <p role="alert" className="rw-error">{material.error.message}<button onClick={() => void material.refetch()}>重试</button></p>}
    {material.data && (/^(audio|video)\//.test(material.data.mime_type)
      ? dataset.media_result && dataset.file_format==="wav"
        ? <DerivedAudioMaterial key={dataset.id} material={material.data} dataset={dataset} taskId={taskId} onViewRange={onViewRange}/>
        : <OriginalMedia key={`${dataset.id}:${dataset.sha256}:${taskId || "project"}:${caseSource?.collectionId || ""}:${caseSource?.sampleId || ""}:${caseSource?.start ?? ""}:${caseSource?.end ?? ""}`} material={material.data} dataset={dataset} taskId={taskId} sampleId={sampleId} sampleCollectionId={sampleCollectionId} caseSource={caseSource} onViewRange={onViewRange}/>
      : <WorkspaceMaterial material={material.data} taskId={taskId} sampleId={sampleId} sampleCollectionId={sampleCollectionId}/>)}
  </section>;
}

type StoredRange = { start?: number; end?: number };
type OriginalMediaProps = { material: Material; dataset: Dataset; taskId?: string; sampleId?: string; sampleCollectionId?: string; onViewRange?:(start:number,end:number)=>void; caseSource?:CaseSourceContext; audioClock?:ReturnType<typeof mediaAudioClock> };

function DerivedAudioMaterial(props:OriginalMediaProps) {
  const run=useMediaRun(props.dataset), packageId=run.data?.package_id;
  const result=useQuery({queryKey:["media-result",packageId],enabled:!!packageId,
    queryFn:({signal})=>apiFetch<MediaResult>(`/api/v1/datasets/${packageId}/media-result`,{signal})});
  if(run.isError||result.isError)return <p role="alert">{run.error?.message||result.error?.message}</p>;
  if(run.isPending||!!packageId&&result.isPending)return <p role="status">正在读取原录音关系…</p>;
  const files=result.data?.media.filter(file=>file.id===props.dataset.id)||[];
  const entries=result.data?.index.entries.filter(entry=>files.some(file=>file.name===entry.audio_file?.split(/[\\/]/).pop()))||[];
  const clock=entries.length===1?mediaAudioClock(entries[0]):undefined;
  if(!clock?.annotationSource)return <p role="alert">此音轨的原录音关系不可用，请从该次处理结果打开原材料。</p>;
  return <OriginalMedia {...props} audioClock={clock}/>;
}

function OriginalMedia(props: OriginalMediaProps) {
  const endpoint = `/api/v1/datasets/${props.dataset.id}/original-time-range`;
  const saved = useQuery({ queryKey: ["original-time-range", props.dataset.id, props.dataset.sha256, props.taskId],
    queryFn: ({signal}) => apiFetch<StoredRange>(props.taskId ? endpoint + "?task_id=" + props.taskId : endpoint, {signal}),
    refetchOnWindowFocus: false, staleTime: 0, refetchOnMount: "always" });
  if (saved.isError) return <p role="alert">{saved.error.message} <button onClick={() => void saved.refetch()}>重试</button></p>;
  if (!saved.data || saved.isFetching) return <p role="status">正在读取选段…</p>;
  return <OriginalMediaPlayer {...props} endpoint={endpoint} initial={props.caseSource?.start!=null && props.caseSource.end!=null ? {start:props.caseSource.start,end:props.caseSource.end} : saved.data} />;
}

function OriginalMediaPlayer({material, dataset, taskId, sampleId, sampleCollectionId, endpoint, initial, onViewRange,audioClock,caseSource}: OriginalMediaProps & { endpoint: string; initial: StoredRange }) {
  const origin=audioClock?.origin??0;
  const cursor = useRef(initial.start ?? 0);
  const reportRange = useRef(onViewRange);
  reportRange.current = onViewRange;
  useEffect(() => {
    if (initial.start != null && initial.end != null) reportRange.current?.(initial.start, initial.end);
  }, [initial.start, initial.end]);
  const [mediaView,setMediaView]=useState<"video"|"audio">("video");
  const [location,setLocation] = useState<Extract<Location,{kind:"time"}> | null>(() => initial.start != null && initial.end != null
    ? {kind:"time", start_seconds:initial.start, end_seconds:initial.end, note:""} : null);
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const serial = useRef(Promise.resolve());
  const savedRange = useRef(initial);
  const generation = useRef(0);
  const [error,setError] = useState("");
  const flush = useRef<(() => void) | null>(null);
  useEffect(() => () => {
    if (pending.current) clearTimeout(pending.current);
    flush.current?.();
  }, []);
  function remember(range: Extract<Location,{kind:"time"}>) {
    setLocation(range);
    onViewRange?.(range.start_seconds,range.end_seconds);
    if (pending.current) clearTimeout(pending.current);
    const index = ++generation.current;
    const start = range.start_seconds, end = range.end_seconds;
    const duration = material.metadata.duration_seconds;
    if (start < 0 || end <= start || (duration && end > duration + .002)) return;
    flush.current = () => {
      flush.current = null;
      pending.current = null;
      serial.current = serial.current.then(async () => {
        if (index !== generation.current) return;
        const old = savedRange.current;
        const unchanged=(old.start === start && old.end === end) || (old.start == null && start === 0 && duration && Math.abs(duration-end) <= .002);
        try {
          if (unchanged) return;
          savedRange.current = await apiFetch<StoredRange>(endpoint, {method:"PUT", body:JSON.stringify({task_id:taskId,sha256:dataset.sha256,start,end})});
          if (index !== generation.current) return;
          if (caseSource?.audioSource) {
            const shift=caseSource.offset || 0;
            if (start-shift>=0 && end-shift>start-shift) await apiFetch(`/api/v1/datasets/${caseSource.collectionId}/sample-selection`, {method:"POST",body:JSON.stringify({task_id:taskId,sample_id:caseSource.sampleId,start:start-shift,end:end-shift,audio_source:caseSource.audioSource})});
          }
          setError("");
          window.dispatchEvent(new CustomEvent("statforge:material-context-changed", {detail:{project:dataset.project_id,task:taskId || "project",dataset:dataset.id}}));
        } catch (reason) { if (index === generation.current) setError(reason instanceof Error ? reason.message : "选段未保存，请重新选择。"); }
      });
    };
    pending.current = setTimeout(() => flush.current?.(), 300);
  }
  const audioOnly=material.mime_type.startsWith("audio/");
  return <>
    {!audioOnly && <div className="rw-media-tabs" role="tablist" aria-label="材料查看方式"><button role="tab" aria-selected={mediaView==="video"} onClick={()=>setMediaView("video")}>视频查看</button><button role="tab" aria-selected={mediaView==="audio"} onClick={()=>setMediaView("audio")}>声音查看</button></div>}
    {!audioOnly&&mediaView==="video"&&<MaterialViewer material={material} page={1} location={null} initialCursor={cursor.current} onCursorChange={time=>{cursor.current=time;}} sharedRange={{start:location?.start_seconds??null,end:location?.end_seconds??null}} onTimeRangeChange={remember} taskId={taskId} sampleId={sampleId} sampleCollectionId={sampleCollectionId}/>}
    {(audioOnly||mediaView==="audio") && <AudioInspector source={dataset} origin={origin} annotationSource={audioClock?.annotationSource} materialContext={{material,taskId,sampleId,sampleCollectionId}} initialCursor={cursor.current+origin} onCursorChange={time=>{cursor.current=time-origin;}} onReviewClip={audioOnly ? undefined : (start,end)=>{
      cursor.current=start-origin; remember({kind:"time",start_seconds:start-origin,end_seconds:end-origin,note:""}); setMediaView("video");
    }} initialStart={location==null?undefined:location.start_seconds+origin} initialEnd={location==null?undefined:location.end_seconds+origin} onViewRange={(start,end)=>remember({kind:"time",start_seconds:start-origin,end_seconds:end-origin,note:""})}/>}
    {error && <p role="alert">{error}</p>}</>;
}

function SourceDataFile({ dataset, datasets, onChoose, hasFileHeading }: {
  dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; hasFileHeading?: boolean;
}) {
  const client = useQueryClient();
  const [mapping, setMapping] = useState<PklMapping | null>(null);
  const [configure, setConfigure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sheet, setSheet] = useState("");
  const [encoding, setEncoding] = useState("auto");
  const isPkl = /^(pkl|pickle)$/.test(dataset.file_format);
  const canPrepare = isPkl || /^(csv|xlsx|parquet)$/.test(dataset.file_format);
  const sheets = useQuery({
    queryKey: ["source-sheets", dataset.id],
    enabled: configure && dataset.file_format === "xlsx",
    queryFn: ({ signal }) => apiFetch<{ sheet_names: string[] }>(`/api/v1/datasets/${dataset.id}/source-sheets`, { signal }),
  });
  const selectedSheet = sheet || (sheets.data?.sheet_names.length === 1 ? sheets.data.sheet_names[0] : "");
  const tables = useMemo(() => analysisTableVersions(datasets), [datasets]);
  const related = datasets.filter(item => item.source_dataset_id === dataset.id && item.upload_status === "ready");
  const versions = analysisTableChoices(related, tables);
  const existing = related.some(data => data.prepared_source_sha256 === dataset.sha256);
  const priorCopies = related.filter(item => (tables.get(item.id)?.copies.length ?? 0) > 1)
    .sort((a,b) => b.created_at.localeCompare(a.created_at));
  async function prepare() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await apiFetch<Dataset>(`/api/v1/datasets/${dataset.id}/prepare-source`, {
        method: "POST",
        body: JSON.stringify({ mapping: isPkl ? mapping : null, sheet_name: selectedSheet || null, csv_encoding: encoding }),
      });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      onChoose(result.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "生成分析数据失败，原文件仍保留在项目中。");
    } finally { setBusy(false); }
  }
  const download = (
    <DownloadButton identity={dataset.id} download={signal => datasetsApi.download(dataset.id, dataset.original_filename, signal)}>
      下载原文件
    </DownloadButton>
  );
  const versionList = versions.length > 0 && (
    <div className="rw-source-versions">
      <h3>已生成的分析数据</h3>
      {versions.map(item => {
        const table = tables.get(item.id);
        return <button type="button" key={item.id} title={item.original_filename} onClick={() => onChoose(item.id)}>
          {[table ? versionTitle(table) : item.original_filename, table?.change, tableDimensions(item)].filter(Boolean).join(" · ")}
        </button>;
      })}
    </div>
  );
  const history = !!priorCopies.length && <details className="rw-source-versions"><summary>生成记录</summary>
    {priorCopies.map(item => <button type="button" key={item.id} onClick={()=>onChoose(item.id)}>
      {versionTitle(tables.get(item.id)!)} · {runTime(/(?:Z|[+-]\d{2}:\d{2})$/i.test(item.created_at) ? item.created_at : item.created_at+"Z")}
    </button>)}</details>;
  if (dataset.file_format === "json") {
    return <section className="rw-data-view rw-source-view">
      {!hasFileHeading && <h2 className="rw-source-filename">{dataset.original_filename}</h2>}
      <WorkspaceJsonPreview
        key={dataset.id + dataset.sha256}
        dataset={dataset}
        busy={busy}
        onPrepare={() => void prepare()}
        prepareError={error}
        hasTable={existing}
        download={download}
      />
      {(!existing || versions.length > 1) && versionList}
      {history}
    </section>;
  }
  return <section className="rw-data-view rw-source-view">
    <div className="rw-content-tools">
      <span>原文件 · {dataset.file_format.toUpperCase()} · {(dataset.file_size / 1024 / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB</span>
      {download}
    </div>
    <div className="rw-source-body">
      <h2>{dataset.original_filename}</h2>
      {versionList}
      {history}
      {canPrepare && !configure && <button className="rw-primary" onClick={() => setConfigure(true)}>{isPkl ? "配置标签与模态" : "准备分析数据"}</button>}
      {configure && <div className="rw-source-settings">
        <h3>{isPkl ? "标签与模态" : "读取设置"}</h3>
        {isPkl && <WorkspacePklImport datasetId={dataset.id} disabled={busy} onChange={setMapping} />}
        {dataset.file_format === "xlsx" && <label className="rw-field">工作表
          <SearchSelect disabled={busy || sheets.isPending} value={selectedSheet} onChange={e => setSheet(e.target.value)}>
            <option value="">选择工作表</option>{sheets.data?.sheet_names.map(name => <option key={name}>{name}</option>)}
          </SearchSelect>
          {sheets.isError && <span role="alert" className="rw-error">{sheets.error.message}<button onClick={() => void sheets.refetch()}>重读工作表</button></span>}
        </label>}
        {dataset.file_format === "csv" && <label className="rw-field">文件编码
          <SearchSelect disabled={busy} value={encoding} onChange={e => setEncoding(e.target.value)}>
            <option value="auto">自动识别</option><option value="utf-8">UTF-8</option><option value="gb18030">GB18030 / GBK</option><option value="big5">Big5</option><option value="cp1252">Windows-1252</option><option value="latin-1">Latin-1</option>
          </SearchSelect>
        </label>}
        <button className="rw-primary" disabled={busy || (isPkl && !mapping) || (dataset.file_format === "xlsx" && !selectedSheet)} onClick={() => void prepare()}>{busy ? "正在生成分析数据…" : "生成分析数据"}</button>
        {error && <p role="alert" className="rw-error">{error}</p>}
      </div>}
    </div>
  </section>;
}
