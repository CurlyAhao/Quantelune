"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Dataset } from "@/lib/types";
import { apiFetch } from "@/lib/api-client";
import type { WorkspaceInventory } from "@/lib/workspace-files";
import { SearchSelect } from "./ui/search-select";

export type CaseSourceContext = {datasetId:string; collectionId:string; sampleId:string; start?:number; end?:number; offset?:number; audioSource?:"video"|"independent"};
export function workspaceCaseEntries(datasets: Dataset[], sessions: WorkspaceInventory["sessions"]) {
  const seen=new Set<string>();
  return [...datasets].filter(d=>d.upload_status!=="deleted" && d.media_case?.kind==="media" && !d.media_case.survey && d.media_case.purpose!=="temporary").sort((a,b)=>b.created_at.localeCompare(a.created_at)).flatMap(d=>{
    const group=sessions.find(s=>s.id===d.id)?.group_id || d.id;
    return (d.media_case?.samples || []).filter(sample=>sample.case_title?.trim()).filter(sample=>{const key=group+":"+sample.sample_id;if(seen.has(key))return false;seen.add(key);return true;}).map(sample=>({d,sample}));
  });
}

type CaseEntries = ReturnType<typeof workspaceCaseEntries>;

export function WorkspaceSelectionActions({datasets, cases, selectedIds, busy, setBusy, onComplete}: {
  datasets: Dataset[];
  cases: CaseEntries;
  selectedIds: string[];
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onComplete: (datasetId: string) => Promise<void>;
}) {
  const [name,setName]=useState("");
  const [adding,setAdding]=useState(false);
  const [caseChoice,setCaseChoice]=useState("");
  const [error,setError]=useState("");
  const selected=selectedIds.map(id=>datasets.find(d=>d.id===id)).filter((d):d is Dataset=>!!d && d.upload_status!=="deleted");
  const caseEligible=selected.length===selectedIds.length && selected.length>0 && selected.every(d=>/^(pdf|txt|md|markdown|png|jpg|jpeg|gif|webp|wav|mp4)$/i.test(d.file_format));
  const mediaEligible=selected.length===selectedIds.length && selected.length>0 && selected.every(d=>/^(wav|mp4)$/i.test(d.file_format));
  async function create(purpose:"case"|"temporary") {
    if(busy || !selected.length)return;
    setError("");setBusy(true);
    try {
      const samples=purpose==="case" ? [{primary_id:selected[0].id,case_title:name.trim(),member_ids:selected.map(d=>d.id)}] : selected.map(d=>({video_id:d.file_format.toLowerCase()==="mp4"?d.id:null,audio_id:d.file_format.toLowerCase()==="wav"?d.id:null}));
      const result=await apiFetch<{id:string}>(`/api/v1/datasets/${selected[0].id}/media-association`,{method:"POST",body:JSON.stringify({purpose,samples})});
      await onComplete(result.id);
    } catch(reason) {setError(reason instanceof Error?reason.message:"无法处理所选材料。");} finally {setBusy(false);}
  }
  async function addToCase() {
    const chosen=cases.find(({d,sample})=>`${d.id}:${sample.sample_id}`===caseChoice);
    if(!chosen || !caseEligible || busy)return;
    setBusy(true);setError("");
    try {
      const rows=(chosen.d.media_case?.samples || []).filter(s=>!s.case_deleted).map(s=>({
        sample_id:s.sample_id,primary_id:s.primary_id || null,video_id:s.video_id || null,
        audio_id:s.audio_id || null,text_id:s.text_id || null,pitch_id:s.pitch_id || null,
        pitch_offset_seconds:s.pitch_alignment?.offset_seconds ?? null,
        case_title:s.case_title || "",audio_offset_seconds:s.audio_alignment?.offset_seconds ?? null,
        content_reviewed:!!s.audio_alignment?.content_reviewed,
        member_ids:Array.from(new Set([...(s.members || []).map(m=>m.id),...(s.sample_id===chosen.sample.sample_id?selectedIds:[])])),
      }));
      const next=await apiFetch<{id:string}>(`/api/v1/datasets/${chosen.d.id}/media-association`,{method:"POST",body:JSON.stringify({samples:rows})});
      await onComplete(next.id);
    } catch(reason) {setError(reason instanceof Error?reason.message:"无法归入案例。");} finally {setBusy(false);}
  }
  return <div className="rw-case-fields rw-selection-actions" aria-label="所选文件的操作"><small>已选 {selectedIds.length} 项</small>
        <div className="rw-inline-actions"><button disabled={busy || !mediaEligible} onClick={()=>void create("temporary")}>一起分析</button><button disabled={busy || !caseEligible} aria-expanded={adding} onClick={()=>{setAdding(!adding);if(!adding)setName(selected.length===1?selected[0].original_filename:"");}}>加入案例</button></div>
        {!mediaEligible && <small>一起分析支持所选音频和视频。</small>}
        {adding && <><label>新案例名称 <input value={name} onChange={e=>setName(e.target.value)} /></label>
        <button disabled={busy || !caseEligible || !name.trim()} onClick={()=>void create("case")}>保存新案例</button>
        {cases.length>0 && <><label>已有案例 <SearchSelect aria-label="搜索已有案例" value={caseChoice} onChange={e=>setCaseChoice(e.target.value)}><option value="">搜索案例名称</option>{cases.map(({d,sample})=><option key={d.id+":"+sample.sample_id} value={d.id+":"+sample.sample_id}>{sample.case_title}</option>)}</SearchSelect></label><button disabled={busy || !caseEligible || !caseChoice} onClick={()=>void addToCase()}>加入已有案例</button></>}</>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function WorkspaceCases({datasets, cases, onOpen}: {
  datasets: Dataset[];
  cases: CaseEntries;
  onOpen: (id: string, context?: CaseSourceContext) => void;
}) {
  const client = useQueryClient();
  const [error, setError] = useState("");
  async function openCase(id:string,sampleId:string) {
    setError("");
    try {const state=await apiFetch<{selection?:{sample_id?:string}}>(`/api/v1/datasets/${id}/samples`);if(state.selection?.sample_id!==sampleId)await apiFetch(`/api/v1/datasets/${id}/sample-selection`,{method:"POST",body:JSON.stringify({sample_id:sampleId,start:null,end:null})});await client.invalidateQueries({queryKey:["media-samples",id]});onOpen(id,{datasetId:id,collectionId:id,sampleId});} catch(reason){setError(reason instanceof Error?reason.message:"无法打开案例。");}
  }
  async function openMember(d:Dataset,sample:NonNullable<NonNullable<Dataset["media_case"]>["samples"]>[number],id:string) {
    setError("");
    try {
      const state=await apiFetch<{selection?:{sample_id?:string;start?:number|null;end?:number|null}}>(`/api/v1/datasets/${d.id}/samples`);
      const same=state.selection?.sample_id===sample.sample_id;
      if(!same) await apiFetch(`/api/v1/datasets/${d.id}/sample-selection`,{method:"POST",body:JSON.stringify({sample_id:sample.sample_id,start:null,end:null})});
      const isVideo=sample.video_id===id;
      const isAudio=sample.audio_id===id;
      const offset=isAudio && sample.video_id ? sample.audio_alignment?.offset_seconds ?? 0 : 0;
      const memberRange=sample.members?.find(member=>member.id===id)?.range;
      onOpen(id,{datasetId:id,collectionId:d.id,sampleId:sample.sample_id,
        ...((isVideo || isAudio) ? {audioSource:isAudio?"independent":"video",offset,
          ...(same && state.selection?.start!=null && state.selection.end!=null ? {start:state.selection.start+offset,end:state.selection.end+offset} : {})} : {}),
        ...(memberRange ? {start:memberRange.start,end:memberRange.end} : {})});
    } catch(reason) {setError(reason instanceof Error?reason.message:"无法打开案例材料。");}
  }
  return <section className="rw-output-tree" aria-label="案例">
    <details open><summary>案例 <small>{cases.length}</small></summary>
      {error && <p role="alert">{error}</p>}
      <ul>{cases.map(({d,sample})=>{
        const ids=Array.from(new Set([sample.primary_id,sample.video_id,sample.audio_id,sample.text_id,...Object.values(sample.files || {}).map(f=>f.id),...(sample.members || []).map(m=>m.id)].filter((id):id is string=>!!id)));
        return <li key={d.id+":"+sample.sample_id}><details><summary><button onClick={e=>{e.preventDefault();void openCase(d.id,sample.sample_id);}}>{sample.case_title}</button></summary><ul>{ids.map(id=>{const file=datasets.find(item=>item.id===id);const range=sample.members?.find(member=>member.id===id)?.range;return <li key={id}>{file && file.upload_status!=="deleted"?<button title={file.source_path || file.original_filename} onClick={()=>void openMember(d,sample,id)}>{file.original_filename}{range && <small> · {range.start}–{range.end} 秒</small>}</button>:<span>材料不可用</span>}</li>;})}</ul></details></li>;
      })}</ul>
    </details>
  </section>;
}
