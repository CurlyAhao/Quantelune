"use client";

import {useEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiBlob,apiFetch} from "@/lib/api-client";
import {saveGeneratedFile} from "@/lib/api-download";
import {audioTime} from "@/lib/audio-time";
import {SearchSelect} from "./ui/search-select";
import styles from "./workspace-audio-inspector.module.css";

type Query={time:number;frequency:number;density:number;unit:string};
type Grid={xmin:number;xmax:number;nx:number;dx:number;x1:number;ymin:number;ymax:number;ny:number;dy:number;y1:number};
type Run={id:string;title:string;status:string;started_at:string;error_message?:string;
  parameters:{mode:string;window_ms:number;max_frequency_hz:number;time_step_ms:number;frequency_step_hz:number};
  method:{original_start:number;original_end:number;grid:Grid;window_ms:number;praat:string;query?:Query};
  times:number[];frequencies:number[];db:number[][];color_max_db:number};
type History={clock:{offset:number};runs:Run[]};
const number=(n:number)=>Number(n.toPrecision(10)).toString();

function Image({data,range,offset,dynamic}:{data:Run;range:[number,number];offset:number;dynamic:number}) {
  const [url,setUrl]=useState("");
  useEffect(()=>{
    const nx=data.times.length,ny=data.frequencies.length,canvas=document.createElement("canvas");
    canvas.width=nx;canvas.height=ny;const ctx=canvas.getContext("2d");if(!ctx)return;
    const pixels=ctx.createImageData(nx,ny),low=data.color_max_db-dynamic;
    for(let x=0;x<nx;x++)for(let y=0;y<ny;y++){
      const level=Math.max(0,Math.min(1,(data.db[x][y]-low)/dynamic)),p=((ny-1-y)*nx+x)*4;
      pixels.data[p]=Math.round(245-level*225);pixels.data[p+1]=Math.round(247-level*184);pixels.data[p+2]=Math.round(243-level*186);pixels.data[p+3]=255;
    }
    ctx.putImageData(pixels,0,0);setUrl(canvas.toDataURL());
  },[data,dynamic]);
  const step=data.times.length>1?data.times[1]-data.times[0]:data.method.grid.dx;
  return url?<image href={url} x={(data.times[0]-offset-step/2-range[0])/(range[1]-range[0])*1000} y={0}
    width={(data.times.at(-1)!-data.times[0]+step)/(range[1]-range[0])*1000} height={100} preserveAspectRatio="none"/>:null;
}

export function AudioSpectrogram({datasetId,measurement,selection,range,onLocate,onPoint,onSelect}:{datasetId:string;
  measurement?:{packageId:string;sampleId:string};selection?:{start:number;end:number};range:[number,number];
  onLocate:(a:number,b?:number)=>void;onPoint:(t:number)=>void;onSelect:(a:number,b:number)=>void}) {
  const client=useQueryClient(),active=useRef<AbortController|null>(null),drag=useRef<{x:number;t:number}|null>(null);
  const [windowMs,setWindowMs]=useState("5"),[maximum,setMaximum]=useState("5000"),[step,setStep]=useState("2"),[frequencyStep,setFrequencyStep]=useState("20");
  const [dynamic,setDynamic]=useState("70"),[time,setTime]=useState(""),[frequency,setFrequency]=useState("");
  const [run,setRun]=useState<Run|null>(null),[reading,setReading]=useState<Query|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const base=`/api/v1/datasets/${datasetId}/audio-spectra`,key=["audio-spectra",datasetId,measurement?.packageId,measurement?.sampleId];
  const suffix=measurement?`?package_id=${measurement.packageId}&sample_id=${encodeURIComponent(measurement.sampleId)}`:"";
  const history=useQuery({queryKey:key,queryFn:({signal})=>apiFetch<History>(base+suffix,{signal})});
  const offset=history.data?.clock.offset??0;
  useEffect(()=>()=>active.current?.abort(),[]);
  function accept(next:Run,restore=false){
    setRun(next);setReading(next.method.query??null);
    if(next.method.query){setTime(number(next.method.query.time-offset));setFrequency(number(next.method.query.frequency));}
    else{setTime("");setFrequency("");}
    if(restore&&next.status==="completed"){
      setWindowMs(String(next.parameters.window_ms));setMaximum(String(next.parameters.max_frequency_hz));
      setStep(String(next.parameters.time_step_ms));setFrequencyStep(String(next.parameters.frequency_step_hz));
      onLocate(next.method.original_start-offset,next.method.original_end-offset);
    }
  }
  async function load(id:string){
    active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError("");
    try{const next=await apiFetch<Run>(base+"/"+id,{signal:controller.signal});if(!controller.signal.aborted)accept(next,true);}
    catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:"无法读取声谱图。");}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  async function calculate(){
    if(!selection)return;active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError("");
    try{const next=await apiFetch<Run>(base,{method:"POST",signal:controller.signal,body:JSON.stringify({mode:"spectrogram",...selection,
      window_ms:Number(windowMs),max_frequency_hz:Number(maximum),time_step_ms:Number(step),frequency_step_hz:Number(frequencyStep),
      package_id:measurement?.packageId??null,sample_id:measurement?.sampleId??null})});
      if(!controller.signal.aborted){accept(next);await client.invalidateQueries({queryKey:key});}}
    catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:"声谱图计算失败。");}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  async function query(save=false,t=Number(time),f=Number(frequency)){
    if(!run)return;active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError("");
    try{const result=await apiFetch<{query:Query;run?:Run}>(base+"/"+run.id+"/query",{method:"POST",signal:controller.signal,
      body:JSON.stringify({time:t+offset,frequency:f,save})});
      if(!controller.signal.aborted){setReading(result.query);if(result.run){accept(result.run);await client.invalidateQueries({queryKey:key});}}}
    catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:"点读失败。");}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  async function download(){if(!run)return;setBusy(true);setError("");try{
    await saveGeneratedFile(await apiBlob(base+"/"+run.id+"/export"),"声谱测量.zip",new AbortController().signal);
  }catch(e){setError(e instanceof Error?e.message:"导出失败。");}finally{setBusy(false);}}
  const saved=history.data?.runs.filter(item=>item.parameters.mode==="spectrogram")??[];
  const tAt=(e:React.PointerEvent<SVGSVGElement>)=>{const box=e.currentTarget.getBoundingClientRect();return range[0]+(e.clientX-box.left)/box.width*(range[1]-range[0]);};
  const grid=run?.method.grid;
  return <div className={styles.spectralSlice}>
    <div className={styles.sliceTitle}><strong>声谱图</strong><span>{selection?`${audioTime(selection.start)}–${audioTime(selection.end)} 秒`:"请先选择分析时间段"}</span><button type="button" disabled={busy||!selection||![windowMs,maximum,step,frequencyStep].every(v=>Number(v)>0)} onClick={()=>void calculate()}>{busy?"正在读取…":"计算并保存声谱"}</button></div>
    <details className={styles.spectrumMethod}><summary>分析设置</summary><div className={styles.sliceActions}>
      <label>高斯窗长（ms）<input type="number" min="0.1" step="1" value={windowMs} onChange={e=>setWindowMs(e.target.value)}/></label>
      <label>最高频率（Hz）<input type="number" min="1" value={maximum} onChange={e=>setMaximum(e.target.value)}/></label>
      <label>请求时步（ms）<input type="number" min="0.01" step="0.1" value={step} onChange={e=>setStep(e.target.value)}/></label>
      <label>请求频步（Hz）<input type="number" min="0.01" value={frequencyStep} onChange={e=>setFrequencyStep(e.target.value)}/></label>
    </div></details>
    {!!saved.length&&<label className={styles.sliceHistory}>已保存声谱<SearchSelect aria-label="已保存声谱" value={run?.id??""} onChange={e=>void load(e.target.value)}><option value="" disabled>选择一次测量</option>{saved.map(item=><option key={item.id} value={item.id}>{item.title} · {new Date(item.started_at).toLocaleString()}</option>)}</SearchSelect></label>}
    {(error||history.isError)&&<p role="alert">{error||history.error?.message}</p>}
    {run?.status==="failed"&&<p role="alert">处理失败：{run.error_message}</p>}
    {run?.status==="completed"&&grid&&<>
      <div className={styles.sliceActions}><label>显示动态范围（dB）<input type="number" min="1" max="160" value={dynamic} onChange={e=>setDynamic(e.target.value)}/></label><button type="button" onClick={()=>onLocate(run.method.original_start-offset,run.method.original_end-offset)}>定位测量选段</button><button type="button" disabled={busy} onClick={()=>void download()}>导出声谱与 WAV</button></div>
      <div className={styles.spectrum}><svg viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label="原音声谱图，点击点读或拖动选段"
        onPointerDown={e=>{drag.current={x:e.clientX,t:tAt(e)};e.currentTarget.setPointerCapture(e.pointerId);}}
        onPointerMove={e=>{if(drag.current&&Math.abs(e.clientX-drag.current.x)>3){const t=tAt(e);onSelect(Math.min(t,drag.current.t),Math.max(t,drag.current.t));}}}
        onPointerCancel={()=>{drag.current=null;}}
        onPointerUp={e=>{const start=drag.current;drag.current=null;if(!start||Math.abs(e.clientX-start.x)>3)return;
          const t=tAt(e), box=e.currentTarget.getBoundingClientRect();
          const f=(1-(e.clientY-box.top)/box.height)*run.frequencies[run.frequencies.length-1];
          if(t<run.times[0]-offset || t>run.times[run.times.length-1]-offset || f<run.frequencies[0] || f>run.frequencies[run.frequencies.length-1]){
            active.current?.abort();setBusy(false);setReading(null);setTime("");setFrequency("");
            setError("点击处没有声谱测量值。");return;
          }
          setTime(number(t));setFrequency(number(f));onPoint(t);void query(false,t,f);}}>
        <Image data={run} range={range} offset={offset} dynamic={Math.max(1,Number(dynamic)||70)}/>
        {selection&&<rect x={(selection.start-range[0])/(range[1]-range[0])*1000} y="0" width={(selection.end-selection.start)/(range[1]-range[0])*1000} height="100" className={styles.selection}/>}
        {reading&&<><line x1={(reading.time-offset-range[0])/(range[1]-range[0])*1000} x2={(reading.time-offset-range[0])/(range[1]-range[0])*1000} y1="0" y2="100" className={styles.cursor}/><line x1="0" x2="1000" y1={100-reading.frequency/(grid.y1+(grid.ny-1)*grid.dy)*100} y2={100-reading.frequency/(grid.y1+(grid.ny-1)*grid.dy)*100} className={styles.cursor}/></>}
      </svg><div className={styles.frequency}><span>{number(grid.y1+(grid.ny-1)*grid.dy)} Hz</span><span>0 Hz</span></div></div>
      <div className={styles.spectrumAxis}>{[0,.25,.5,.75,1].map(v=><span key={v} style={{left:`${v*100}%`}}>{audioTime(range[0]+v*(range[1]-range[0]))} 秒</span>)}</div>
      <div className={styles.sliceActions}><label>点读时间（秒）<input type="number" step="0.001" value={time} onChange={e=>setTime(e.target.value)}/></label><label>点读频率（Hz）<input type="number" step="0.001" value={frequency} onChange={e=>setFrequency(e.target.value)}/></label><button type="button" disabled={busy||!time.trim()||!frequency.trim()} onClick={()=>void query()}>查询密度</button><button type="button" disabled={busy||!time.trim()||!frequency.trim()} onClick={()=>void query(true)}>保存读数</button></div>
      {reading&&<p className={styles.sliceReading}>原录音 {number(reading.time)} 秒 · {number(reading.frequency)} Hz · 功率谱密度 <strong>{reading.density.toExponential(10)} PCM²/Hz</strong></p>}
      <details className={styles.spectrumMethod}><summary>测量方法</summary><p>Praat {run.method.praat} · 高斯窗 {run.method.window_ms} ms · 原录音 {number(run.method.original_start)}–{number(run.method.original_end)} 秒</p><p>实际网格 {grid.nx} 个时间点 × {grid.ny} 个频率点 · 时步 {number(grid.dx*1000)} ms · 频步 {number(grid.dy)} Hz</p><p>完整网格双线性插值；颜色使用密度的 10 log₁₀ 显示，动态范围不改变测量值。</p></details>
    </>}
  </div>;
}
