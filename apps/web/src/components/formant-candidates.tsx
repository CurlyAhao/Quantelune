"use client";

import {useEffect, useRef, useState} from "react";
import {useQuery, useQueryClient} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";
import {SearchSelect} from "@/components/ui/search-select";
import styles from "./workspace-audio-inspector.module.css";

type Parameters = {middle_ceiling_hz:number;ceiling_step:number;steps_each_side:number;time_step_s:number;window_s:number;preemphasis_hz:number};
export type FormantMethod = {parameters:Parameters;version:string;praat_version:string;sample_rate:number;max_formants:number};
type Point = {time:number;value:number|null};
type Candidate = {index:number;ceiling_hz:number;point_frequency:(number|null)[];point_bandwidth:(number|null)[];tracks:Point[][]};
type PathFinderParameters = {frequency_bandwidth_weight:number;frequency_change_weight:number;stress_weight:number;ceiling_change_weight:number;intensity_modulation_db:number;stress_window_seconds:number;coefficients_by_track:number[];power:number};
type SaveAction = "generate"|"apply"|"auto";
type Response = {available:boolean;package_id:string;revision_label:string;method?:FormantMethod;pathfinder?:{praat_version:string;parameters:PathFinderParameters}|null;pathfinder_defaults:PathFinderParameters;changed_frame_count:number;groups:Candidate[];frame:{index:number;time:number;selected:number};total_frames:number;range_frame_count:number;domain:[number,number];selected_tracks:Point[][];versions:{package_id:string;label:string}[];baseline_package_id?:string};
const defaults:Parameters={middle_ceiling_hz:5000,ceiling_step:.05,steps_each_side:2,time_step_s:.005,window_s:.025,preemphasis_hz:50};
const num=(v:number|null|undefined,d=3)=>v==null?"—":Number(v.toFixed(d)).toString();
const colors=["#347a67","#a46336","#715799","#337f9e"];

export function FormantCandidates({packageId,sampleId,point,view,selection,onLocate,onVersion}:{packageId:string;sampleId:string;point:number|null;view:[number,number]|null;selection:{start:number;end:number}|null;onLocate:(time:number)=>void;onVersion?:(id:string)=>void}) {
  const openKey=`statforge:formant-open:${packageId}:${sampleId}`;
  const [open,setOpen]=useState(()=>typeof window!=="undefined"&&localStorage.getItem(openKey)==="1");
  const [parameters,setParameters]=useState(defaults),[candidate,setCandidate]=useState<number|null>(null),[busyAction,setBusyAction]=useState<SaveAction|null>(null),[error,setError]=useState("");
  const busy=busyAction!==null;
  const [frameIndex,setFrameIndex]=useState<number|null>(null),[frameInput,setFrameInput]=useState("");
  const client=useQueryClient();
  useEffect(()=>{setFrameIndex(null);},[point]);
  const range=view|| (selection?[selection.start,selection.end] as [number,number]:null);
  const result=useQuery({queryKey:["formant-candidates",packageId,sampleId,point,frameIndex,range],enabled:open,
    queryFn:({signal})=>apiFetch<Response>(`/api/v1/datasets/${packageId}/formant-candidates`,{method:"POST",signal,body:JSON.stringify({sample_id:sampleId,...(frameIndex!=null?{frame_index:frameIndex}:point!=null?{point}:{}),...(range?{start:range[0],end:range[1]}:{})})})});
  const data=result.data;
  const loadedParameters=useRef<string|null>(null), actualFrame=data?.frame?.index;
  useEffect(()=>{if(data?.method&&loadedParameters.current!==data.package_id){setParameters(data.method.parameters);loadedParameters.current=data.package_id;}},[data?.package_id,data?.method]);
  useEffect(()=>{if(actualFrame!=null)setFrameInput(String(actualFrame));},[actualFrame]);
  function version(id:string){localStorage.setItem(`statforge:formant-open:${id}:${sampleId}`,"1");onVersion?.(id);}
  async function save(action:SaveAction) {
    setBusyAction(action);setError("");
    try {
      const input=action==="generate"?{parameters}:action==="apply"?{...selection,candidate_index:candidate??data?.frame.selected}:{};
      const saved=await apiFetch<{package_id:string}>(`/api/v1/datasets/${packageId}/formant-revisions`,{method:"POST",body:JSON.stringify({sample_id:sampleId,action,...input})});
      await Promise.all([client.invalidateQueries({queryKey:["datasets"]}),client.invalidateQueries({queryKey:["media-samples"]}),client.invalidateQueries({queryKey:["workspace-files"]})]);
      version(saved.package_id);
    }catch(reason){setError(reason instanceof Error?reason.message:"共振峰处理失败，请重新打开当前结果。");}finally{setBusyAction(null);}
  }
  const chosen=data?.groups?.find(g=>g.index===(candidate??data?.frame?.selected));
  const bounds=range||data?.domain||[0,1];
  const ceiling=data?.groups?.length?Math.max(...data.groups.map(g=>g.ceiling_hz)):5000;
  const pathfinder=data?.pathfinder?.parameters??data?.pathfinder_defaults;
  function path(points:Point[]){let d="",connected=false;for(const p of points){if(p.value==null){connected=false;continue;}const x=48+(p.time-bounds[0])/(bounds[1]-bounds[0]||1)*930,y=180-p.value/ceiling*165;d+=(connected?"L":"M")+x.toFixed(3)+" "+y.toFixed(3)+" ";connected=true;}return d;}
  return <details className={styles.pitchCandidates} open={open} onToggle={e=>{setOpen(e.currentTarget.open);localStorage.setItem(openKey,e.currentTarget.open?"1":"0");}}><summary>比较共振峰候选</summary>
    {open&&result.isPending&&<p role="status">正在读取候选…</p>}{result.isError&&<p role="alert">{result.error.message}</p>}
    {data&&<>
      {data.available&&chosen&&<>
        <div className={styles.pitchActions}><strong>{data.revision_label}</strong>{data.pathfinder&&<span>较来源版本改选 {data.changed_frame_count} / {data.total_frames} 帧</span>}<label>查看版本 <SearchSelect aria-label="共振峰结果版本" value={packageId} disabled={busy} onChange={e=>version(e.target.value)}>{data.versions.map(v=><option key={v.package_id} value={v.package_id}>{v.label}</option>)}</SearchSelect></label>{data.baseline_package_id&&<button type="button" disabled={busy} onClick={()=>version(data.baseline_package_id!)}>候选生成前的结果</button>}</div>
        <div className={styles.pitchActions}><label>比较上限 <SearchSelect aria-label="共振峰候选上限" value={String(chosen.index)} disabled={busy} onChange={e=>setCandidate(Number(e.target.value))}>{data.groups.map(g=><option key={g.index} value={g.index}>{num(g.ceiling_hz,6)} Hz</option>)}</SearchSelect></label><span>{data.groups.length} 组 · {data.total_frames} 帧</span></div>
        <svg className={styles.formantComparison} viewBox="0 0 1000 205" role="img" aria-label={`共振峰候选 ${num(chosen.ceiling_hz)} Hz 与当前轨迹比较`}>
          {[0,.5,1].map(f=><g key={f}><line x1={48} x2={978} y1={180-f*165} y2={180-f*165} className={styles.sliceGrid}/><text x={3} y={184-f*165}>{Math.round(ceiling*f)}</text></g>)}
          {data.selected_tracks.map((track,i)=><path key={`current-${i}`} d={path(track)} fill="none" stroke="var(--rw-muted)" strokeWidth={1.5} strokeDasharray="5 4"/>)}
          {chosen.tracks.map((track,i)=><path key={i} d={path(track)} fill="none" stroke={colors[i]} strokeWidth={2}/>)}
          <text x={48} y={202}>{num(bounds[0],4)} 秒</text><text x={978} y={202} textAnchor="end">{num(bounds[1],4)} 秒</text>
        </svg>
        <p className={styles.formantLegend}>{colors.map((color,i)=><span key={i} style={{color}}>F{i+1}</span>)}<span>Hz · 彩色为比较候选，虚线为当前保存轨迹</span></p>
        <div className={styles.pitchActions}><label>帧 <input type="number" aria-label="共振峰帧" min={1} max={data.total_frames} value={frameInput} onChange={e=>setFrameInput(e.target.value)}/> / {data.total_frames}</label><button type="button" disabled={!Number.isInteger(Number(frameInput))||Number(frameInput)<1||Number(frameInput)>data.total_frames} onClick={()=>setFrameIndex(Number(frameInput))}>查看共振峰帧</button><button type="button" onClick={()=>onLocate(data.frame.time)}>定位 {num(data.frame.time,6)} 秒</button></div>
        <div className={styles.statistics}><table><thead><tr><th>上限（Hz）</th>{[1,2,3,4].map(i=><th key={i}>F{i} / B{i}（Hz）</th>)}</tr></thead><tbody>{data.groups.map(g=><tr key={g.index} aria-selected={g.index===chosen.index}><th><label><input type="radio" name={`formant-${packageId}`} checked={g.index===chosen.index} onChange={()=>setCandidate(g.index)}/>{num(g.ceiling_hz)}{g.index===data.frame.selected?" · 当前帧":""}</label></th>{g.point_frequency.map((v,i)=><td key={i}>{num(v)} / {num(g.point_bandwidth[i])}</td>)}</tr>)}</tbody></table></div>
        <div className={styles.pitchActions}><button type="button" disabled={busy||!selection||!onVersion} onClick={()=>void save("apply")}>{busyAction==="apply"?"正在保存…":"将候选应用到选段并另存"}</button><span>{selection?`${num(selection.start,6)}–${num(selection.end,6)} 秒`:"在上方波形选择时间范围"}</span></div>
        <div className={styles.pitchActions}>
          <button type="button" disabled={busy||!onVersion||data.groups.length<2} onClick={()=>void save("auto")}>{busyAction==="auto"?"正在自动选路…":"整段自动选路并另存"}</button>
          <span>{data.groups.length<2?"需要至少两组候选":"在已保存的候选中逐帧选路"}</span>
        </div>
        {pathfinder&&<details className={styles.pitchParameters}><summary>自动选路参数</summary><p className={styles.note}>Praat {data.pathfinder?.praat_version??data.method?.praat_version} · Path finder · 频率/带宽、频率变化、拟合误差、上限变化权重 {pathfinder.frequency_bandwidth_weight} / {pathfinder.frequency_change_weight} / {pathfinder.stress_weight} / {pathfinder.ceiling_change_weight} · 强度调制 {pathfinder.intensity_modulation_db} dB · 建模窗 {num(pathfinder.stress_window_seconds*1000)} ms · 各轨系数 {pathfinder.coefficients_by_track.join(" / ")} · 幂 {pathfinder.power}</p></details>}
      </>}
      <div className={styles.pitchParameterFields}>{([{key:"middle_ceiling_hz",label:"中间上限 Hz"},{key:"ceiling_step",label:"上限步距"},{key:"steps_each_side",label:"每侧步数"}] as const).map(({key,label})=><label key={key}>{label}<input type="number" step={key==="steps_each_side"?1:"any"} value={Number.isFinite(parameters[key])?parameters[key]:""} disabled={busy} onChange={e=>setParameters(old=>({...old,[key]:e.target.valueAsNumber}))}/></label>)}</div>
      <details className={styles.pitchParameters}><summary>测量设置</summary><div className={styles.pitchParameterFields}>{([{key:"time_step_s",label:"帧步长 ms",scale:1000},{key:"window_s",label:"窗长 ms",scale:1000},{key:"preemphasis_hz",label:"预加重 Hz",scale:1}] as const).map(({key,label,scale})=><label key={key}>{label}<input type="number" step="any" value={Number.isFinite(parameters[key])?parameters[key]*scale:""} disabled={busy} onChange={e=>setParameters(old=>({...old,[key]:e.target.valueAsNumber/scale}))}/></label>)}</div></details>
      {data.method&&<p className={styles.note}>Praat {data.method.praat_version} · Parselmouth {data.method.version} · Burg · {data.method.sample_rate} Hz 采样 · 最多 5 个共振峰 · 窗 {data.method.parameters.window_s*1000} ms · 步长 {data.method.parameters.time_step_s*1000} ms · 预加重 {data.method.parameters.preemphasis_hz} Hz</p>}
      <button type="button" disabled={busy||!onVersion||Object.values(parameters).some(v=>!Number.isFinite(v))} onClick={()=>void save("generate")}>{busyAction==="generate"?"正在处理…":data.available?"按参数生成另一组候选":"生成共振峰候选"}</button>
    </>}
    {error&&<p role="alert">{error}</p>}
  </details>;
}
