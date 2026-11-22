"use client";
import { useState } from "react";
import { projectAssistantApi } from "@/lib/project-assistant-api";

export function PlotAgentRequest({project,taskId,runId,revision,datasetId}:{project:string;taskId?:string;runId:string;revision:number;datasetId?:string|null}) {
  const [instruction,setInstruction]=useState(""),[pending,setPending]=useState(false),[error,setError]=useState("");
  async function send() {
    if(pending || !instruction.trim()) return;
    const context=taskId || "project";
    setPending(true);setError("");
    window.dispatchEvent(new CustomEvent("statforge:show-agent",{detail:{project,context}}));
    try {
      const saved=await projectAssistantApi.send(project,{request_id:crypto.randomUUID(),context_key:context,dataset_id:datasetId,
        figure_target:{id:runId,revision},question:instruction.trim()});
      sessionStorage.setItem(`statforge:material-conversation:${project}:${context}`,saved.id);
      window.dispatchEvent(new CustomEvent("statforge:material-conversation",{detail:{project,context,id:saved.id}}));
      setInstruction("");
    } catch(reason){setError(reason instanceof Error?reason.message:"无法开始调整，请重试。");}
    finally{setPending(false);}
  }
  return <div className="rw-plot-agent-request"><label><span className="sr-only">AI 图形调整要求</span><input value={instruction} onChange={e=>setInstruction(e.target.value)} maxLength={1500} placeholder="希望怎样调整这张图？" /></label><button type="button" disabled={pending||!instruction.trim()} onClick={()=>void send()}>{pending?"正在发送…":"发送至 Agent"}</button>{error&&<p role="alert">{error}</p>}</div>;
}
