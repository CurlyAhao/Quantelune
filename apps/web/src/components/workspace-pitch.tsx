"use client";

import {useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";
import type {Dataset} from "@/lib/types";
import {SearchSelect} from "./ui/search-select";
import {MediaResultView} from "./workspace-media-results";
import {MediaOwnership} from "./workspace-result-navigation";

export type NativePitch={source_id:string;clock:{xmin:number;xmax:number;dx:number;x1:number};association?:{group_id:string;audio_id:string;name:string;offset_seconds:number}|null};

export function WorkspacePitch({dataset,datasets,onChoose,taskId}:{dataset:Dataset;datasets:Dataset[];onChoose:(id:string)=>void;taskId?:string}) {
  const client=useQueryClient();
  const opened=useQuery({queryKey:["native-pitch",dataset.id,dataset.sha256],retry:false,staleTime:Infinity,queryFn:async({signal})=>{
    const item=await apiFetch<{package_id:string}>(`/api/v1/datasets/${dataset.id}/pitch-object`,{method:"POST",signal});
    const result=await apiFetch<Dataset>(`/api/v1/datasets/${item.package_id}`,{signal});
    await client.invalidateQueries({queryKey:["datasets"]});return result;
  }});
  if(opened.isPending)return <p role="status">正在读取基频…</p>;
  if(opened.isError)return <p role="alert">{opened.error.message} <button onClick={()=>void opened.refetch()}>重试</button></p>;
  const rows=datasets.some(item=>item.id===opened.data.id)?datasets:[...datasets,opened.data];
  return <><MediaOwnership dataset={opened.data} datasets={rows} onChoose={onChoose} taskId={taskId}/><MediaResultView dataset={opened.data} datasets={rows} onChoose={onChoose} taskId={taskId}/></>;
}

export function PitchAudioAssociation({packageId,object,datasets}:{packageId:string;object:NativePitch;datasets:Dataset[]}) {
  const [audio,setAudio]=useState(object.association?.audio_id||""),[offset,setOffset]=useState(object.association?String(object.association.offset_seconds):"");
  const [busy,setBusy]=useState(false),[error,setError]=useState("");const client=useQueryClient();
  async function save(){setBusy(true);setError("");try{
    await apiFetch(`/api/v1/datasets/${packageId}/pitch-association`,{method:"POST",body:JSON.stringify({audio_id:audio,offset_seconds:Number(offset)})});
    await Promise.all([client.invalidateQueries({queryKey:["media-result",packageId]}),client.invalidateQueries({queryKey:["datasets"]})]);
  }catch(reason){setError(reason instanceof Error?reason.message:"无法保存关联。");}finally{setBusy(false);}}
  return <details><summary>材料关联{object.association?` · ${object.association.name}`:" · 选择原音轨"}</summary>
    <div className="rw-inline-actions"><label>原音轨<SearchSelect aria-label="Pitch 原音轨" value={audio} onChange={e=>setAudio(e.target.value)}><option value="">选择 WAV</option>{datasets.filter(item=>item.file_format==="wav"&&item.raw_file&&!item.media_result).map(item=><option key={item.id} value={item.id}>{item.display_name||item.original_filename}</option>)}</SearchSelect></label>
      <label>时间偏移（秒）<input aria-label="Pitch 时间偏移" type="number" step="any" value={offset} onChange={e=>setOffset(e.target.value)}/></label>
      <button disabled={busy||!audio||!offset.trim()||!Number.isFinite(Number(offset))} onClick={()=>void save()}>{busy?"正在保存…":"保存关联"}</button></div>
    <p>音轨时间 = Pitch 时间 + 偏移。</p>{error&&<p role="alert">{error}</p>}
  </details>;
}
