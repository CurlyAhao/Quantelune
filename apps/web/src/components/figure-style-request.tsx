"use client";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";

export function FigureStyleRequest<T>({project,id,revision,preview,multiline=false}:{project:string;id:string;revision:number;preview:(style:T)=>void|Promise<void>;multiline?:boolean}) {
  const [instruction,setInstruction]=useState(""),[pending,setPending]=useState(false),[error,setError]=useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => { setPending(false); setError(""); setInstruction(""); return () => active.current?.abort(); }, [project, id, revision]);
  async function request() {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setPending(true);setError("");
    try {
      const result=await apiFetch<{style:T}>(`/api/v1/projects/${project}/assistant/figures/${id}/style-preview`,{method:"POST",signal:controller.signal,body:JSON.stringify({revision,instruction})});
      if (!controller.signal.aborted) await preview(result.style);
    } catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:"未能生成样式预览。");}
    finally{if(!controller.signal.aborted)setPending(false);}
  }
  return <div className={multiline?"qp-style-form":"rw-inline-actions"}>{multiline ?
    <label>怎样调整这张图？<textarea rows={3} maxLength={1500} value={instruction} onChange={e=>setInstruction(e.target.value)} placeholder="例如：增大横轴刻度的字号"/></label> :
    <input aria-label="图形样式要求" placeholder="想怎样调整这幅图？" maxLength={1500} value={instruction} onChange={e=>setInstruction(e.target.value)}/>}
    <button disabled={pending||!instruction.trim()} onClick={()=>void request()}>{pending?"正在生成…":multiline?"预览调整":"AI 调整样式"}</button>{error&&<span role="alert">{error}</span>}</div>;
}
