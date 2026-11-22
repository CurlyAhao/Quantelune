"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftToLineIcon, ArrowRightToLineIcon, Maximize2Icon, ZoomInIcon } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { useVideoBytes } from "./use-video-bytes";
import { materialApi } from "@/lib/material-api";
import type { Material } from "@/lib/material-types";
import { useAnnotationSource } from "./workspace-annotations";
import { DownloadButton } from "./download-button";
import type { Dataset } from "@/lib/types";
import styles from "./workspace-audio-inspector.module.css";
import {TextGridTracks} from "./workspace-textgrid";
import {AudioSpectrum} from "./workspace-audio-spectrum";
import {AudioSpectrogram} from "./workspace-audio-spectrogram";
import {VoiceQuality} from "./workspace-voice-quality";
import {PitchCandidates} from "./pitch-candidates";
import {FormantCandidates} from "./formant-candidates";
import {audioTime} from "@/lib/audio-time";

export type Curve = {unit:string; points:{time:number|null;value:number|null}[]; valid_count?:number; total_count?:number; method?:string};
export type CurveSample = {sample_id:string;title?:string;display_title?:string;curves:Record<string,Curve>};
const labels:Record<string,string> = {waveform:"波形",rms:"短时RMS",pitch:"基频 F0",intensity:"Praat 强度",f1:"F1",f2:"F2",f3:"F3",f4:"F4",b1:"B1 带宽",b2:"B2 带宽",b3:"B3 带宽",b4:"B4 带宽"};
const fmt=audioTime;

type SoundView = { start: string; end: string; zoom: [number,number] | null };
type FrameStats = {valid_count:number;total_count:number;mean:number|null;min:number|null;max:number|null};
type FrameQuery = {tracks:Record<string,{unit:string;step_seconds:number|null;point?:{frame_time:number|null;value:number|null};selection?:FrameStats}>};
const valueText=(value:number|null|undefined,unit:string)=>value==null?"无有效值":(unit==="amplitude"?Number(value.toPrecision(5)).toString():fmt(value));
const resultViews = new Map<string, SoundView>();

/** origin is the sample time corresponding to zero in the actual playback file. */
export function AudioInspector({source,curves,origin=0,objectDomain,initialStart,initialEnd,onRange,onViewRange,rangeLabel="回看原片段",measurement,pitchMethod,annotationSource,onPitchVersion,materialContext,initialCursor,onCursorChange,hideRangeAction=false,onReviewClip}: {onReviewClip?:(start:number,end:number)=>void;initialCursor?:number|null;onCursorChange?:(time:number)=>void;hideRangeAction?:boolean;source?:Dataset; curves?:Record<string,Curve>; origin?:number; objectDomain?:[number,number]; initialStart?:number|null; initialEnd?:number|null; onRange?:(a:number,b:number)=>void; onViewRange?:(a:number,b:number)=>void; rangeLabel?:string;measurement?:{packageId:string;sampleId:string};pitchMethod?:string;annotationSource?:{datasetId:string;clockShift?:number};onPitchVersion?:(id:string)=>void;materialContext?:{material:Material;taskId?:string;sampleId?:string;sampleCollectionId?:string}}) {
  const videoBytes = useVideoBytes(materialContext?.material);
  const annotationId=annotationSource?.datasetId || (!measurement?source?.id:undefined);
  const gridMaterial=useQuery({queryKey:["audio-textgrid-material",annotationId,source?.sha256],
    queryFn:({signal})=>apiFetch<Material>("/api/v1/datasets/"+annotationId+"/source-material",{method:"POST",signal}),
    enabled:!!annotationId,staleTime:Infinity});
  const audio = useRef<HTMLAudioElement>(null), drag = useRef<{time:number;x:number}|null>(null);
  const [url,setUrl]=useState(""), [error,setError]=useState(""), [waveError,setWaveError]=useState("");
  const [wave,setWave]=useState<Curve|null>(null), [duration,setDuration]=useState(0), [cursor,setCursor]=useState<number|null>(null);
  const [start,setStart]=useState(initialStart == null ? "" : String(initialStart)), [end,setEnd]=useState(initialEnd == null ? "" : String(initialEnd));
  const [view,setView]=useState<[number,number]|null>(null), [playingRange,setPlayingRange]=useState<number|null>(null);
  const [waveMaterial,setWaveMaterial]=useState<Material|null>(null), [overview,setOverview]=useState<Curve|null>(null), [waveLoading,setWaveLoading]=useState(false);
  const [point,setPoint]=useState<number|null>(null), [activePanel,setActivePanel]=useState<"analysis"|"annotations">("analysis");
  const [bandwidthOpen,setBandwidthOpen]=useState(false);
  const [qualityPulses,setQualityPulses]=useState<number[]>([]);
  const [queryRange,setQueryRange]=useState<{point:number|null;start:number|null;end:number|null}>({point:null,start:null,end:null});
  const rangeKey = curves && source ? [source.id, source.sha256, origin, initialStart, initialEnd].join("/") : "";
  useEffect(() => {
    const saved = rangeKey ? resultViews.get(rangeKey) : undefined;
    setStart(saved?.start ?? (initialStart == null ? "" : String(initialStart)));
    setEnd(saved?.end ?? (initialEnd == null ? "" : String(initialEnd)));
    if (saved) setView(saved.zoom ?? null);
  }, [rangeKey, initialStart, initialEnd, source?.id]);
  function selectRange(a: string, b: string, zoom = view) {
    setStart(a); setEnd(b); setView(zoom);
    if (rangeKey) resultViews.set(rangeKey, {start:a, end:b, zoom});
  }
  useEffect(() => {
    if (playingRange == null) return;
    let id = 0;
    const advance = () => {
      const player = audio.current;
      if (!player) return;
      if (player.currentTime + origin >= playingRange) {
        player.pause(); player.currentTime = playingRange - origin;
        setCursor(playingRange); setPlayingRange(null);
      } else id = requestAnimationFrame(advance);
    };
    id = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(id);
  }, [playingRange, origin]);
  useEffect(()=>{
    setUrl("");setWave(null);setOverview(null);setWaveMaterial(null);setDuration(0);setCursor(null);setPoint(null);setPlayingRange(null);setError("");setWaveError("");setWaveLoading(true);
    if (!source) return;
    const controller=new AbortController(); let objectUrl=""; let context:AudioContext|null=null;
    void (async()=>{
      try {
        const material=materialContext?.material || await apiFetch<Material>(`/api/v1/datasets/${source.id}/source-material`,{method:"POST",signal:controller.signal});
        if(material.mime_type.startsWith("audio/")){
          const result=await materialApi.playback(material.id,controller.signal);
          if(controller.signal.aborted)return;
          setUrl(result.url);setDuration(result.duration);setWaveMaterial(material);
          const curve={unit:"幅度",points:result.points.map(point=>({time:point.time+origin,value:point.value}))};
          setOverview(curve);setWave(curve);setWaveLoading(false);return;
        }
        // Existing video-container preview retains its original time mapping.
        if (materialContext?.material && !videoBytes.data) {
          if (videoBytes.error) throw videoBytes.error;
          return;
        }
        const blob = videoBytes.data ?? await materialApi.blob(material.id,controller.signal);
        if(controller.signal.aborted)return;
        objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);
        // Avoid retaining or decoding arbitrarily large files in the browser.
        if(curves?.waveform?.points.length)return;
        if(blob.size>80*1024*1024){setWaveError("文件较大，未在浏览器解码波形；仍可播放与选段。");return;}
        try {
          context=new AudioContext(); const buffer=await context.decodeAudioData(await blob.arrayBuffer());
          if(controller.signal.aborted)return;
          const count=Math.min(900,buffer.length), step=Math.ceil(buffer.length/count), points:Curve["points"]=[];
          const channels=Array.from({length:buffer.numberOfChannels},(_,i)=>buffer.getChannelData(i));
          for(let i=0;i<buffer.length;i+=step){let peak=0; for(let j=i;j<Math.min(i+step,buffer.length);j++){let value=0;for(const data of channels)value+=data[j]/channels.length;if(Math.abs(value)>Math.abs(peak))peak=value;} points.push({time:origin+i/buffer.sampleRate,value:peak});}
          setWave({unit:"幅度",points,valid_count:points.length,total_count:points.length});
        } catch { if(!controller.signal.aborted)setWaveError("浏览器无法解码此文件的波形；仍可播放与选段。"); }
      } catch(reason) {if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:"无法读取声音文件。");}
      finally {if(!controller.signal.aborted)setWaveLoading(false);}
    })();
    return ()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);if(context)void context.close();};
  },[source?.id,source?.sha256,origin,!!curves?.waveform,videoBytes.data,videoBytes.error]);
  useEffect(()=>{
    if(!waveMaterial)return;
    if(!view){setWave(overview);setWaveLoading(false);return;}
    const controller=new AbortController();
    const timer=setTimeout(()=>{
      setWaveLoading(true);setWaveError("");
      void materialApi.audioPreview(waveMaterial.id,Math.max(0,view[0]-origin),Math.min(duration,view[1]-origin),controller.signal)
        .then(result=>{if(!controller.signal.aborted)setWave({unit:"幅度",points:result.points.map(point=>({time:point.time+origin,value:point.value}))});})
        .catch(reason=>{if(!controller.signal.aborted)setWaveError(reason instanceof Error?reason.message:"选段波形读取失败，请重新放大选段。");})
        .finally(()=>{if(!controller.signal.aborted)setWaveLoading(false);});
    },180);
    return ()=>{clearTimeout(timer);controller.abort();};
  },[waveMaterial?.id,view?.[0],view?.[1],overview,origin,duration]);
  const restoreTime=useRef<number|null>(null);
  const restoredView=useRef<{start:string;end:string;zoom:[number,number]|null;point:number|null;cursor:number|null}|null>(null);
  useEffect(()=>{
    if(!measurement)return;
    const key=`statforge:pitch-view:${measurement.packageId}:${measurement.sampleId}`;
    try {
      const raw=localStorage.getItem(key)||sessionStorage.getItem(key);
      if(raw){restoredView.current=JSON.parse(raw);}
      const saved=restoredView.current;if(!saved)return;
      selectRange(saved.start,saved.end,saved.zoom);setPoint(saved.point);setCursor(saved.cursor);restoreTime.current=saved.cursor;
    } catch { /* Ignore unavailable or malformed session state. */ }
  },[measurement?.packageId,measurement?.sampleId,source?.id]);
  function choosePitchVersion(id:string){
    if(!measurement||!onPitchVersion)return;
    try {localStorage.setItem(`statforge:pitch-view:${id}:${measurement.sampleId}`,JSON.stringify({start,end,zoom:view,point,cursor}));} catch { /* Navigation still works without saved view state. */ }
    onPitchVersion(id);
  }
  const entries=Object.entries({...curves,...(wave&&(!curves?.waveform||!!view)?{waveform:wave}:{})}).sort(([a],[b])=>(a==="waveform"?-1:b==="waveform"?1:0));
  const times=entries.flatMap(([,c])=>c.points.flatMap(p=>p.time!=null && Number.isFinite(p.time)?[p.time]:[]));
  const min=objectDomain?.[0] ?? (duration>0?origin:(times.length?Math.min(...times):0)), max=objectDomain?.[1] ?? (duration>0?origin+duration:(times.length?Math.max(...times):0));
  const lowerBound=objectDomain?min:Math.max(0,min);
  const range:[number,number]=view || [min,max]; const width=range[1]-range[0];
  const first=Number(start),last=Number(end), valid=!!start.trim() && !!end.trim() && Number.isFinite(first) && Number.isFinite(last) && first>=lowerBound-0.001 && last<=max+0.001 && last>first;
  const onViewRangeRef=useRef(onViewRange);onViewRangeRef.current=onViewRange;
  useEffect(()=>{if(valid)onViewRangeRef.current?.(first,last);},[first,last,valid]);
  function timeAt(event:React.PointerEvent<Element>) {const r=event.currentTarget.getBoundingClientRect();return Math.max(range[0],Math.min(range[1],range[0]+(event.clientX-r.left)/r.width*width));}
  useEffect(()=>{
    const timer=setTimeout(()=>setQueryRange({point,start:valid?first:null,end:valid?last:null}),180);
    return ()=>clearTimeout(timer);
  },[point,first,last,valid]);
  const readings=useQuery({queryKey:["audio-inspection",measurement?.packageId,measurement?.sampleId,queryRange],
    queryFn:({signal})=>apiFetch<FrameQuery>(`/api/v1/datasets/${measurement!.packageId}/audio-inspection`,{method:"POST",signal,body:JSON.stringify({sample_id:measurement!.sampleId,...queryRange})}),
    enabled:!!measurement&&(queryRange.point!=null||queryRange.start!=null),staleTime:Infinity});
  const ready=readings.data&&queryRange.point===point&&queryRange.start===(valid?first:null)&&queryRange.end===(valid?last:null);
  const allReadRows=Object.entries(ready?readings.data!.tracks:{});
  const readRows=allReadRows.filter(([name])=>!/^b[1-4]$/.test(name));
  const bandwidthRows=allReadRows.filter(([name])=>/^b[1-4]$/.test(name));
  function pointValues(rows:typeof readRows) {
    return rows.map(([name,track])=><span key={name} title={track.point?.frame_time!=null?"测量帧 "+fmt(track.point.frame_time)+" 秒":undefined}>{labels[name]||name}{track.unit==="amplitude"?"（幅度）":""}：{valueText(track.point?.value,track.unit)}{track.point?.value!=null&&track.unit!=="amplitude"?" "+track.unit:""}</span>);
  }
  function frameTable(rows:typeof readRows) {
    return <div className={styles.statistics}><table><thead><tr><th>测量</th><th>有效/总帧</th><th>均值</th><th>最小</th><th>最大</th></tr></thead><tbody>{rows.filter(([,track])=>track.selection).map(([name,track])=><tr key={name}><th>{labels[name]||name}{"（"+(track.unit==="amplitude"?"幅度":track.unit)+"）"}</th><td>{track.selection!.valid_count}/{track.selection!.total_count}</td>{(["mean","min","max"] as const).map(key=><td key={key}>{track.selection![key]==null?"—":valueText(track.selection![key],track.unit)}</td>)}</tr>)}</tbody></table></div>;
  }
  function pointerDown(event:React.PointerEvent<Element>){drag.current={time:timeAt(event),x:event.clientX};event.currentTarget.setPointerCapture(event.pointerId);}
  function pointerMove(event:React.PointerEvent<Element>){const from=drag.current;if(from&&Math.abs(event.clientX-from.x)>3){const now=timeAt(event);selectRange(fmt(Math.min(from.time,now)),fmt(Math.max(from.time,now)));}}
  function pointerUp(event:React.PointerEvent<Element>,snapPulses=false){
    const from=drag.current;if(!from)return;drag.current=null;const now=timeAt(event);
    if(Math.abs(event.clientX-from.x)>3)selectRange(fmt(Math.min(from.time,now)),fmt(Math.max(from.time,now)));
    else if(snapPulses&&qualityPulses.length&&now>=qualityPulses[0]&&now<=qualityPulses[qualityPulses.length-1]){let nearest=qualityPulses[0];for(const time of qualityPulses)if(Math.abs(time-now)<Math.abs(nearest-now))nearest=time;locatePulse(nearest);}
    else{setPoint(now);setCursor(now);setPlayingRange(null);if(audio.current)audio.current.currentTime=Math.max(0,now-origin);}
  }
  const overlays=<>{valid&&last>=range[0]&&first<=range[1]&&<rect x={Math.max(0,(first-range[0])/(width||1)*1000)} y="0" width={Math.max(0,(Math.min(last,range[1])-Math.max(first,range[0]))/(width||1)*1000)} height="100" className={styles.selection}/>}
    {cursor!=null&&cursor>=range[0]&&cursor<=range[1]&&<line x1={(cursor-range[0])/(width||1)*1000} x2={(cursor-range[0])/(width||1)*1000} y1="0" y2="100" className={styles.cursor}/>}</>;
  const axes=<div className={styles.axis} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={()=>{drag.current=null;}}>{[0,.25,.5,.75,1].map(f=><span key={f}>{fmt(range[0]+width*f)} 秒</span>)}</div>;
  function locateAnnotation(a:number,b?:number,play=false){
    setError("");setPoint(b==null?a:null);setPlayingRange(null);
    if(a<min-.000001||(b??a)>max+.000001){setError("此标注超出当前派生音频范围，请回原录音查看。");return;}
    if(b!=null)selectRange(String(a),String(b),a<range[0]||b>range[1]?null:view);
    else selectRange("","",a<range[0]||a>range[1]?null:view);
    const from=play&&b==null?Math.max(min,a-.25):a, to=b??Math.min(max,a+.25);
    if(audio.current){audio.current.pause();audio.current.currentTime=Math.max(0,from-origin);}
    setCursor(from);
    if(play&&audio.current){setPlayingRange(to);void audio.current.play().catch(()=>setError("无法播放此标注。"));}
  }
  const material=materialContext?.material;
  const annotations=useAnnotationSource({filename:material?.filename||source?.original_filename||"",material_id:material?.id,material_sha256:material?.sha256,material_version:material?.version,related_material_ids:material?.metadata.annotation_material_ids,task_id:materialContext?.taskId,sample_id:materialContext?.sampleId,sample_collection_id:materialContext?.sampleCollectionId},locator=>{if(locator.kind==="time")locateAnnotation(locator.start_seconds+origin,locator.point ? undefined : locator.end_seconds+origin,!locator.point);},!!material);
  const timeAnnotations=!objectDomain&&gridMaterial.data?<TextGridTracks key={gridMaterial.data.id} material={gridMaterial.data} audioInput={source?{datasetId:source.id,...measurement}:undefined} range={range} cursor={cursor} selection={valid?{start,end}:undefined} clockShift={annotationSource?.clockShift??origin} onLocate={locateAnnotation}/>:gridMaterial.isError?<p role="alert">{gridMaterial.error.message}</p>:null;
  const inspection=<>
    {source&&!objectDomain&&<section className={styles.spectrogram}><AudioSpectrogram key={source.id+"/"+(measurement?.sampleId||"")} datasetId={source.id} measurement={measurement} selection={valid?{start:first,end:last}:undefined} range={range} onLocate={locateAnnotation} onPoint={locatePulse} onSelect={(a,b)=>selectRange(String(a),String(b))}/></section>}
    <details className="rw-audio-more"><summary>其他声音分析</summary>
    {source&&!objectDomain&&<VoiceQuality originalInput={!!annotationSource} key={source.id+"/"+(measurement?.sampleId||"")} datasetId={source.id} selection={valid?{start:first,end:last}:undefined} measurement={measurement} onLocate={locateAnnotation} onPulses={setQualityPulses}/>}
    {source&&!objectDomain&&<AudioSpectrum originalInput={!!annotationSource} key={source.id+"/"+(measurement?.sampleId||"")} datasetId={source.id} cursor={cursor} selection={valid?{start:first,end:last}:undefined} measurement={measurement} onLocate={locateAnnotation}/>}
    </details>
    {measurement&&(point!=null||valid)&&<div className={styles.readings}>
      {readings.isError?<p role="alert">{readings.error.message}</p>:!ready?<p role="status">正在读取测量帧…</p>:<>
        {point!=null&&<div className={styles.pointReadout}><strong>点读 {fmt(point)} 秒</strong>{pointValues(readRows)}</div>}
        {valid&&<><strong>选段帧统计 · {fmt(first)}–{fmt(last)} 秒</strong>{frameTable(readRows)}<p className={styles.note}>按帧中心落在选段内计数，结束点不含。{readRows.some(([name])=>name==="intensity")&&"强度按所选帧能量平均；整段摘要使用 Praat 能量积分平均。"}</p></>}
        {!!bandwidthRows.length&&<details className={styles.bandwidth} open={bandwidthOpen} onToggle={(event)=>setBandwidthOpen(event.currentTarget.open)}><summary>共振峰带宽 B1–B4</summary>
          {point!=null&&<div className={styles.pointReadout}><strong>点读 {fmt(point)} 秒</strong>{pointValues(bandwidthRows)}</div>}
          {valid&&<><strong>带宽选段统计 · {fmt(first)}–{fmt(last)} 秒</strong>{frameTable(bandwidthRows)}</>}
        </details>}
      </>}
    </div>}
  </>;
  function playRange(){if(!audio.current||!valid)return;if(objectDomain&&(first<origin-.001||last>origin+duration+.001)){setError("所选 Pitch 时间超出关联音轨，请调整选段或时间偏移。");return;}setError("");audio.current.currentTime=Math.max(0,first-origin);setPlayingRange(last);void audio.current.play().catch(()=>setError("无法播放选段。"));}
  function locatePulse(time:number){setPoint(time);setCursor(time);setPlayingRange(null);if(audio.current){audio.current.pause();audio.current.currentTime=Math.max(0,time-origin);}}
  return <section className={styles.inspector} aria-label="声音查看器" onPointerDownCapture={material?annotations.activate:undefined}><div className={styles.heading}><strong>声音查看</strong>{source && <span>{source.original_filename} · {(source.file_size/1024/1024).toFixed(2)} MB</span>}{material&&<div className="rw-inline-actions">{annotations.enabled&&<button onClick={annotations.open}>查看标注 {annotations.segments.length||""}</button>}<DownloadButton identity={material.id} download={signal=>materialApi.download(material.id,material.filename,signal)}>导出原文件</DownloadButton></div>}</div>
    {source && url && <audio ref={audio} controls src={url} preload="metadata" onLoadedMetadata={e=>{setDuration(Number.isFinite(e.currentTarget.duration)?e.currentTarget.duration:0);const resume=restoreTime.current??initialCursor??initialStart??origin;e.currentTarget.currentTime=Math.max(0,resume-origin);setCursor(e.currentTarget.currentTime+origin);restoreTime.current=null;
      if (!measurement && initialStart == null && initialEnd == null && !start && !end && e.currentTarget.duration > 0) {
        selectRange(String(Math.max(0,origin)),String(origin+e.currentTarget.duration));
      }}} onTimeUpdate={e=>{const time=e.currentTarget.currentTime+origin;setCursor(time);onCursorChange?.(time);}} onPlay={()=>setError("")} onPause={()=>setPlayingRange(null)} />}
    {!source && !objectDomain && <p>找不到此结果对应的音频文件，仍可查看已生成曲线。</p>}{error && <p role="alert">{error}</p>}{waveError && <p role="alert">{waveError}</p>}{waveLoading&&<p role="status">正在读取{view?"选段":"声音概览"}…</p>}
    <div className={styles.controls}><p className={styles.note}>点击点读，拖动选段 · {objectDomain?"Pitch 时间":"原材料时间"}（秒）{cursor!=null && ` · 播放位置 ${fmt(cursor)} 秒`}</p></div>
    {qualityPulses.length>0&&<p className={styles.note}>周期点 {qualityPulses.length} 个 · 点击波形上的竖线定位{point!=null&&` · 当前 ${point.toFixed(6)} 秒`}</p>}
    {entries.map(([name,curve])=>{const points=curve.points.filter(p=>p.time!=null && p.time>=range[0] && p.time<=range[1] && p.value!=null && Number.isFinite(p.value));const values=points.map(p=>p.value!);const lo=values.length?Math.min(...values):0,hi=values.length?Math.max(...values):1;let path="",dots="",connected=false;
      for(const p of curve.points){if(p.time==null || p.value==null || !Number.isFinite(p.time) || !Number.isFinite(p.value)){connected=false;continue;}if(p.time<range[0]||p.time>range[1]){connected=false;continue;}const x=(p.time-range[0])/(width||1)*1000,y=85-(p.value-lo)/(hi-lo||1)*70;path+=(connected?"L":"M")+x.toFixed(2)+" "+y.toFixed(2)+" ";dots+="M"+x.toFixed(2)+" "+y.toFixed(2)+"h0.01 ";connected=true;}
      return <div key={name} className={styles.curve}><div className={styles.curveHeading}><span><strong>{labels[name]||name}</strong> · {curve.unit==="amplitude"?"幅度":curve.unit}{curve.valid_count!=null && curve.total_count!=null && ` · 有效 ${curve.valid_count}/${curve.total_count}`}{curve.method && ` · ${curve.method}`}{name==="pitch"&&pitchMethod&&` · ${pitchMethod}`}</span>{name==="waveform"&&<span className={styles.waveActions}><button type="button" disabled={!valid} onClick={()=>selectRange(start,end,[first,last])}><ZoomInIcon size={15}/>放大选段</button><button type="button" onClick={()=>selectRange(start,end,null)}><Maximize2Icon size={15}/>全段视图</button></span>}</div>
      {values.length ? <><svg viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label={`${labels[name]||name}，拖动选择时间范围`} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={event=>pointerUp(event,name==="waveform")} onPointerCancel={()=>{drag.current=null;}}>
        {valid && last>=range[0] && first<=range[1] && <rect x={Math.max(0,(first-range[0])/(width||1)*1000)} y="0" width={Math.max(0,(Math.min(last,range[1])-Math.max(first,range[0]))/(width||1)*1000)} height="100" className={styles.selection}/>}<path d={path} className={styles.line}/><path d={dots} className={styles.points}/>{name==="waveform"&&qualityPulses.filter(t=>t>=range[0]&&t<=range[1]).map((t,i)=><line key={i} x1={(t-range[0])/(width||1)*1000} x2={(t-range[0])/(width||1)*1000} y1="0" y2="100" className={styles.pulse} onPointerDown={e=>e.stopPropagation()} onPointerUp={e=>e.stopPropagation()} onClick={e=>{e.stopPropagation();locatePulse(t);}}><title>{t.toFixed(6)} 秒</title></line>)}{cursor!=null && cursor>=range[0] && cursor<=range[1] && <line x1={(cursor-range[0])/(width||1)*1000} x2={(cursor-range[0])/(width||1)*1000} y1="0" y2="100" className={styles.cursor}/>}</svg>{axes}<p className={styles.note}>纵轴 {fmt(lo)} 至 {fmt(hi)} {curve.unit==="amplitude"?"幅度":curve.unit}</p></> : <p>当前范围没有有效测量</p>}{name==="pitch"&&measurement&&<PitchCandidates packageId={measurement.packageId} sampleId={measurement.sampleId} point={point} view={view} selection={valid?{start:first,end:last}:null} onLocate={locatePulse} onReprocess={onRange&&max>min?()=>onRange(valid?first:Math.max(0,min),valid?last:max):undefined} onVersion={onPitchVersion?choosePitchVersion:undefined}/>} {name==="f1"&&measurement&&<FormantCandidates packageId={measurement.packageId} sampleId={measurement.sampleId} point={point} view={view} selection={valid?{start:first,end:last}:null} onLocate={locatePulse} onVersion={onPitchVersion?choosePitchVersion:undefined}/>} </div>})}
    {!entries.length && <p>暂无曲线；可直接播放和选择时间范围。</p>}
    <div className={styles.actions}>
      <div className={styles.field}><span>选段开始（秒）</span><span className={styles.timeInput}><input aria-label="选段开始（秒）" type="number" step="0.01" value={start} onChange={e=>selectRange(e.target.value,end)}/><button type="button" title="取播放位置为起点" aria-label="取播放位置为起点" disabled={cursor==null} onClick={()=>selectRange(fmt(cursor!),end)}><ArrowLeftToLineIcon size={15}/></button></span></div>
      <div className={styles.field}><span>选段结束（秒）</span><span className={styles.timeInput}><input aria-label="选段结束（秒）" type="number" step="0.01" value={end} onChange={e=>selectRange(start,e.target.value)}/><button type="button" title="取播放位置为终点" aria-label="取播放位置为终点" disabled={cursor==null} onClick={()=>selectRange(start,fmt(cursor!))}><ArrowRightToLineIcon size={15}/></button></span></div>
      <button type="button" disabled={!valid || !url} onClick={playRange}>回听选段</button>
      <button type="button" disabled={max<=lowerBound} onClick={()=>selectRange(fmt(lowerBound),fmt(max))}>选择全段</button>
      {onRange && !hideRangeAction && !(materialContext&&onViewRange) && <button type="button" disabled={!valid} onClick={()=>onRange(first,last)}>{rangeLabel}</button>}
      {annotations.enabled&&<button type="button" disabled={!valid} onClick={()=>annotations.annotate({kind:"time",start_seconds:first-origin,end_seconds:last-origin,note:""})}>标注选段</button>}
      {annotations.enabled&&<button type="button" disabled={!url||cursor==null} onClick={()=>{if(!audio.current)return;audio.current.pause();const at=audio.current.currentTime;annotations.annotate({kind:"time",start_seconds:at,end_seconds:at,point:true,note:""});}}>标记当前时间点</button>}
      {onReviewClip&&valid&&(first>lowerBound+.001||last<max-.001)&&<button type="button" onClick={()=>onReviewClip(first,last)}>回看原片段</button>}
    </div>
    <section className={styles.workArea}>
      <div className={styles.workTabs} role="tablist" aria-label="声音操作">
        <button type="button" role="tab" aria-selected={activePanel==="analysis"} onClick={()=>setActivePanel("analysis")}>声音分析</button>
        <button type="button" role="tab" aria-selected={activePanel==="annotations"} onClick={()=>setActivePanel("annotations")}>时间标注</button>
      </div>
      <div role="tabpanel" hidden={activePanel!=="analysis"} className={styles.workPanel}>{inspection}</div>
      <div role="tabpanel" hidden={activePanel!=="annotations"} className={styles.workPanel}>{annotations.enabled&&<button type="button" onClick={annotations.open}>查看已有标注 {annotations.segments.length||""}</button>}{timeAnnotations}</div>
    </section>
  </section>;
}
