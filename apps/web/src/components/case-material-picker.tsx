"use client";

import { useEffect, useState } from "react";
import type { CaseSourceContext } from "./workspace-cases";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import type { Dataset } from "@/lib/types";
import type { WorkspaceInventory } from "@/lib/workspace-files";
import { SearchSelect } from "@/components/ui/search-select";

type Sample = {sample_id:string;case_deleted?:boolean;title:string;case_title?:string;primary_id?:string;video_id?:string;audio_id?:string;text_id?:string;pitch_id?:string;pitch_alignment?:{offset_seconds:number};members?:{id:string;sha256:string}[];audio_alignment?:{offset_seconds:number|null;content_reviewed:boolean};files?:Record<string,{id?:string;sha256?:string}>};
export function CaseMaterialPicker({dataset,datasets,taskId,children,onCaseChange,compact=false,onOpenCase,currentRange}: {dataset:Dataset;datasets:Dataset[];taskId?:string;onCaseChange?:(value:CaseSourceContext|undefined)=>void;children:(sampleId?:string,collectionId?:string)=>React.ReactNode;compact?:boolean;onOpenCase?:(id:string,sampleId:string)=>void;currentRange?:{start:number;end:number}}) {
  const client=useQueryClient();
  const [group,setGroup]=useState(""),[sample,setSample]=useState(""),[title,setTitle]=useState(dataset.original_filename),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [creating,setCreating]=useState(false);
  const [saveRange,setSaveRange]=useState(false);
  const inventory=useQuery({queryKey:["workspace-files",dataset.project_id],queryFn:({signal})=>apiFetch<WorkspaceInventory>(`/api/v1/projects/${dataset.project_id}/workspace-files`,{signal})});
  const seen=new Set<string>();
  const choices=[...datasets].filter(item=>item.media_case?.kind==="media" && item.media_case.purpose!=="temporary" && !item.media_case.survey && item.upload_status!=="deleted").sort((a,b)=>b.created_at.localeCompare(a.created_at)).flatMap(item=>{
    const identity=inventory.data?.sessions.find(row=>row.id===item.id)?.group_id || item.id;
    return (item.media_case?.samples || []).filter(row=>!!row.case_title && !row.case_deleted).filter(row=>{const key=identity+":"+row.sample_id;if(seen.has(key))return false;seen.add(key);return true;}).map(row=>({group:item.id,sample:row.sample_id,name:row.case_title!,created:item.created_at,linked:([[row.video_id,"video_av"],[row.audio_id,"audio"],[row.text_id,"text"]] as const).some(([id,key])=>id===dataset.id && (!row.files?.[key] || row.files[key].sha256===dataset.sha256)) || !!row.members?.some(member=>member.id===dataset.id && member.sha256===dataset.sha256)}));
  });
  const items=useQuery({queryKey:["media-samples",group,taskId],queryFn:({signal})=>apiFetch<{samples:Sample[]}>(`/api/v1/datasets/${group}/samples${taskId?`?task_id=${encodeURIComponent(taskId)}`:""}`,{signal}),enabled:!!group});
  const selected=items.data?.samples.find(item=>item.sample_id===sample && !item.case_deleted);
  const linked=!!selected && (([[selected.video_id,"video_av"],[selected.audio_id,"audio"],[selected.text_id,"text"]] as const).some(([id,key])=>id===dataset.id && (!selected.files?.[key] || selected.files[key].sha256===dataset.sha256)) || !!selected.members?.some(member=>member.id===dataset.id && member.sha256===dataset.sha256));
  useEffect(()=>{onCaseChange?.(linked ? {datasetId:dataset.id,collectionId:group,sampleId:sample} : undefined);},[onCaseChange,linked,dataset.id,group,sample]);
  async function save(create:boolean) {
    if(busy)return;setBusy(true);setError("");
    try {
      if(create && !title.trim())throw new Error("请填写案例名称。");
      if(!create && !selected)throw new Error("请选择案例。");
      if(saveRange && compact) {
        if(!currentRange || currentRange.start<0 || currentRange.end<=currentRange.start)throw new Error("请先选择有效的起止时间。");
        await apiFetch(`/api/v1/datasets/${dataset.id}/original-time-range`,{method:"PUT",body:JSON.stringify({task_id:taskId,sha256:dataset.sha256,...currentRange})});
        await client.invalidateQueries({queryKey:["original-time-range",dataset.id]});
      }
      const rows=create?[{primary_id:dataset.id,case_title:title.trim(),member_ids:[dataset.id],range_member_id:saveRange?dataset.id:null}]:(items.data?.samples || []).filter(item=>!item.case_deleted).map(item=>({sample_id:item.sample_id,primary_id:item.primary_id || null,video_id:item.video_id || null,audio_id:item.audio_id || null,text_id:item.text_id || null,pitch_id:item.pitch_id || null,pitch_offset_seconds:item.pitch_alignment?.offset_seconds ?? null,case_title:item.case_title || "",range_member_id:saveRange && item.sample_id===sample ? dataset.id:null,member_ids:Array.from(new Set([...(item.members || []).map(member=>member.id),...(item.sample_id===sample?[dataset.id]:[])])),audio_offset_seconds:item.audio_alignment?.offset_seconds ?? null,content_reviewed:!!item.audio_alignment?.content_reviewed}));
      const next=await apiFetch<{id:string;sample_ids:string[]}>(`/api/v1/datasets/${create?dataset.id:group}/media-association`,{method:"POST",body:JSON.stringify({task_id:taskId,purpose:create?"case":"legacy",samples:rows})});
      await Promise.all([client.invalidateQueries({queryKey:["datasets"]}),client.invalidateQueries({queryKey:["workspace-files"]})]);
      setGroup(next.id);setSample(create?next.sample_ids[0]:sample);setCreating(false);
    } catch(reason){setError(reason instanceof Error?reason.message:"无法保存案例关联。");}finally{setBusy(false);}
  }
  async function openCase(groupId:string,sampleId:string) {
    setError("");
    try {
      const state=await apiFetch<{selection?:{sample_id?:string}}>(`/api/v1/datasets/${groupId}/samples${taskId?`?task_id=${encodeURIComponent(taskId)}`:""}`);
      if(state.selection?.sample_id!==sampleId) await apiFetch(`/api/v1/datasets/${groupId}/sample-selection`,{method:"POST",body:JSON.stringify({task_id:taskId,sample_id:sampleId,start:null,end:null})});
      await client.invalidateQueries({queryKey:["media-samples",groupId]});
      onOpenCase?.(groupId,sampleId);
    } catch(reason) { setError(reason instanceof Error ? reason.message : "无法打开案例。"); }
  }
  if(compact) return <section aria-label="案例归属"><h3>案例归属</h3>
    {choices.some(item=>item.linked) ? choices.filter(item=>item.linked).map(item=><div key={item.group+":"+item.sample}><button type="button" onClick={()=>void openCase(item.group,item.sample)}>{item.name}</button>{choices.filter(choice=>choice.name===item.name).length>1 && <small> · {new Date(item.created).toLocaleString("zh-CN")}</small>}</div>) : <p>未加入案例</p>}
    <details><summary>{choices.some(item=>item.linked)?"加入其他案例":"加入案例"}</summary>
      <div className="rw-inline-actions"><button type="button" aria-pressed={!creating} onClick={()=>setCreating(false)}>选择已有案例</button><button type="button" aria-pressed={creating} onClick={()=>{setTitle(dataset.original_filename);setCreating(true);}}>创建新案例</button></div>
      {/^(mp4|wav)$/i.test(dataset.file_format) && <label><input type="checkbox" checked={saveRange} onChange={event=>setSaveRange(event.target.checked)}/> 关联当前选段</label>}
      {!creating ? <div className="rw-case-fields"><label>案例 <SearchSelect aria-label="搜索已有案例名称" value={group && sample ? JSON.stringify([group,sample]) : ""} onChange={event=>{if(!event.target.value){setGroup("");setSample("");return;}const value=JSON.parse(event.target.value) as string[];setGroup(value[0]);setSample(value[1]);}}><option value="">搜索案例名称</option>{choices.map(item=><option key={JSON.stringify([item.group,item.sample])} value={JSON.stringify([item.group,item.sample])}>{item.name}</option>)}</SearchSelect></label>
        {selected && !linked && <button type="button" disabled={busy} onClick={()=>void save(false)}>将此文件归入案例</button>}{linked && <span>已关联</span>}{items.isError && <p role="alert">{items.error.message}</p>}</div> : <div className="rw-case-fields"><label>案例名称 <input value={title} onChange={event=>setTitle(event.target.value)} /></label><button type="button" disabled={busy || !title.trim()} onClick={()=>void save(true)}>创建并关联此文件</button></div>}
    </details>{error && <p role="alert">{error}</p>}
  </section>;
  return <div className="rw-material-layout"><div className="rw-material-main">{children(linked?sample:undefined,linked?group:undefined)}</div><aside className="rw-material-context"><details open className="rw-case-picker"><summary>材料与案例{linked && selected ? ` · ${selected.case_title || selected.title}` : ""}</summary><div className="rw-inline-actions"><button type="button" aria-pressed={!creating} onClick={()=>setCreating(false)}>选择已有案例</button><button type="button" aria-pressed={creating} onClick={()=>{setTitle(dataset.original_filename);setCreating(true);}}>创建新案例</button></div>{/^(mp4|wav)$/i.test(dataset.file_format) && <label><input type="checkbox" checked={saveRange} onChange={event=>setSaveRange(event.target.checked)}/> 关联当前选段</label>}<div hidden={creating}><div className="rw-case-fields"><label>案例 <SearchSelect aria-label="搜索已有案例名称" value={group && sample ? JSON.stringify([group,sample]) : ""} onChange={event=>{if(!event.target.value){setGroup("");setSample("");return;}const value=JSON.parse(event.target.value) as string[];setGroup(value[0]);setSample(value[1]);}}><option value="">搜索案例名称</option>{choices.map(item=><option key={JSON.stringify([item.group,item.sample])} value={JSON.stringify([item.group,item.sample])}>{item.name}</option>)}</SearchSelect></label>
    {selected && !linked && <button type="button" disabled={busy} onClick={()=>void save(false)}>将此文件归入案例</button>}{linked && <span>已关联</span>}</div>
    {items.isError && <p role="alert">{items.error.message}</p>}</div><div hidden={!creating}><div className="rw-case-fields"><label>案例名称 <input value={title} onChange={event=>setTitle(event.target.value)} /></label><button type="button" disabled={busy || !title.trim()} onClick={()=>void save(true)}>创建并关联此文件</button></div></div>{error && <p role="alert">{error}</p>}</details></aside></div>;
}
