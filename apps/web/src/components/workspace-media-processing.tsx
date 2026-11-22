"use client";

import { PraatProgram } from "./praat-program";
import { SearchSelect } from "@/components/ui/search-select";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import type { Dataset } from "@/lib/types";
import styles from "./workspace-media-samples.module.css";

export type MediaOptions = {steps: string[]; audio_source: "video" | "independent"; sample_rate: number; channels: number; audio_filter:"none"|"lowpass"|"highpass"|"bandpass"; filter_low_hz:number; filter_high_hz:number; filter_transition_hz:number; window_ms: number; hop_ms: number; n_mfcc: number; frames_per_video: number; pitch_method:"raw_autocorrelation"|"filtered_autocorrelation"|"raw_cross_correlation"|"filtered_cross_correlation"; pitch_max_number_of_candidates:number; pitch_very_accurate:boolean; pitch_attenuation_at_top:number; pitch_silence_threshold:number; pitch_voicing_threshold:number; pitch_octave_cost:number; pitch_octave_jump_cost:number; pitch_voiced_unvoiced_cost:number; intensity_minimum_pitch_hz:number; intensity_time_step:number; intensity_subtract_mean:boolean; pitch_floor_hz:number; pitch_ceiling_hz:number; praat_time_step:number; formant_ceiling_hz:number; formant_window_s:number; formant_preemphasis_hz:number; speech_count?:number; speech_count_unit:"english_words"|"chinese_characters"; speech_text_scope:"full_media"|"selected_range"; speech_duration_basis:"audio_selection"|"video_duration"};
export const defaultMediaOptions: MediaOptions = {steps:["extract_audio","mfcc","rms","frames"],audio_source:"video",sample_rate:16000,channels:1,audio_filter:"none",filter_low_hz:300,filter_high_hz:3400,filter_transition_hz:100,window_ms:25,hop_ms:10,n_mfcc:20,frames_per_video:5,pitch_method:"raw_autocorrelation",pitch_max_number_of_candidates:15,pitch_very_accurate:false,pitch_attenuation_at_top:0.03,pitch_silence_threshold:0.03,pitch_voicing_threshold:0.45,pitch_octave_cost:0.01,pitch_octave_jump_cost:0.35,pitch_voiced_unvoiced_cost:0.14,intensity_minimum_pitch_hz:75,intensity_time_step:0.01,intensity_subtract_mean:true,pitch_floor_hz:75,pitch_ceiling_hz:600,praat_time_step:0.01,formant_ceiling_hz:5500,formant_window_s:0.025,formant_preemphasis_hz:50,speech_count_unit:"english_words",speech_text_scope:"full_media",speech_duration_basis:"audio_selection"};
export function mediaOptionErrors(value: MediaOptions, sourceRate?:number): Partial<Record<keyof MediaOptions, string>> {
  const errors: Partial<Record<keyof MediaOptions, string>> = {};
  const check = (key: keyof MediaOptions, label: string, max: number, integer = false, min = 0) => {
    const n = value[key];
    if (typeof n !== "number" || !Number.isFinite(n) || n <= min) errors[key] = `${label}须大于 ${min}${key === "formant_ceiling_hz" ? " Hz" : ""}`;
    else if (n > max) errors[key] = `${label}不能大于 ${max}`;
    else if (integer && !Number.isInteger(n)) errors[key] = `${label}须为整数`;
  };
  check("sample_rate", "采样率", 192000, true, 7999);
  if (typeof value.sample_rate !== "number" || !Number.isFinite(value.sample_rate) || value.sample_rate < 8000) errors.sample_rate = "采样率须至少为 8000 Hz";
  check("window_ms", "窗口", 10000);
  check("hop_ms", "步长", 10000);
  check("n_mfcc", "MFCC 维数", 128, true);
  check("frames_per_video", "抽帧数量", 30, true);
  if (value.steps.includes("pitch")) {
    check("pitch_max_number_of_candidates", "候选数量", 100, true, 1);
    for (const [key,label,max] of [
      ["pitch_silence_threshold","静音阈值",1],["pitch_voicing_threshold","有声阈值",1],
      ["pitch_octave_cost","八度代价",10],["pitch_octave_jump_cost","八度跳跃代价",10],["pitch_voiced_unvoiced_cost","有声/无声切换代价",10],
    ] as const) {
      const n=value[key];
      if (!Number.isFinite(n) || n<0 || n>max) errors[key]=label+"须在 0 到 "+max+" 之间";
    }
    if ((value.pitch_method||"raw_autocorrelation").startsWith("filtered_") && !(value.pitch_attenuation_at_top>0 && value.pitch_attenuation_at_top<1)) errors.pitch_attenuation_at_top="顶部频率衰减须大于 0、小于 1";
  }
  if (value.steps.includes("intensity")) {
    check("intensity_minimum_pitch_hz", "强度最低周期频率", 1000);
    check("intensity_time_step", "强度帧步长", 1);
  }
  check("pitch_floor_hz", "基频下限", 1000);
  check("pitch_ceiling_hz", "基频上限", 2000);
  check("praat_time_step", "测量步长", 1);
  check("formant_ceiling_hz", "共振峰上限", 12000);
  check("formant_window_s", "Burg 窗长", 1);
  check("formant_preemphasis_hz", "预加重起点", 5000);
  if (!errors.pitch_floor_hz && !errors.pitch_ceiling_hz && value.pitch_floor_hz >= value.pitch_ceiling_hz) errors.pitch_ceiling_hz = "基频上限应高于下限";
  if (value.speech_count != null && (!Number.isInteger(value.speech_count) || value.speech_count < 0)) errors.speech_count = "计数须为非负整数";
  if (value.audio_filter && value.audio_filter!=="none") {
    const ceiling=sourceRate?sourceRate/2:96000;
    if(value.audio_filter!=="lowpass") check("filter_low_hz","滤波下限",ceiling);
    if(value.audio_filter!=="highpass") check("filter_high_hz","滤波上限",ceiling);
    check("filter_transition_hz","过渡宽度",ceiling);
    if(value.audio_filter==="bandpass" && value.filter_low_hz>=value.filter_high_hz) errors.filter_high_hz="滤波上限应高于下限";
  }
  return errors;
}
const stepNames: Record<string,string> = {extract_audio:"提取音轨",pitch:"基频 F0、CV 与极差",intensity:"Praat 强度",formants:"共振峰 F1–F4",speech_rate:"语速",mfcc:"MFCC",rms:"RMS（短时与整段）",frames:"均匀取帧",resnet:"图像特征 ResNet18",tfidf:"文本 TF-IDF",minilm:"文本 MiniLM"};
export function MediaProcessingOptions({value,onChange,disabled=false,audioOnly=false,needsClipCount=false,sourceRate}:{sourceRate?:number;audioOnly?:boolean;needsClipCount?:boolean;value:MediaOptions;onChange:(value:MediaOptions)=>void;disabled?:boolean}) {
  const pitchSettings=useRef<Partial<Record<MediaOptions["pitch_method"], Partial<MediaOptions>>>>({});
  const pitchKeys = ["pitch_floor_hz","pitch_ceiling_hz","praat_time_step","pitch_max_number_of_candidates","pitch_very_accurate","pitch_attenuation_at_top","pitch_silence_threshold","pitch_voicing_threshold","pitch_octave_cost","pitch_octave_jump_cost","pitch_voiced_unvoiced_cost"] as const;
  function changePitchMethod(method:MediaOptions["pitch_method"]) {
    const saved:Partial<MediaOptions>={};
    for (const key of pitchKeys) Object.assign(saved,{[key]:value[key]});
    pitchSettings.current[value.pitch_method]=saved;
    const filtered=method.startsWith("filtered_");
    const defaults={...saved,pitch_silence_threshold:filtered?0.09:0.03,pitch_voicing_threshold:filtered?0.50:0.45,pitch_octave_cost:filtered?0.055:0.01};
    onChange({...value,...(pitchSettings.current[method] || defaults),pitch_method:method});
  }
  const errors=mediaOptionErrors(value,sourceRate);
  const audio=value.steps.some(s=>["extract_audio","mfcc","rms","pitch","intensity","formants","speech_rate"].includes(s));
  const frames=value.steps.some(s=>["frames","resnet"].includes(s));
  const numeric=(key: keyof MediaOptions,label:string,min:number,max:number,step=1)=><label>{label}<input type="number" aria-label={label} min={min} max={max} step={step} value={String(value[key])} aria-invalid={!!errors[key]} onChange={e=>onChange({...value,[key]:Number(e.target.value)})}/>{errors[key] && <small className="rw-error" role="alert">{errors[key]}</small>}</label>;
  return <fieldset className={styles.processing} disabled={disabled}><legend>想做什么</legend>
    <div className={styles.steps}>{Object.entries(stepNames).filter(([key])=>!audioOnly || !["frames","resnet"].includes(key)).map(([step,label])=><label key={step}><input type="checkbox" checked={value.steps.includes(step)} onChange={e=>onChange({...value,steps:e.target.checked?[...value.steps,step]:value.steps.filter(s=>s!==step)})}/>{label}</label>)}</div>
    {value.steps.includes("pitch") && <div className={styles.pitchMethod}>
      <label>基频方法<SearchSelect aria-label="基频方法" value={value.pitch_method} onChange={e=>changePitchMethod(e.target.value as MediaOptions["pitch_method"])}>
        <option value="raw_autocorrelation">原始自相关</option><option value="filtered_autocorrelation">滤波自相关</option><option value="raw_cross_correlation">原始互相关</option><option value="filtered_cross_correlation">滤波互相关</option>
      </SearchSelect></label>
      {(value.pitch_method||"raw_autocorrelation").startsWith("filtered_") && <PraatProgram/>}
    </div>}
    {audio && !audioOnly && <label>声音来源 <SearchSelect aria-label="声音来源" value={value.audio_source} onChange={e=>onChange({...value,audio_source:e.target.value as MediaOptions["audio_source"]})}><option value="video">视频内音轨</option><option value="independent">已关联的独立 WAV</option></SearchSelect></label>}
    {(value.steps.includes("speech_rate") || errors.speech_count) && <div className={styles.parameters}><label>计数单位<SearchSelect value={value.speech_count_unit} onChange={e=>onChange({...value,speech_count_unit:e.target.value as MediaOptions["speech_count_unit"]})}><option value="english_words">英文词 / 秒</option><option value="chinese_characters">汉字 / 秒</option></SearchSelect></label><label>{needsClipCount ? (value.speech_count_unit === "english_words" ? "选段词数" : "选段字数") : "人工计数（可选）"}<input type="number" min="0" step="1" placeholder={needsClipCount ? "填这段的实际计数" : "从关联文本计数"} value={value.speech_count ?? ""} aria-invalid={!!errors.speech_count} onChange={e=>onChange({...value,speech_count:e.target.value===""?undefined:Number(e.target.value)})}/>{errors.speech_count && <small className="rw-error" role="alert">{errors.speech_count}</small>}</label>{!audioOnly && <label>时长<SearchSelect value={value.speech_duration_basis} onChange={e=>onChange({...value,speech_duration_basis:e.target.value as MediaOptions["speech_duration_basis"]})}><option value="audio_selection">所选声音时长</option><option value="video_duration">完整视频时长</option></SearchSelect></label>}</div>}
    {audio&&<label>音轨滤波 <SearchSelect aria-label="音轨滤波" value={value.audio_filter||"none"} onChange={e=>onChange({...value,audio_filter:e.target.value as MediaOptions["audio_filter"]})}>
      <option value="none">不滤波</option><option value="lowpass">低通</option><option value="highpass">高通</option><option value="bandpass">带通</option>
    </SearchSelect></label>}
    {audio&&value.audio_filter&&value.audio_filter!=="none"&&<details open={Object.keys(errors).some(key=>key.startsWith("filter_"))||undefined}><summary>滤波参数</summary><div className={styles.parameters}>
      {value.audio_filter!=="lowpass"&&numeric("filter_low_hz","滤波下限 Hz",1,sourceRate?sourceRate/2:96000)}
      {value.audio_filter!=="highpass"&&numeric("filter_high_hz","滤波上限 Hz",1,sourceRate?sourceRate/2:96000)}
      {numeric("filter_transition_hz","过渡宽度 Hz",1,sourceRate?sourceRate/2:96000)}
    </div><p>Praat Hann · 先选段、滤波，再按所选采样率保存。所选特征使用这份滤波音轨。</p></details>}
    <details open={Object.keys(errors).some(key=>key!=="speech_count") || undefined}><summary>处理参数</summary><div className={styles.parameters}>
      {(audio || errors.sample_rate) && <>{numeric("sample_rate","采样率 Hz",8000,192000)}<label>声道<SearchSelect aria-label="声道" value={value.channels} onChange={e=>onChange({...value,channels:Number(e.target.value)})}><option value={1}>单声道</option><option value={2}>双声道</option></SearchSelect></label></>}
      {(value.steps.some(s=>["mfcc","rms"].includes(s)) || errors.window_ms || errors.hop_ms) && <>{numeric("window_ms","窗口 ms",0.1,10000,0.1)}{numeric("hop_ms","步长 ms",0.1,10000,0.1)}</>}
      {(value.steps.includes("mfcc") || errors.n_mfcc) && numeric("n_mfcc","MFCC 维数",1,128)}
      {(value.steps.includes("pitch") || errors.pitch_floor_hz) && numeric("pitch_floor_hz","基频下限 Hz",20,600)}
      {(value.steps.includes("pitch") || errors.pitch_ceiling_hz) && numeric("pitch_ceiling_hz",(value.pitch_method||"raw_autocorrelation").startsWith("filtered_")?"顶部频率 Hz":"基频上限 Hz",40,2000)}
      {(value.steps.some(s=>["pitch","formants"].includes(s)) || errors.praat_time_step) && numeric("praat_time_step","F0 / 共振峰步长（秒）",0.001,0.1,0.001)}
      {(value.steps.includes("formants") || errors.formant_ceiling_hz || errors.formant_window_s || errors.formant_preemphasis_hz) && <>{numeric("formant_ceiling_hz","共振峰上限 Hz",1000,10000)}{numeric("formant_window_s","Burg 窗长（秒）",0.005,0.1,0.001)}{numeric("formant_preemphasis_hz","预加重起点 Hz",0,1000)}</>}
      {value.steps.includes("intensity") && <>
        {numeric("intensity_minimum_pitch_hz","强度最低周期频率 Hz",1,1000)}
        <label>强度帧步长 ms<input type="number" aria-label="强度帧步长 ms" min="0.1" max="1000" step="1" value={Number((value.intensity_time_step*1000).toFixed(6))} aria-invalid={!!errors.intensity_time_step} onChange={e=>onChange({...value,intensity_time_step:Number(e.target.value)/1000})}/>{errors.intensity_time_step&&<small className="rw-error" role="alert">{errors.intensity_time_step}</small>}</label>
        <label className={styles.pitchAccurate}><input type="checkbox" checked={value.intensity_subtract_mean} onChange={e=>onChange({...value,intensity_subtract_mean:e.target.checked})}/>扣除局部均值</label>
      </>}
      {(frames || errors.frames_per_video) && numeric("frames_per_video","抽帧数量",1,30)}
    </div>
    {value.steps.includes("pitch") && <details className={styles.pitchAdvanced} open={Object.keys(errors).some(k=>k.startsWith("pitch_") && !["pitch_floor_hz","pitch_ceiling_hz"].includes(k)) || undefined}><summary>基频高级参数</summary><div className={styles.parameters}>
      {numeric("pitch_max_number_of_candidates","候选数量",2,100)}
      {(value.pitch_method||"raw_autocorrelation").startsWith("filtered_") && numeric("pitch_attenuation_at_top","顶部频率衰减",0.001,0.999,0.001)}
      {numeric("pitch_silence_threshold","静音阈值",0,1,0.01)}
      {numeric("pitch_voicing_threshold","有声阈值",0,1,0.01)}
      {numeric("pitch_octave_cost","八度代价",0,10,0.001)}
      {numeric("pitch_octave_jump_cost","八度跳跃代价",0,10,0.01)}
      {numeric("pitch_voiced_unvoiced_cost","有声/无声切换代价",0,10,0.01)}
    </div><label className={styles.pitchAccurate}><input type="checkbox" checked={value.pitch_very_accurate} onChange={e=>onChange({...value,pitch_very_accurate:e.target.checked})}/>高精度</label></details>}
    </details>
  </fieldset>;
}

type Pair={sample_id?:string;video_id:string;audio_id:string; text_id:string; audio_offset_seconds:string; content_reviewed:boolean;case_title?:string;member_ids?:string[]};
export function MediaAssociationEditor({dataset,datasets,taskId,onChoose,initial,beforeSave,embedded=false,ownerCase}:{dataset:Dataset;datasets:Dataset[];taskId?:string;onChoose:(id:string)=>void;initial?:Pair[];beforeSave?:()=>Promise<boolean>;embedded?:boolean;ownerCase?:{collectionId:string;sampleId:string}}) {
  const client=useQueryClient();
  const [opened,setOpened]=useState(embedded),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [filter,setFilter]=useState("");
  const [marked,setMarked]=useState<string[]>([]);
  const metadata=useQuery({queryKey:["media-filename-metadata",dataset.id],queryFn:({signal})=>apiFetch<Record<string,{source:string;label:string;key:string;values:Record<string,string>}>>(`/api/v1/datasets/${dataset.id}/media-filename-metadata`,{signal}),enabled:opened});
  const standalone=initial ? initial.every(row=>!row.video_id) : dataset.file_format==="wav";
  const [pairs,setPairs]=useState<Pair[]>(initial || [{video_id:standalone?"":dataset.id,audio_id:standalone?dataset.id:"",text_id:"",audio_offset_seconds:standalone?"":"0",content_reviewed:false}]);
  const videos=datasets.filter(d=>d.raw_file && d.file_format==="mp4" && !d.media_result);
  const audio=datasets.filter(d=>d.raw_file && d.file_format==="wav" && (!d.media_result||d.id===dataset.id));
  const caseMaterials=datasets.filter(d=>!d.media_result && d.upload_status!=="deleted" && /^(pdf|txt|md|markdown|png|jpg|jpeg|gif|webp|wav|mp4)$/i.test(d.file_format));
  const texts=datasets.filter(d=>d.raw_file && ["txt","md","markdown"].includes(d.file_format) && !d.media_result);
  const name=(d:Dataset)=>d.display_name || (d.media_result ? d.original_filename : d.source_path || d.original_filename);
  const filenameKey=(d:Dataset)=>d.original_filename.replace(/\\/g,"/").split("/").pop()!.toLocaleLowerCase();
  const nameCounts=new Map<string,number>();
  for(const item of [...videos,...audio]) { const key=filenameKey(item);nameCounts.set(key,(nameCounts.get(key) || 0)+1); }
  const meta=(d:Dataset)=>{const key=filenameKey(d);return nameCounts.get(key)===1?metadata.data?.[key]:undefined;};
  const detail=(d:Dataset)=>meta(d)?.label || "";
  const candidates=(standalone?audio:videos).filter(d=>!pairs.some(p=>(standalone?p.audio_id:p.video_id)===d.id));
  const visible=candidates.filter(d=>`${name(d)} ${d.original_filename} ${detail(d)} ${Object.values(meta(d)?.values || {}).join(" ")}`.toLocaleLowerCase().includes(filter.toLocaleLowerCase()));
  function suggest() {
    const stem=(d:Dataset)=>d.original_filename.replace(/\.[^.]+$/, "").toLocaleLowerCase();
    setPairs(old=>old.map(pair=>{const video=standalone?audio.find(d=>d.id===pair.audio_id):videos.find(d=>d.id===pair.video_id);if(!video)return pair;
      const a=audio.filter(d=>stem(d)===stem(video)),t=texts.filter(d=>stem(d)===stem(video));
      const nextAudio=pair.audio_id || (a.length===1?a[0].id:"");
      return {...pair,audio_id:nextAudio,text_id:pair.text_id || (t.length===1?t[0].id:""),...(nextAudio!==pair.audio_id && !standalone?{audio_offset_seconds:"0",content_reviewed:false}:{})};}));
  }
  async function save() {
    setBusy(true);setError("");
    try {
      if (pairs.some(p => p.video_id && p.audio_id && p.audio_offset_seconds.trim() && !Number.isFinite(Number(p.audio_offset_seconds)))) throw new Error("请填写有效的时间偏移。");
      if (beforeSave && !(await beforeSave())) throw new Error("请先修正处理选段。");
      const saved=await apiFetch<{id:string}>(`/api/v1/datasets/${initial ? dataset.id : pairs[0].video_id || pairs[0].audio_id}/media-association`,{method:"POST",body:JSON.stringify({task_id:taskId,purpose:initial?"legacy":"temporary",...(!initial && ownerCase?{owner_case_collection_id:ownerCase.collectionId,owner_case_sample_id:ownerCase.sampleId}:{}),samples:pairs.map(p=>({...p,video_id:p.video_id || null,audio_id:p.audio_id || null,text_id:p.text_id || null,audio_offset_seconds:p.video_id && p.audio_id && p.audio_offset_seconds.trim() ? Number(p.audio_offset_seconds) : null,content_reviewed:!!(p.video_id && p.audio_id && p.content_reviewed)}))})});
      await Promise.all([client.invalidateQueries({queryKey:["datasets"]}),client.invalidateQueries({queryKey:["workspace-files"]})]);
      if(!embedded)setOpened(false);
      onChoose(saved.id);
    } catch(reason){setError(reason instanceof Error?reason.message:"无法保存关联。");} finally {setBusy(false);}
  }
  return <section className={styles.association}>
    {!opened?<button className="rw-primary" onClick={()=>setOpened(true)}>{initial?"修改材料关联":standalone?"声音特征":"声音特征与取帧"}</button>:<>
      <div className={styles.controls}><strong>选择材料</strong><button disabled={busy} onClick={suggest}>建议同名配对</button>{!embedded&&<button onClick={()=>setOpened(false)}>收起</button>}</div>
      {pairs.some(p=>p.video_id && p.audio_id) && <p>独立 WAV 时间 = 视频时间 + 偏移秒数</p>}
      <details className={styles.bulk}><summary>批量搜索并选择{standalone?"录音":"视频"}</summary>
        <label>搜索文件名或元信息 <input type="search" value={filter} onChange={e=>setFilter(e.target.value)} /></label>
        <div className={styles.controls}><button type="button" disabled={busy || !visible.length} onClick={()=>setMarked(old=>Array.from(new Set([...old,...visible.map(d=>d.id)])))}>选择筛选结果</button><button type="button" disabled={busy || !marked.length} onClick={()=>setMarked([])}>清除勾选</button><button type="button" disabled={busy || !marked.length} onClick={()=>{const chosen=candidates.filter(d=>marked.includes(d.id));setPairs(old=>[...old,...chosen.map(d=>({video_id:standalone?"":d.id,audio_id:standalone?d.id:"",text_id:"",audio_offset_seconds:standalone?"":"0",content_reviewed:false}))]);setMarked([]);}}>添加已勾选（{marked.length}）</button></div>
        <div className={styles.candidates}>{visible.map(d=><label key={d.id}><input type="checkbox" disabled={busy} checked={marked.includes(d.id)} onChange={e=>setMarked(old=>e.target.checked?[...old,d.id]:old.filter(id=>id!==d.id))}/><span>{detail(d) || name(d)} <small>（{d.original_filename}）</small></span></label>)}{!visible.length && <p>没有可添加的匹配材料。</p>}</div>
        {metadata.isError && <small>元信息未能读取，仍可按文件名选择。</small>}
      </details>
      <div className="rw-table-scroll"><table className="rw-data-table"><thead><tr>{!standalone && <th>视频</th>}<th>{standalone?"录音":"独立音频（可选）"}</th><th>文本（可选）</th>{initial && <th>案例名称与其他材料</th>}{!standalone && <><th>WAV 偏移（秒）</th><th>内容已人工校对</th></>}<th/></tr></thead><tbody>{pairs.map((pair,i)=><tr key={i}>
        {([["video_id",videos],["audio_id",audio],["text_id",texts]] as const).filter(([key])=>!standalone || key!=="video_id").map(([key,files])=><td key={key}><SearchSelect aria-label={`${key==="video_id"?"视频":key==="audio_id"?"独立音频":"文本"} ${i+1}`} value={pair[key]} disabled={busy} onChange={e=>setPairs(old=>old.map((p,j)=>j===i?{...p,[key]:e.target.value,...(key!=="text_id" && e.target.value!==p[key]?{audio_offset_seconds:"0",content_reviewed:false}:{})}:p))}>{key!=="video_id" && !(standalone && key==="audio_id") && <option value="">不关联</option>}{files.map(d=><option key={d.id} value={d.id}>{detail(d) ? `${detail(d)} · ${d.original_filename}` : name(d)}</option>)}</SearchSelect></td>)}
        {initial && <td><label>案例名称<input aria-label={`案例名称 ${i+1}`} value={pair.case_title || ""} onChange={e=>setPairs(old=>old.map((p,j)=>j===i?{...p,case_title:e.target.value}:p))} placeholder="可选" /></label><details><summary>其他材料（{pair.member_ids?.length || 0}）</summary><p>材料归属案例，不表示时间同步。</p><div className={styles.candidates}>{caseMaterials.map(d=><label key={d.id}><input type="checkbox" checked={pair.member_ids?.includes(d.id) || false} disabled={busy} onChange={e=>setPairs(old=>old.map((p,j)=>j===i?{...p,member_ids:e.target.checked?[...(p.member_ids || []),d.id]:(p.member_ids || []).filter(id=>id!==d.id)}:p))}/>{name(d)}</label>)}</div></details></td>}
        {!standalone && <><td><input type="number" step="0.001" aria-label={`WAV 偏移 ${i+1}`} disabled={busy || !pair.video_id || !pair.audio_id} value={pair.audio_offset_seconds} placeholder="旧版同秒" onChange={e=>setPairs(old=>old.map((p,j)=>j===i?{...p,audio_offset_seconds:e.target.value}:p))}/></td><td><input type="checkbox" aria-label={`内容已人工校对 ${i+1}`} disabled={busy || !pair.video_id || !pair.audio_id} checked={pair.content_reviewed} onChange={e=>setPairs(old=>old.map((p,j)=>j===i?{...p,content_reviewed:e.target.checked}:p))}/></td></>}
        <td>{pairs.length>1 && <button disabled={busy} onClick={()=>setPairs(pairs.filter((_,j)=>j!==i))}>移除此行</button>}</td></tr>)}</tbody></table></div>
      <div className={styles.controls}><button disabled={busy || pairs.length>=(standalone?audio:videos).length} onClick={()=>{const d=(standalone?audio:videos).find(d=>!pairs.some(p=>(standalone?p.audio_id:p.video_id)===d.id));if(d)setPairs([...pairs,{video_id:standalone?"":d.id,audio_id:standalone?d.id:"",text_id:"",audio_offset_seconds:standalone?"":"0",content_reviewed:false}]);}}>{standalone?"添加录音":"添加视频"}</button><button className="rw-primary" disabled={busy} onClick={()=>void save()}>{busy?"正在保存…":"保存并继续"}</button></div>
      {error && <p role="alert">{error}</p>}
    </>}
  </section>;
}
