"use client";

import { SearchSelect } from "@/components/ui/search-select";
import { useEffect, useState } from "react";
import { AudioInspector, type CurveSample } from "./workspace-audio-inspector";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { datasetsApi } from "@/lib/api-endpoints";
import type { Dataset } from "@/lib/types";
import { MaterialSuggestions } from "./material-suggestions";
import { DownloadButton } from "./download-button";
import { annotationLocationLabel, transcriptVersionLabel, useWorkspaceAnnotations } from "./workspace-annotations";
import { codingApi } from "@/lib/text-coding";
import { mediaAudioClock } from "@/lib/media-audio-clock";
import {PitchAudioAssociation,type NativePitch} from "./workspace-pitch";

type Props = {dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; taskId?: string};
type SavedSpeechRate = {count?:number|null; unit:string; count_source:string; duration_seconds:number; range_note?:string};
type Entry = {formant_path?:import("./formant-candidates").FormantMethod;formant_revision?:{selection?:{start:number;end:number}};audio_preprocessing?:{kind:string;low_hz:number;high_hz:number;transition_hz:number;praat:string;sample_rate:number};speech_rate?:SavedSpeechRate;sample?: {sample_id: string; title?:string; display_title?:string; video_id?:string; audio_id?:string; emotion?: string}; image?: {id: number; file_name: string}; array_prefix?: string; segment?: {player_start:number;player_end:number;audio_player_start?:number;audio_player_end?:number;first_audio_pts_seconds?:number}; audio_selection?:"video"|"independent"; audio_origin_seconds?:number;audio_alignment?:{effective_offset_seconds?:number}; audio_file?:string; frame_files?:string[]};
export type MediaResult = {curve_previews?:CurveSample[];index: {kind: string; entries: Entry[];native_pitch?:NativePitch}; arrays: Record<string,{shape:number[];dtype:string}>; media:{id:string;name:string}[]; method_parameters?: Record<string,unknown>|null; run: {summary_id: string; package_id: string; completed_at: string;parameters?:Record<string,unknown>}};

export function MediaRowLink({dataset,row,onChoose,taskId,datasets,label="回看原样本",range}: Props & {row: Record<string, unknown>;label?:string;range?:{start:number;end:number}}) {
  const client = useQueryClient();
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  async function locate() {
    setBusy(true); setError("");
    try {
      if(dataset.visual_result){const id=String(row.sample_id),expected=dataset.visual_result.sources[id];
        if(!expected||!datasets.some(d=>d.id===id&&d.sha256===expected))throw new Error("此图像版本已不可用，请在工作区重新打开原图。");
        onChoose(id);return;
      }
      if (dataset.media_result) {
        const id = dataset.media_result.collection_id;
        const sampleValue = row.sample_id ?? row.image_id;
        if (sampleValue == null || !String(sampleValue)) throw new Error("此行没有可核验的样本编号。");
        const sampleId = String(sampleValue);
        const result = await apiFetch<MediaResult>(`/api/v1/datasets/${dataset.id}/media-result`);
        if(result.index.native_pitch){onChoose(result.index.native_pitch.association?.audio_id||result.index.native_pitch.source_id);return;}
        const matches = result.index.entries.filter(item => item.sample?.sample_id === sampleId || String(item.image?.id ?? "") === sampleId);
        if (matches.length !== 1) throw new Error("无法在原结果中唯一定位此样本。");
        const savedSelection = matches[0].formant_revision?.selection;
        const start = range?.start ?? savedSelection?.start ?? matches[0].segment?.player_start, end = range?.end ?? savedSelection?.end ?? matches[0].segment?.player_end;
        await apiFetch(`/api/v1/datasets/${id}/sample-selection`, {method:"POST",body:JSON.stringify({task_id:taskId,sample_id:sampleId,start:start ?? null,end:end ?? null,audio_source:matches[0].audio_selection})});
        // The target preview initializes its selection once. Do not let an
        // inactive cached response initialize it with the previous sample.
        client.removeQueries({queryKey:["media-samples",id,taskId]});
        sessionStorage.removeItem("statforge:material-results:"+id+":"+(taskId || "project"));
        sessionStorage.setItem("statforge:reveal-media:"+id+":"+(taskId||"project"),JSON.stringify({sample_id:sampleId,start,end}));
        onChoose(id);
      } else if (dataset.source_dataset_id && row.Index != null) {
        const id = dataset.source_dataset_id;
        await apiFetch(`/api/v1/datasets/${id}/signals/preview`, {method:"POST",body:JSON.stringify({task_id:taskId,window:Number(row.Index)-1})});
        onChoose(id);
      }
    } catch(reason) {setError(reason instanceof Error ? reason.message : "无法定位原样本。");}
    finally {setBusy(false);}
  }
  if (dataset.coding_summary) return <CodingReferences dataset={dataset} row={row}/>;
  return <><button type="button" disabled={busy} onClick={() => void locate()}>{label}</button>{error && <span role="alert" className="rw-error">{error}</span>}</>;
}


function CodingReferences({dataset,row}:{dataset:Dataset;row:Record<string,unknown>}) {
  const annotations=useWorkspaceAnnotations();
  const snapshot=useQuery({queryKey:["coding-summary-snapshot",dataset.id],queryFn:({signal})=>codingApi.summarySnapshot(dataset.project_id,dataset.id,signal)});
  const sampleId=typeof row.sample_id === "string" ? row.sample_id : "";
  const collectionId=typeof row.sample_collection_id === "string" ? row.sample_collection_id : "";
  const caseKey=typeof row.case_key === "string" ? row.case_key : "";
  const references=dataset.coding_summary?.sample_segments.filter(item=>item.sample_id===sampleId && (caseKey && item.case_key ? item.case_key===caseKey : item.sample_collection_id===collectionId)) || [];
  return <details><summary>回看汇总时的标注（{references.length}）</summary>
    <p>汇总保留生成时的标注；版本状态按当前转录显示。</p>
    {snapshot.isPending && <p>正在读取快照…</p>}
    {snapshot.isError && <p role="alert">编码快照暂不可读取。</p>}
    {snapshot.data && references.map(ref=>{
      const segment=snapshot.data.payload.segments.find(value=>value.id===ref.segment_id && value.sample_id===sampleId && value.sample_collection_id===ref.sample_collection_id && (value.material_id ?? null)===ref.material_id && (value.material_sha256 ?? null)===ref.material_sha256 && (value.material_version ?? null)===ref.material_version && JSON.stringify(value.locator)===JSON.stringify(ref.locator));
      if(!segment) return <p key={ref.segment_id}>快照中的标注不可用。</p>;
      const themes=segment.theme_ids.map(id=>snapshot.data!.payload.themes.find(theme=>theme.id===id)?.name).filter(Boolean).join("、");
      return <div key={ref.segment_id}><button type="button" onClick={()=>{annotations?.focus(segment);annotations?.setOpen(true);}} disabled={!annotations}>{segment.filename} · {annotationLocationLabel(segment.locator)}</button>
        <p>{themes || "备注"}{segment.memo ? ` · ${segment.memo}` : ""}</p>
        {transcriptVersionLabel(segment,snapshot.data.transcription_status?.[segment.id],snapshot.data.transcription_info?.[segment.id]) && <small>{transcriptVersionLabel(segment,snapshot.data.transcription_status?.[segment.id],snapshot.data.transcription_info?.[segment.id])}</small>}
      </div>;
    })}
    {!references.length && <p>没有与该案例精确匹配的标注。</p>}
  </details>;
}

export function MediaCurves({packageId,datasets,onRange,onChoose,taskId}: {packageId:string;datasets:Dataset[];taskId?:string;onChoose?:(id:string)=>void;onRange?:(sampleId:string,start:number,end:number,source:"video"|"independent")=>void}) {
  const result=useQuery({queryKey:["media-result",packageId],queryFn:({signal})=>apiFetch<MediaResult>(`/api/v1/datasets/${packageId}/media-result`,{signal})});
  const viewKey="statforge:curve-sample:"+packageId+":"+(taskId||"project");
  const [id,setId]=useState(()=>typeof window!=="undefined"?sessionStorage.getItem(viewKey)||"":"");
  const [range,setRange]=useState<{sampleId:string;start:number;end:number}|null>(null);
  const annotations=useWorkspaceAnnotations();
  const [annotationError,setAnnotationError]=useState("");

  useEffect(()=>{setId(sessionStorage.getItem(viewKey)||"");setRange(null);},[viewKey]);
  function choose(id:string){sessionStorage.setItem(viewKey,id);setId(id);setRange(null);}

  const samples=result.data?.curve_previews || [];
  const sample=samples.find(item=>item.sample_id===id)||samples[0];
  const entry=result.data?.index.entries.find(item=>item.sample?.sample_id===sample?.sample_id);
  const name=entry?.audio_file?.split(/[\\/]/).pop();
  const matches=result.data?.media.filter(item=>item.name===name) || [];
  const native=result.data?.index.native_pitch;
  const media=native?.association ? {id:native.association.audio_id,name:native.association.name} : matches.length===1 ? matches[0] : undefined;
  const known=media && datasets.find(item=>item.id===media.id);
  const source=useQuery({queryKey:["media-audio-dataset",media?.id],queryFn:({signal})=>apiFetch<Dataset>(`/api/v1/datasets/${media!.id}`,{signal}),enabled:!!media && !known,retry:false});
  if(result.isPending)return <p role="status">正在读取声音曲线…</p>;
  if(result.isError)return <p role="alert">{result.error.message}</p>;
  if (!sample) {
    const record = datasets.find(item=>item.id===packageId);
    const clips = result.data.index.entries.filter(item=>item.sample && item.segment);
    return <><p>此结果没有声音曲线。</p>{record && onChoose && clips.map(item =>
      <div className="rw-inline-actions" key={item.sample!.sample_id}>
        <span>{item.sample!.display_title || item.sample!.title} · {item.segment!.player_start}–{item.segment!.player_end} 秒</span>
        {onRange ? <button onClick={()=>onRange(item.sample!.sample_id,item.segment!.player_start,item.segment!.player_end,item.audio_selection || "video")}>回看原片段</button> :
          <MediaRowLink dataset={record} datasets={datasets} taskId={taskId} onChoose={onChoose} row={{sample_id:item.sample!.sample_id}} label="回看原片段"/>}
      </div>)}</>;
  }
  const {origin,annotationSource}=native ? {origin:-(native.association?.offset_seconds||0),annotationSource:undefined} : mediaAudioClock(entry);
  const options=result.data?.run.parameters || {};
  const methods=result.data?.method_parameters as {audio?:{sample_rate?:number;channels?:number;window_ms?:number;hop_ms?:number};praat?:{pitch_method?:string;pitch_engine?:{engine:string;version:string;praat_version?:string};pitch_parameters?:Record<string,number|boolean>;versions?:{praat?:string;parselmouth?:string};pitch_floor_hz?:number;pitch_ceiling_hz?:number;time_step_seconds?:number;intensity_minimum_pitch_hz?:number;intensity_time_step_seconds?:number;intensity_subtract_mean?:boolean;bandwidth_method?:string;formant_ceiling_hz?:number;formant_window_seconds?:number;formant_preemphasis_hz?:number}} | undefined;
  const steps=Array.isArray(options.steps) ? options.steps : [];
  const praat=methods?.praat;
  const pitchMethod=({native_object:"导入的 Pitch 对象",filtered_autocorrelation:"滤波自相关",raw_autocorrelation:"原始自相关",autocorrelation:"原始自相关",raw_cross_correlation:"原始互相关",filtered_cross_correlation:"滤波互相关"} as Record<string,string>)[praat?.pitch_method || ""];
  const pitchEngine=praat?.pitch_engine;
  const pitchVersion=pitchEngine?.engine==="Praat" ? "Praat "+pitchEngine.version : "Praat "+(pitchEngine?.praat_version || praat?.versions?.praat || "")+(pitchEngine?.version || praat?.versions?.parselmouth ? " · Parselmouth "+(pitchEngine?.version || praat?.versions?.parselmouth) : "");
  const speech=entry?.speech_rate;
  const countUnit=speech?.unit==="english_words" ? "英文词" : "汉字";
  const output=datasets.find(item=>item.id===packageId);
  const currentRange=range?.sampleId===sample.sample_id ? range : entry?.formant_revision?.selection ?? (entry?.segment ? {start:entry.segment.player_start,end:entry.segment.player_end}:undefined);
  const originalLabel=currentRange ? "回看原片段" : entry?.sample?.video_id ? "回看原视频":"回听原录音";
  async function annotateSelection(){
    setAnnotationError("");
    try{
      const rawId=entry?.sample?.video_id||entry?.sample?.audio_id;
      if(!rawId||!currentRange||!output?.media_result)throw new Error("请先选择可回查的原片段。");
      const material=await apiFetch<import("@/lib/material-types").Material>(`/api/v1/datasets/${rawId}/source-material`,{method:"POST"});
      annotations?.begin({filename:material.filename,material_id:material.id,material_sha256:material.sha256,material_version:material.version,
        dataset_id:rawId,sample_id:sample.sample_id,sample_collection_id:output.media_result.collection_id,task_id:taskId},
        {kind:"time",start_seconds:currentRange.start,end_seconds:currentRange.end,note:""},()=>{
          onRange?.(sample.sample_id,currentRange.start,currentRange.end,entry?.audio_selection||"video");
        });
    }catch(reason){setAnnotationError(reason instanceof Error?reason.message:"无法打开标注。");}
  }
  function reviewClip() {
    const collection = output?.media_result?.collection_id;
    if (!sample || !collection || !currentRange || !onRange) return;
    sessionStorage.setItem("statforge:reveal-media:"+collection+":"+(taskId||"project"),
      JSON.stringify({sample_id:sample.sample_id,start:currentRange.start,end:currentRange.end}));
    onRange(sample.sample_id,currentRange.start,currentRange.end,entry?.audio_selection || "video");
  }
  const original = native
    ? native.association && onChoose && <button onClick={()=>onChoose(native.association!.audio_id)}>打开原音轨</button>
    : onRange && currentRange && output?.media_result
      ? <button onClick={reviewClip}>{originalLabel} · {Number(currentRange.start.toFixed(3))}–{Number(currentRange.end.toFixed(3))} 秒</button>
      : output && onChoose && <MediaRowLink dataset={output} datasets={datasets} onChoose={onChoose} taskId={taskId} row={{sample_id:sample.sample_id}} range={currentRange} label={originalLabel}/>;
  return <section className="rw-media-curves"><h3>声音曲线</h3>
    <div className="rw-current-sample">
      <label>当前样本<SearchSelect aria-label="当前样本" value={sample.sample_id} onChange={event=>choose(event.target.value)}>{samples.map(item=><option key={item.sample_id} value={item.sample_id}>{item.display_title && item.display_title!==item.title ? item.display_title+" · ":""}{item.title||item.sample_id}</option>)}</SearchSelect></label>
      {original}
      {!native&&output&&onChoose&&currentRange&&annotations&&<button onClick={()=>void annotateSelection()}>标注选段</button>}
    </div>
    {native&&<PitchAudioAssociation key={packageId+":"+(native.association?.group_id||"")} packageId={packageId} object={native} datasets={datasets}/>}
    {!native&&output&&onChoose&&<MaterialSuggestions dataset={output} taskId={taskId} version={packageId+":"+sample.sample_id}
      resultSelection={{sample_id:sample.sample_id,start:currentRange?.start,end:currentRange?.end}}
      onChoose={onChoose} onAnnotate={currentRange&&annotations ? ()=>void annotateSelection():undefined}
      onReviewSource={onRange && currentRange ? reviewClip : undefined}
      onSummary={result.data?.run.summary_id ? ()=>onChoose(result.data!.run.summary_id):undefined}/>}
    {annotationError&&<p role="alert" className="rw-error">{annotationError}</p>}

    {entry?.audio_preprocessing&&<p>滤波音轨 · {{lowpass:"低通",highpass:"高通",bandpass:"带通"}[entry.audio_preprocessing.kind]||"Hann"} · {entry.audio_preprocessing.low_hz}–{entry.audio_preprocessing.high_hz} Hz · 以下曲线使用这份音轨。</p>}
    {speech?.range_note ? <p className="rw-error">{speech.range_note}</p> :
      speech && typeof speech.count==="number" && speech.duration_seconds>0 && <p>语速 · {speech.count} {countUnit} ÷ {Number(speech.duration_seconds.toFixed(4))} 秒 ≈ {Number((speech.count/speech.duration_seconds).toFixed(4))} {countUnit}/秒 · {speech.count_source==="manual" ? "人工计数" : "文本计数"}</p>}
    {methods && <details><summary>测量方法</summary><ul>
      {entry?.audio_preprocessing&&<li>Praat {entry.audio_preprocessing.praat} Hann · 过渡宽度 {entry.audio_preprocessing.transition_hz} Hz · 先选段、滤波，再重采样到 {entry.audio_preprocessing.sample_rate} Hz、保存 PCM16。</li>}
      {methods.audio && <li>测量音轨 · {methods.audio.sample_rate} Hz · {methods.audio.channels} 声道</li>}
      {steps.includes("rms") && <li>平均短时RMS · {methods.audio?.window_ms} ms 窗，{methods.audio?.hop_ms} ms 步长；只计完整窗。整段RMS按所选音轨的全部采样点计算。</li>}
      {sample.curves.pitch&&<li>基频 · {pitchMethod||"旧结果未记录算法"}{praat?.pitch_floor_hz!=null&&praat?.pitch_ceiling_hz!=null&&` · 下限 ${praat.pitch_floor_hz} Hz · ${praat.pitch_method?.startsWith("filtered_")?"顶部频率":"上限"} ${praat.pitch_ceiling_hz} Hz`}{praat?.time_step_seconds!=null&&` · 步长 ${praat.time_step_seconds*1000} ms`}{(pitchEngine || praat?.versions?.praat) && " · "+pitchVersion}。CV 为有效基频的样本标准差除以均值，极差为最高减最低。</li>}
      {sample.curves.pitch&&praat?.pitch_parameters&&<li>{Object.entries(praat.pitch_parameters).filter(([k])=>!["time_step","pitch_floor","pitch_ceiling"].includes(k)).map(([k,v])=>({max_number_of_candidates:"候选数量",very_accurate:"高精度",attenuation_at_top:"顶部频率衰减",silence_threshold:"静音阈值",voicing_threshold:"有声阈值",octave_cost:"八度代价",octave_jump_cost:"八度跳跃代价",voiced_unvoiced_cost:"有声/无声切换代价"}[k]||k)+" "+(typeof v==="boolean"?(v?"开启":"关闭"):v)).join(" · ")}</li>}
      {steps.includes("intensity") && <li>Praat 强度 · 最低周期频率 {praat?.intensity_minimum_pitch_hz ?? "未记录"} Hz · 帧步长 {(praat?.intensity_time_step_seconds ?? praat?.time_step_seconds) != null ? Number(praat?.intensity_time_step_seconds ?? praat?.time_step_seconds)*1000+" ms" : "未记录"} · {praat?.intensity_subtract_mean===false?"不扣除局部均值":praat?.intensity_subtract_mean===true?"扣除局部均值":"均值设置未记录"} · 按能量平均（dB）{praat?.versions?.praat && " · Praat "+praat.versions.praat}。</li>}
      {steps.includes("formants") && praat?.bandwidth_method && <li>B1–B4 为对应 F1–F4 的带宽（Hz），与中心频率使用相同的 Burg 对象和时间帧。</li>}
      {steps.includes("formants") && praat && !entry?.formant_path && <li>Burg 共振峰 · 5 个峰，上限 {praat.formant_ceiling_hz} Hz，窗长 {Number(praat.formant_window_seconds)*1000} ms，预加重 {praat.formant_preemphasis_hz} Hz。</li>}
    </ul></details>}
    <AudioInspector key={`${packageId}:${sample.sample_id}`} hideRangeAction objectDomain={native?[native.clock.xmin,native.clock.xmax]:undefined} measurement={{packageId,sampleId:sample.sample_id}} pitchMethod={pitchMethod} onPitchVersion={onChoose ? id=>{sessionStorage.setItem("statforge:curve-sample:"+id+":"+(taskId||"project"),sample.sample_id);onChoose(id);} : undefined} annotationSource={annotationSource} source={known||source.data} curves={sample.curves} origin={origin} initialStart={entry?.formant_revision?.selection?.start??entry?.segment?.player_start} initialEnd={entry?.formant_revision?.selection?.end??entry?.segment?.player_end} onViewRange={(start,end)=>setRange({sampleId:sample.sample_id,start,end})} onRange={!native&&onRange ? (a,b)=>onRange(sample.sample_id,a,b,entry?.audio_selection ?? "video"):undefined}/>
  </section>;
}

export function MediaResultView(props: Props) {
  const {dataset,onChoose} = props;
  const client=useQueryClient();
  const result = useQuery({queryKey:["media-result",dataset.id],queryFn:({signal})=>apiFetch<MediaResult>(`/api/v1/datasets/${dataset.id}/media-result`,{signal})});
  return <section className="rw-data-view">
    <div className="rw-content-tools"><h2>完整特征</h2><div className="rw-inline-actions">
      <DownloadButton identity={dataset.id} download={signal=>datasetsApi.download(dataset.id,"完整特征.npz",signal)}>导出完整数组</DownloadButton>

    </div></div>
    {result.isPending && <p role="status">正在读取特征…</p>}
    {result.isError && <p role="alert">{result.error.message}</p>}
    {result.data && <>
      {!!result.data.curve_previews?.length && <MediaCurves packageId={dataset.id} datasets={props.datasets} onChoose={onChoose} taskId={props.taskId} onRange={(sampleId,start,end,audio_source)=>{void (async()=>{try {const collection=dataset.media_result?.collection_id;if(!collection)throw new Error("找不到原材料组。");await apiFetch(`/api/v1/datasets/${collection}/sample-selection`,{method:"POST",body:JSON.stringify({task_id:props.taskId,sample_id:sampleId,start,end,audio_source})});client.removeQueries({queryKey:["media-samples",collection,props.taskId]});sessionStorage.removeItem("statforge:material-results:"+collection+":"+(props.taskId || "project"));onChoose(collection);}catch(reason){window.alert(reason instanceof Error?reason.message:"无法打开原片段。");}})();}}/>}
      <div className="rw-table-scroll"><table className="rw-data-table"><thead><tr><th>样本</th><th>原材料</th></tr></thead><tbody>
        {result.data.index.entries.map((entry,i)=><tr key={i}><td>{entry.sample?.title ?? entry.sample?.sample_id ?? entry.image?.file_name}</td><td><MediaRowLink {...props} row={{sample_id:entry.sample?.sample_id ?? String(entry.image?.id),start:entry.segment?.player_start,end:entry.segment?.player_end}} />{(entry.audio_file || !!entry.frame_files?.length) && <details><summary>派生音轨与帧</summary>{[...(entry.audio_file ? [{path:entry.audio_file,label:"派生音轨"}]:[]),...(entry.frame_files || []).map((path,j)=>({path,label:`帧 ${j+1}`}))].map(({path,label})=>{const name=path.split(/[\\/]/).pop(); const media=result.data?.media?.find(m=>m.name===name); return media ? <button key={media.id} onClick={()=>onChoose(media.id)}>{label}</button>:null;})}</details>}</td></tr>)}
      </tbody></table></div>
      <details><summary>数组与维度 · {Object.keys(result.data.arrays).length} 项</summary><div className="rw-table-scroll"><table className="rw-data-table"><thead><tr><th>数组</th><th>维度</th><th>数值类型</th></tr></thead><tbody>
        {Object.entries(result.data.arrays).map(([key,value])=><tr key={key}><td>{key}</td><td>{value.shape.join(" × ")}</td><td>{value.dtype}</td></tr>)}
      </tbody></table></div></details>
    </>}
  </section>;
}
