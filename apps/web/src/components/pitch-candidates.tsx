"use client";

import {useEffect, useState} from "react";
import {useQuery, useQueryClient} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";
import {DownloadButton} from "@/components/download-button";
import {downloadApiFile} from "@/lib/api-download";
import {SearchSelect} from "@/components/ui/search-select";
import styles from "./workspace-audio-inspector.module.css";

type Summary={mean:number|null;cv:number|null;range:number|null;valid_count:number;total_count:number};
type Candidate={index:number;frequency:number;strength:number;voiced:boolean;selectable:boolean};
type PathParameters={silence_threshold:number;voicing_threshold:number;octave_cost:number;octave_jump_cost:number;voiced_unvoiced_cost:number;ceiling:number};
const pathFields: {key:keyof PathParameters;label:string}[]=[{key:"silence_threshold",label:"静音阈值"},{key:"voicing_threshold",label:"有声阈值"},{key:"octave_cost",label:"倍频代价"},{key:"octave_jump_cost",label:"跳频代价"},{key:"voiced_unvoiced_cost",label:"有声/无声切换代价"},{key:"ceiling",label:"基频上限 Hz"}];
type PitchResponse={path_parameters:PathParameters;path_engine:string;available:boolean;package_id:string;root_package_id:string;parent_package_id?:string;revision:number;summary:Summary;original_summary:Summary;frame:{index:number;time:number;player_time:number;intensity:number;original_selected:number;selected:number;original_hz:number|null;value:number|null;candidates:Candidate[]};total_frames:number;versions:{package_id:string;revision:number;label:string;created_at:string}[]};
const number=(v:number|null|undefined)=>v==null?"—":Number(v.toFixed(4)).toString();

export function PitchCandidates({packageId,sampleId,point,view,selection,onLocate,onVersion,onReprocess}:{packageId:string;sampleId:string;point:number|null;view:[number,number]|null;selection:{start:number;end:number}|null;onLocate:(time:number)=>void;onVersion?:(id:string)=>void;onReprocess?:()=>void}) {
  const openKey=`statforge:pitch-open:${packageId}:${sampleId}`;
  const [open,setOpen]=useState(()=>typeof window!=="undefined"&&localStorage.getItem(openKey)==="1"),[frameIndex,setFrameIndex]=useState<number|null>(null),[frameInput,setFrameInput]=useState(""),[changes,setChanges]=useState<Record<number,number>>({}),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [parameters,setParameters]=useState<PathParameters|null>(null),[format,setFormat]=useState("long");
  const client=useQueryClient();
  useEffect(()=>{setFrameIndex(null);},[point]);
  const result=useQuery({queryKey:["pitch-candidates",packageId,sampleId,point,frameIndex,view,selection?.start,selection?.end],enabled:open,queryFn:({signal})=>apiFetch<PitchResponse>(`/api/v1/datasets/${packageId}/pitch-candidates`,{method:"POST",signal,body:JSON.stringify({sample_id:sampleId,...(frameIndex!=null?{frame_index:frameIndex}:point!=null?{point}:{}),...(view?{start:view[0],end:view[1]}:selection||{})})})});
  const data=result.data, frame=data?.frame;
  useEffect(()=>{setParameters(data?.path_parameters??null);},[data?.package_id,sampleId]);
  useEffect(()=>{if(frame)setFrameInput(String(frame.index));},[frame?.index]);
  useEffect(()=>{if(open&&frame)onLocate(frame.player_time);},[frame?.index,frame?.player_time]);
  function navigate(index:number){setFrameIndex(index);}
  function version(id:string){localStorage.setItem(`statforge:pitch-open:${id}:${sampleId}`,"1");onVersion?.(id);}
  async function save(action:"edit"|"undo"|"reselect") {
    setBusy(true);setError("");
    try {
      const response=await apiFetch<{package_id:string}>(`/api/v1/datasets/${packageId}/pitch-revisions`,{method:"POST",body:JSON.stringify({sample_id:sampleId,action,changes:action==="edit"?Object.entries(changes).map(([index,candidate_index])=>({frame_index:Number(index),candidate_index})):[],...(action==="reselect"?{parameters}: {})})});
      await Promise.all([client.invalidateQueries({queryKey:["datasets"]}),client.invalidateQueries({queryKey:["media-samples"]}),client.invalidateQueries({queryKey:["workspace-files"]})]);
      version(response.package_id);
    } catch(reason){setError(reason instanceof Error?reason.message:"无法保存修订。");} finally{setBusy(false);}
  }
  return <details className={styles.pitchCandidates} open={open} onToggle={e=>{setOpen(e.currentTarget.open);localStorage.setItem(openKey,e.currentTarget.open?"1":"0");}}><summary>查看和修订基频候选</summary>
    {open&&result.isPending&&<p role="status">正在读取候选…</p>}{result.isError&&<p role="alert">{result.error.message}</p>}
    {data&&!data.available&&<p>这次结果未保存候选，可用原材料重新计算。 {onReprocess&&<button type="button" onClick={onReprocess}>返回材料重新处理</button>}</p>}
    {data?.available&&frame&&<>
      <div className={styles.pitchSummary}><strong>{data.revision===0?"原始结果":`修订 ${data.revision}`}</strong><span>均值 {number(data.summary.mean)} Hz</span><span>CV {number(data.summary.cv)}</span><span>极差 {number(data.summary.range)} Hz</span><span>有效帧 {data.summary.valid_count}/{data.summary.total_count}</span></div>
      <div className={styles.pitchActions}><label>帧 <input type="number" min={1} max={data.total_frames} value={frameInput} onChange={e=>setFrameInput(e.target.value)}/> / {data.total_frames}</label><button type="button" disabled={!Number.isInteger(Number(frameInput))||Number(frameInput)<1||Number(frameInput)>data.total_frames} onClick={()=>navigate(Number(frameInput))}>查看帧</button><button type="button" disabled={frame.index<=1} onClick={()=>navigate(frame.index-1)}>上一帧</button><button type="button" disabled={frame.index>=data.total_frames} onClick={()=>navigate(frame.index+1)}>下一帧</button><button type="button" onClick={()=>onLocate(frame.player_time)}>定位 {number(frame.player_time)} 秒</button></div>
      <div className={styles.statistics}><table><thead><tr><th>候选</th><th>频率 Hz</th><th>相关峰强度</th><th>原始</th><th>当前选中</th></tr></thead><tbody>{frame.candidates.map(candidate=><tr key={candidate.index}><td><label><input type="radio" name={`pitch-frame-${frame.index}`} checked={(changes[frame.index]??frame.selected)===candidate.index} disabled={!candidate.selectable||busy||!onVersion} onChange={()=>setChanges(old=>({...old,[frame.index]:candidate.index}))}/>{candidate.frequency===0?"无声":candidate.voiced?"有声":"超过上限"}</label></td><td>{number(candidate.frequency)}</td><td>{number(candidate.strength)}</td><td>{candidate.index===frame.original_selected?"原始":"—"}</td><td>{candidate.index===frame.selected?"当前":"—"}</td></tr>)}</tbody></table></div>
      <div className={styles.pitchActions}><button type="button" disabled={busy||!onVersion||!Object.keys(changes).length} onClick={()=>void save("edit")}>{busy?"正在保存…":"保存修订"}</button><button type="button" disabled={busy||!onVersion||!!Object.keys(changes).length||data.revision===0||!data.parent_package_id} onClick={()=>void save("undo")}>撤销上次修订</button>{data.versions.length>0&&<label>查看版本 <SearchSelect aria-label="基频结果版本" value={packageId} disabled={busy||!onVersion||!!Object.keys(changes).length} onChange={e=>version(e.target.value)}>{data.versions.map(item=><option key={item.package_id} value={item.package_id}>{item.label|| (item.revision===0?"原始结果":`修订 ${item.revision}`)}</option>)}</SearchSelect></label>}</div>
      <details className={styles.pitchParameters}><summary>重选基频路径</summary>
        <p>路径参数{data.path_engine?` · ${data.path_engine}`:""}</p>
        {parameters&&<div className={styles.pitchParameterFields}>{pathFields.map(({key,label})=><label key={key}>{label}<input type="number" step="any" value={Number.isFinite(parameters[key])?parameters[key]:""} onChange={e=>setParameters(old=>old?{...old,[key]:e.target.valueAsNumber}:old)}/></label>)}</div>}
        <button type="button" disabled={busy||!onVersion||!parameters||Object.values(parameters).some(value=>!Number.isFinite(value))||!!Object.keys(changes).length} onClick={()=>void save("reselect")}>重选基频路径</button>
        {!!Object.keys(changes).length&&<p>请先保存或清除手动修改，再重选路径。</p>}
      </details>
      <div className={styles.pitchActions}><label>导出格式 <SearchSelect aria-label="基频导出格式" value={format} onChange={e=>setFormat(e.target.value)}><option value="long">长文本</option><option value="short">短文本</option><option value="binary">二进制</option></SearchSelect></label><DownloadButton identity={`${packageId}:${sampleId}:${format}`} download={signal=>downloadApiFile(`/api/v1/datasets/${packageId}/pitch-export?sample_id=${encodeURIComponent(sampleId)}&format=${format}`,`pitch-${sampleId}.Pitch`,signal)}>导出当前基频</DownloadButton></div>
      {!!Object.keys(changes).length&&<p>已修改 {Object.keys(changes).length} 帧，保存后生成新版本。 <button type="button" disabled={busy} onClick={()=>setChanges({})}>清除手动修改</button></p>}{error&&<p role="alert">{error}</p>}
    </>}
  </details>;
}
