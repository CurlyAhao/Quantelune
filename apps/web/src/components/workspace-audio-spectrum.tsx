"use client";
import {useEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiBlob,apiFetch} from "@/lib/api-client";
import {saveGeneratedFile} from "@/lib/api-download";
import {audioTime} from "@/lib/audio-time";
import {SearchSelect} from "./ui/search-select";
import styles from "./workspace-audio-inspector.module.css";

type Band={lower_hz:number;upper_hz:number;energy:number;mean_density:number};
type Method={original_start:number;original_end:number;sample_count:number;sample_rate_hz:number;fft_size:number;bin_spacing_hz:number;actual_window_ms:number;input_channels:number;praat:string;parselmouth:string;query?:Band};
type Run={id:string;title:string;status:string;started_at:string;error_message?:string;parameters:{mode:string;window_ms:number};method:Method;frequencies?:number[];power?:number[];amplitude?:number[]};
type History={clock:{offset:number};runs:Run[]};
const precise=(n:number)=>Number(n.toPrecision(7)).toString();

export function AudioSpectrum({datasetId,cursor,selection,measurement,onLocate,originalInput=false}:{originalInput?:boolean;datasetId:string;cursor:number|null;selection?:{start:number;end:number};measurement?:{packageId:string;sampleId:string};onLocate:(a:number,b?:number)=>void}) {
  const client=useQueryClient(),active=useRef<AbortController|null>(null);
  const [mode,setMode]=useState(selection?"selection":"cursor"),[windowMs,setWindowMs]=useState("25");
  const [open,setOpen]=useState(false),[run,setRun]=useState<Run|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [lower,setLower]=useState("0"),[upper,setUpper]=useState("8000"),[frequency,setFrequency]=useState(""),[saving,setSaving]=useState(false);
  const [band,setBand]=useState<Band|null>(null);
  const base=`/api/v1/datasets/${datasetId}/audio-spectra`;
  const suffix=measurement?`?package_id=${measurement.packageId}&sample_id=${encodeURIComponent(measurement.sampleId)}`:"";
  const key=["audio-spectra",datasetId,measurement?.packageId,measurement?.sampleId];
  const history=useQuery({queryKey:key,queryFn:({signal})=>apiFetch<History>(base+suffix,{signal}),enabled:open});
  useEffect(()=>()=>active.current?.abort(),[]);
  async function load(id:string){
    active.current?.abort();const control=new AbortController();active.current=control;setBusy(true);setError("");
    try{const next=await apiFetch<Run>(base+"/"+id,{signal:control.signal});if(!control.signal.aborted){setRun(next);setFrequency("");setBand(next.method.query??null);setMode(next.parameters.mode);setWindowMs(String(next.parameters.window_ms));if(next.method.query){setLower(String(next.method.query.lower_hz));setUpper(String(next.method.query.upper_hz));}if(next.status==="completed")onLocate(next.method.original_start-(history.data?.clock.offset??0),next.method.original_end-(history.data?.clock.offset??0));}}
    catch(e){if(!control.signal.aborted)setError(e instanceof Error?e.message:"无法读取频谱。");}
    finally{if(!control.signal.aborted)setBusy(false);}
  }
  async function calculate(){
    active.current?.abort();const control=new AbortController();active.current=control;setBusy(true);setError("");
    try{
      const next=await apiFetch<Run>(base,{method:"POST",signal:control.signal,body:JSON.stringify({mode,point:cursor,start:selection?.start??null,end:selection?.end??null,window_ms:Number(windowMs),package_id:measurement?.packageId??null,sample_id:measurement?.sampleId??null})});
      if(!control.signal.aborted){setRun(next);setFrequency("");setBand(null);await client.invalidateQueries({queryKey:key});}
    }catch(e){if(!control.signal.aborted)setError(e instanceof Error?e.message:"频谱计算失败。");}
    finally{if(!control.signal.aborted)setBusy(false);}
  }
  async function download(){
    if(!run)return;setSaving(true);setError("");
    try{await saveGeneratedFile(await apiBlob(base+"/"+run.id+"/export"),`频谱-${run.method.sample_count}采样点.zip`,new AbortController().signal);}
    catch(e){setError(e instanceof Error?e.message:"无法导出频谱。");}finally{setSaving(false);}
  }
  async function queryBand(save=false){
    if(!run)return;active.current?.abort();const control=new AbortController();active.current=control;setBusy(true);setError("");
    try{const result=await apiFetch<{query:Band;run?:Run}>(base+"/"+run.id+"/query",{method:"POST",signal:control.signal,body:JSON.stringify({lower_hz:Number(lower),upper_hz:Number(upper),save})});
      if(!control.signal.aborted){setBand(result.query);if(result.run){setRun(result.run);await client.invalidateQueries({queryKey:key});}}}
    catch(e){if(!control.signal.aborted)setError(e instanceof Error?e.message:"频带查询失败。");}
    finally{if(!control.signal.aborted)setBusy(false);}
  }
  const f=run?.frequencies??[],p=run?.power??[],nyquist=f.at(-1)??24000;
  const lo=Math.max(0,Number(lower)),hi=Math.min(nyquist,Number(upper)),span=hi-lo;
  const indexes=f.flatMap((value,i)=>value>=lo&&value<=hi?[i]:[]);
  let peak=indexes[0]??0;for(const i of indexes)if(p[i]>p[peak])peak=i;
  const top=p[peak]||1;
  const stride=Math.max(1,Math.ceil(indexes.length/3500));const drawn:number[]=[];
  for(let n=0;n<indexes.length;n+=stride){let best=indexes[n];for(let j=n+1;j<Math.min(n+stride,indexes.length);j++)if(p[indexes[j]]>p[best])best=indexes[j];drawn.push(best);}
  const path=span>0?drawn.map((i,n)=>`${n?"L":"M"}${55+(f[i]-lo)/span*930} ${210-p[i]/top*185}`).join(" "):"";
  const selected=frequency.trim()&&f.length?Math.max(0,Math.min(f.length-1,Math.round(Number(frequency)/(run?.method.bin_spacing_hz||1)))):null;
  const offset=history.data?.clock.offset??0;
  const saved=history.data?.runs.filter(item=>item.parameters.mode!=="spectrogram")??[];
  const runLabel=(item:Run)=>item.status==="completed"?`${item.method.query?item.title:item.title.split(" · ")[0]} · ${precise(item.method.original_start-offset)}–${precise(item.method.original_end-offset)} 秒`:item.title;
  return <div className={styles.spectralSlice}>
    <button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>{open?"收起频谱":originalInput?"查看原音频谱":"查看频谱"}</button>
    {open&&<>
      {originalInput&&<p>声源：原录音（未滤波）。</p>}
      <div className={styles.sliceActions}>
        <label>取样<SearchSelect aria-label="频谱取样" value={mode} onChange={e=>setMode(e.target.value)}><option value="cursor">光标</option><option value="selection">选段</option></SearchSelect></label>
        {mode==="cursor"?<><label>光标（秒）<input aria-label="频谱光标（秒）" type="number" step="0.001" value={cursor==null?"":audioTime(cursor)} onChange={e=>{if(e.target.value.trim()&&Number.isFinite(Number(e.target.value)))onLocate(Number(e.target.value));}}/></label><label>窗长（ms）<input type="number" min="0.01" step="1" value={windowMs} onChange={e=>setWindowMs(e.target.value)}/></label></>:<span>{selection?`${audioTime(selection.start)}–${audioTime(selection.end)} 秒 · 整段 Hann 窗`:"请在波形上选段，或填写起止时间。"}</span>}
        <button type="button" disabled={busy||!Number(windowMs)||(mode==="cursor"?cursor==null:!selection)} onClick={()=>void calculate()}>{busy?"正在读取实际波形…":"计算并保存"}</button>
      </div>
      {!!saved.length&&<label className={styles.sliceHistory}>已保存频谱<SearchSelect aria-label="已保存频谱" value={run?.id||""} onChange={e=>void load(e.target.value)}><option value="" disabled>选择一次测量</option>{saved.map(item=><option key={item.id} value={item.id}>{runLabel(item)} · {new Date(item.started_at).toLocaleString()} · {item.status==="completed"?"已保存":item.status==="failed"?"失败":"处理中"}</option>)}</SearchSelect></label>}
      {(error||history.isError)&&<p role="alert">{error||history.error?.message}</p>}
      {run?.status==="failed"&&<p role="alert">处理失败：{run.error_message}</p>}
      {run?.status==="completed"&&<>
        <div className={styles.sliceTitle}><strong>频谱 · {precise(run.method.original_start-offset)}–{precise(run.method.original_end-offset)} 秒</strong><button type="button" onClick={()=>onLocate(run.method.original_start-offset,run.method.original_end-offset)}>定位测量选段</button><button type="button" disabled={saving} onClick={()=>void download()}>{saving?"正在导出…":"导出频谱与 WAV"}</button></div>
        <div className={styles.sliceActions}><label>频带起点（Hz）<input type="number" min="0" value={lower} onChange={e=>setLower(e.target.value)}/></label><label>频带终点（Hz）<input type="number" min="1" value={upper} onChange={e=>setUpper(e.target.value)}/></label><button type="button" disabled={busy||!(span>0)} onClick={()=>void queryBand()}>查询频带</button><button type="button" disabled={busy||!(span>0)} onClick={()=>void queryBand(true)}>保存频带读数</button><button type="button" onClick={()=>{setLower("0");setUpper(String(nyquist));}}>全频段</button></div>
        <div className={styles.slicePointRow}><label>频率点读（Hz）<input type="number" min="0" max={nyquist} value={frequency} onChange={e=>setFrequency(e.target.value)}/></label></div>
        {band&&<dl className={styles.bandReading}><div><dt>频带</dt><dd>{precise(band.lower_hz)}–{precise(band.upper_hz)} Hz</dd></div><div><dt>频带能量</dt><dd>{band.energy.toExponential(10)} <span>PCM²·s</span></dd></div><div><dt>平均能量密度</dt><dd>{band.mean_density.toExponential(10)} <span>PCM²·s/Hz</span></dd></div></dl>}
        {span>0&&indexes.length?<><svg className={styles.slicePlot} viewBox="0 0 1000 250" role="img" aria-label="频谱，点击读取最近频格" onClick={e=>{const svg=e.currentTarget,transform=svg.getScreenCTM();if(!transform)return;const point=svg.createSVGPoint();point.x=e.clientX;point.y=e.clientY;const x=point.matrixTransform(transform.inverse()).x,hz=Math.max(lo,Math.min(hi,lo+(x-55)/930*span)),index=Math.max(0,Math.min(f.length-1,Math.round(hz/run.method.bin_spacing_hz)));setFrequency(String(f[index]));}}>
          {[0,.5,1].map(t=><g key={t}><line x1="55" y1={210-185*t} x2="985" y2={210-185*t} className={styles.sliceGrid}/><text x="49" y={214-185*t} textAnchor="end">{(top*t).toExponential(1)}</text></g>)}
          <path d={path} className={styles.line}/>{[0,.25,.5,.75,1].map(t=><text key={t} x={55+930*t} y="235" textAnchor={t===0?"start":t===1?"end":"middle"}>{precise(lo+span*t)} Hz</text>)}
          {selected!=null&&f[selected]>=lo&&f[selected]<=hi&&<line x1={55+(f[selected]-lo)/span*930} x2={55+(f[selected]-lo)/span*930} y1="20" y2="210" className={styles.cursor}/>}</svg>
          <p className={styles.note}>频谱模平方（PCM²·s²） · 当前频带最强频格 {precise(f[peak])} Hz</p>
          {selected!=null&&<p className={styles.sliceReading}>点读 {precise(f[selected])} Hz · 幅值 {precise(run.amplitude![selected])} PCM·s · 模平方 {precise(p[selected])} PCM²·s²</p>}
        </>:<p>频带没有有效频格，请调整显示范围。</p>}
        <details className={styles.spectrumMethod}><summary>测量方法</summary><p>Hann 窗 · 实际 {precise(run.method.actual_window_ms)} ms · {run.method.sample_count} 个采样点 · {run.method.sample_rate_hz} Hz · {run.method.input_channels>1?"声道混合为单声道":"单声道"}</p><p>FFT {run.method.fft_size} 点 · 频率间隔 {precise(run.method.bin_spacing_hz)} Hz · Praat {run.method.praat}</p><p>幅值为窗后波形 FFT 除以采样率的模，纵轴为其平方。使用归一化 PCM，未换算为声压或 dB。导出保留全部频格、原时间映射、取段 WAV 和加窗 WAV。</p></details>
      </>}
    </>}
  </div>;
}
