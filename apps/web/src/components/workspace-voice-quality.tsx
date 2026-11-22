"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiBlob,apiFetch} from "@/lib/api-client";
import {saveGeneratedFile} from "@/lib/api-download";
import {audioTime} from "@/lib/audio-time";
import {SearchSelect} from "./ui/search-select";
import {useWorkspaceAnnotations} from "./workspace-annotations";
import styles from "./workspace-audio-inspector.module.css";

type Parameters={pitch_floor:number;pitch_ceiling:number;time_step:number;period_floor:number;period_ceiling:number;maximum_period_factor:number;maximum_amplitude_factor:number};
type Method={original_start:number;original_end:number;sample_rate_hz:number;sample_count:number;praat:string;parselmouth:string;pitch:{floor_hz:number;ceiling_hz:number;time_step:number};period:{floor_seconds:number;ceiling_seconds:number;maximum_period_factor:number;maximum_amplitude_factor:number}};
type Run={id:string;title:string;status:string;started_at:string;error_message?:string;parameters:Parameters;method:Method;summary:Record<string,number|null>;artifact_ids:Record<string,string>;pulse_times_original?:number[];hnr_times_original?:number[];hnr_db?:(number|null)[]};
type History={clock:{offset:number};runs:Run[]};
const defaults:Parameters={pitch_floor:100,pitch_ceiling:650,time_step:.01,period_floor:.0001,period_ceiling:.02,maximum_period_factor:1.3,maximum_amplitude_factor:1.6};
const settings:{key:keyof Parameters;label:string;scale:number;step:string}[]=[
  {key:"pitch_floor",label:"基频下限（Hz）",scale:1,step:"1"},{key:"pitch_ceiling",label:"基频上限（Hz）",scale:1,step:"1"},
  {key:"time_step",label:"帧步长（ms）",scale:1000,step:"1"},{key:"period_floor",label:"周期下限（ms）",scale:1000,step:"0.1"},
  {key:"period_ceiling",label:"周期上限（ms）",scale:1000,step:"1"},{key:"maximum_period_factor",label:"最大周期因子",scale:1,step:"0.1"},{key:"maximum_amplitude_factor",label:"最大振幅因子",scale:1,step:"0.1"}];
const extraMetrics=[
  ["Jitter local absolute","jitter_local_absolute_seconds","μs",1e6],["Jitter RAP","jitter_rap_ratio","%",100],["Jitter PPQ5","jitter_ppq5_ratio","%",100],["Jitter DDP","jitter_ddp_ratio","%",100],
  ["Shimmer local dB","shimmer_local_db","dB",1],["Shimmer APQ3","shimmer_apq3_ratio","%",100],["Shimmer APQ5","shimmer_apq5_ratio","%",100],["Shimmer APQ11","shimmer_apq11_ratio","%",100],["Shimmer DDA","shimmer_dda_ratio","%",100]] as const;
const display=(value:number|null|undefined,scale=1)=>value==null?"—":Number((value*scale).toPrecision(7)).toString();

export function VoiceQuality({datasetId,selection,measurement,onLocate,onPulses,originalInput=false}:{originalInput?:boolean;datasetId:string;selection?:{start:number;end:number};measurement?:{packageId:string;sampleId:string};onLocate:(a:number,b?:number)=>void;onPulses:(points:number[])=>void}){
  const client=useQueryClient(),navigation=useWorkspaceAnnotations(),active=useRef<AbortController|null>(null),initialized=useRef(false);
  const [open,setOpen]=useState(false),[run,setRun]=useState<Run|null>(null),[busy,setBusy]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState("");
  const [params,setParams]=useState(defaults),[showPulses,setShowPulses]=useState(false);
  const base=`/api/v1/datasets/${datasetId}/voice-quality`,key=["voice-quality",datasetId,measurement?.packageId,measurement?.sampleId];
  const suffix=measurement?`?package_id=${measurement.packageId}&sample_id=${encodeURIComponent(measurement.sampleId)}`:"";
  const history=useQuery({queryKey:key,queryFn:({signal})=>apiFetch<History>(base+suffix,{signal}),enabled:open});
  const offset=history.data?.clock.offset??0;
  useEffect(()=>{onPulses(open&&showPulses?(run?.pulse_times_original??[]).map(t=>t-offset):[]);return()=>onPulses([]);},[open,showPulses,run,offset,onPulses]);
  useEffect(()=>()=>active.current?.abort(),[]);
  const load=useCallback(async(id:string)=>{
    active.current?.abort();const control=new AbortController();active.current=control;setBusy(true);setError("");
    try{const next=await apiFetch<Run>(base+"/"+id,{signal:control.signal});if(!control.signal.aborted){setRun(next);setParams({...defaults,...next.parameters});}}
    catch(e){if(!control.signal.aborted)setError(e instanceof Error?e.message:"无法读取声音质量结果。");}
    finally{if(!control.signal.aborted)setBusy(false);}
  },[base]);
  useEffect(()=>{if(history.data&&!initialized.current){initialized.current=true;const latest=history.data.runs[0];if(latest&&!run)void load(latest.id);}},[history.data,load,run]);
  async function calculate(){
    if(!selection)return;active.current?.abort();const control=new AbortController();active.current=control;setBusy(true);setError("");
    try{const next=await apiFetch<Run>(base,{method:"POST",signal:control.signal,body:JSON.stringify({...params,...selection,package_id:measurement?.packageId??null,sample_id:measurement?.sampleId??null})});
      if(!control.signal.aborted){setRun(next);await client.invalidateQueries({queryKey:key});await client.invalidateQueries({queryKey:["datasets"]});}}
    catch(e){if(!control.signal.aborted)setError(e instanceof Error?e.message:"声音质量计算失败。");}
    finally{if(!control.signal.aborted)setBusy(false);}
  }
  async function download(){if(!run)return;setSaving(true);setError("");try{await saveGeneratedFile(await apiBlob(base+"/"+run.id+"/export"),`声音质量-${audioTime(run.method.original_start)}-${audioTime(run.method.original_end)}.zip`,new AbortController().signal);}catch(e){setError(e instanceof Error?e.message:"导出失败。");}finally{setSaving(false);}}
  const label=(item:Run)=>item.status==="completed"?`声音质量 · ${audioTime(item.method.original_start-offset)}–${audioTime(item.method.original_end-offset)} 秒`:item.title;
  const s=run?.summary??{},values=run?.hnr_db??[],times=run?.hnr_times_original??[],valid=values.filter((x):x is number=>x!=null),lo=valid.length?Math.min(...valid):0,hi=valid.length?Math.max(...valid):1;
  let path="",joined=false;const first=run?.method.original_start??0,last=run?.method.original_end??1;
  for(let i=0;i<values.length;i++){const v=values[i];if(v==null){joined=false;continue;}path+=`${joined?"L":"M"}${(times[i]-first)/(last-first||1)*1000} ${85-(v-lo)/(hi-lo||1)*70} `;joined=true;}
  return <div className={styles.spectralSlice}>
    <button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>{open?"收起声音质量":originalInput?"原音声音质量":"声音质量"}</button>
    {open&&<>
      {originalInput&&<p>声源：原录音（未滤波）。</p>}
      <div className={styles.sliceActions}><span>{selection?`当前选段 ${audioTime(selection.start)}–${audioTime(selection.end)} 秒`:"请在波形上选段，或填写起止时间。"}</span><button type="button" disabled={busy||!selection} onClick={()=>void calculate()}>{busy?"正在计算声音质量…":"计算并保存"}</button></div>
      <details className={styles.spectrumMethod}><summary>测量设置</summary><div className={styles.sliceActions}>{settings.map(({key:field,label,scale,step})=><label key={field}>{label}<input type="number" aria-label={label} step={step} value={Number((params[field]*scale).toPrecision(10))} onChange={e=>setParams({...params,[field]:Number(e.target.value)/scale})}/></label>)}</div></details>
      {!!history.data?.runs.length&&<label className={styles.sliceHistory}>已保存测量<SearchSelect style={{flex:"1 1 360px",minWidth:0,maxWidth:660}} aria-label="已保存声音质量" value={run?.id||""} onChange={e=>void load(e.target.value)}><option value="" disabled>选择一次测量</option>{history.data.runs.map(item=><option key={item.id} value={item.id}>{label(item)} · {new Date(item.started_at).toLocaleString()} · {item.status==="completed"?"已保存":item.status==="failed"?"失败":"处理中"}</option>)}</SearchSelect></label>}
      {(error||history.isError)&&<p role="alert">{error||history.error?.message}</p>}{run?.status==="failed"&&<p role="alert">处理失败：{run.error_message}</p>}
      {run?.status==="completed"&&<>
        <div className={styles.sliceTitle}><strong>{label(run)}</strong><button type="button" onClick={()=>onLocate(first-offset,last-offset)}>定位测量选段</button>{navigation?.openData&&<button type="button" onClick={()=>navigation.openData?.(run.artifact_ids["summary.csv"])}>查看摘要表</button>}<button type="button" disabled={saving} onClick={()=>void download()}>{saving?"正在导出…":"导出测量与 WAV"}</button></div>
        <div className={styles.statistics}><table><thead><tr><th>实际周期点</th><th>Jitter local（%）</th><th>Shimmer local（%）</th><th>HNR（dB）</th><th>CPPS（dB）</th></tr></thead><tbody><tr><th>{display(s.pulse_count)}</th><td>{display(s.jitter_local_ratio,100)}</td><td>{display(s.shimmer_local_ratio,100)}</td><td>{display(s.mean_hnr_db)}</td><td>{display(s.cpps_db)}</td></tr></tbody></table></div>
        {s.pulse_count===0&&<p className={styles.note}>此选段没有检测到周期点，Jitter 和 Shimmer 未定义。</p>}
        <div className={styles.controls}><label><input type="checkbox" checked={showPulses} onChange={e=>setShowPulses(e.target.checked)}/>在波形上显示周期点</label><span className={styles.note}>有效周期 {display(s.valid_period_count)} · HNR 有效帧 {display(s.hnr_valid_frames)}/{display(s.hnr_total_frames)}</span></div>
        {!!values.length&&<details className={styles.spectrumMethod}><summary>HNR 曲线</summary><div className={styles.curve}>{valid.length?<><svg viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label="HNR 时间曲线"><path d={path} className={styles.line}/></svg><div className={styles.axis}><span>{audioTime(first-offset)} 秒</span><span>{audioTime(last-offset)} 秒</span></div><p>纵轴 {display(lo)} 至 {display(hi)} dB</p></>:<p>此选段没有有效 HNR 帧。</p>}</div></details>}
        <details className={styles.spectrumMethod}><summary>其他 Jitter / Shimmer 指标</summary><div className={styles.statistics}><table><thead><tr><th>指标</th><th>值</th><th>单位</th></tr></thead><tbody>{extraMetrics.map(([name,key,unit,scale])=><tr key={key}><th>{name}</th><td>{display(s[key],scale)}</td><td>{unit}</td></tr>)}</tbody></table></div></details>
        <details className={styles.spectrumMethod}><summary>本次方法与参数</summary><p>Praat {run.method.praat} · Parselmouth {run.method.parselmouth} · {run.method.sample_rate_hz} Hz · {run.method.sample_count} 个采样点，矩形取段。</p><p>原始互相关 Pitch → 波形与 Pitch 联合计算周期点。基频 {run.method.pitch.floor_hz}–{run.method.pitch.ceiling_hz} Hz，步长 {display(run.method.pitch.time_step,1000)} ms。</p><p>有效周期 {display(run.method.period.floor_seconds,1000)}–{display(run.method.period.ceiling_seconds,1000)} ms，最大周期因子 {run.method.period.maximum_period_factor}，振幅因子 {run.method.period.maximum_amplitude_factor}。这组固定周期界限不等同于 Voice report 的自动周期界限。</p><p>HNR 使用独立互相关 Harmonicity 的有效帧均值；静音阈值 0.1、每窗 1 周期。CPPS：2 ms 步长，最高频率 5000 Hz，50 Hz 预加重；10 ms 时间、1 ms 倒频率平滑，抛物线峰值插值与 Robust 直线趋势拟合（1–50 ms）。完整参数随导出保存。</p></details>
      </>}
    </>}
  </div>;
}
