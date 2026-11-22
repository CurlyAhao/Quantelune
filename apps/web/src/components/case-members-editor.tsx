"use client";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import type { Dataset } from "@/lib/types";

type Case = {sample_id:string;title:string;primary_id?:string;case_title?:string;members?:{id:string;sha256:string}[]};
export function CaseMembersEditor({dataset,datasets,taskId,cases,onChoose}: {dataset:Dataset;datasets:Dataset[];taskId?:string;cases:Case[];onChoose:(id:string)=>void}) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [rows,setRows]=useState(()=>cases.map(item=>({sample_id:item.sample_id,primary_id:item.primary_id || item.sample_id,case_title:item.case_title || "",member_ids:(item.members || []).map(member=>member.id)})));
  const client=useQueryClient();
  const candidates=datasets.filter(item=>!item.media_result && item.upload_status!=="deleted" && /^(pdf|txt|md|markdown|png|jpg|jpeg|gif|webp|wav|mp4)$/i.test(item.file_format));
  async function save(){setBusy(true);setError("");try{const result=await apiFetch<{id:string}>(`/api/v1/datasets/${dataset.id}/media-association`,{method:"POST",body:JSON.stringify({task_id:taskId,samples:rows})});await Promise.all([client.invalidateQueries({queryKey:["datasets"]}),client.invalidateQueries({queryKey:["workspace-files"]})]);onChoose(result.id);}catch(reason){setError(reason instanceof Error?reason.message:"无法保存案例。");}finally{setBusy(false);}}
  return <section>{!open?<button onClick={()=>setOpen(true)}>修改案例材料</button>:<>{rows.map((row,index)=><fieldset key={row.primary_id}><legend>案例 {index+1}</legend><label>案例名称 <input value={row.case_title} onChange={event=>setRows(old=>old.map((item,i)=>i===index?{...item,case_title:event.target.value}:item))}/></label><div>{candidates.map(item=><label key={item.id} style={{display:"block"}}><input type="checkbox" checked={row.member_ids.includes(item.id)} disabled={busy || item.id===row.primary_id} onChange={event=>setRows(old=>old.map((value,i)=>i===index?{...value,member_ids:event.target.checked?[...value.member_ids,item.id]:value.member_ids.filter(id=>id!==item.id)}:value))}/>{item.display_name || item.original_filename}</label>)}</div></fieldset>)}<button disabled={busy} onClick={()=>void save()}>保存案例</button><button disabled={busy} onClick={()=>setOpen(false)}>取消</button>{error && <p role="alert">{error}</p>}</>}</section>;
}
