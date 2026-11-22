"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Popover } from "@base-ui/react/popover";
import { SparklesIcon, XIcon } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { projectAssistantApi, type AssistantResult } from "@/lib/project-assistant-api";
import type { Dataset } from "@/lib/types";
import { useWorkspaceAnnotations } from "./workspace-annotations";

import { workspaceApi } from "@/lib/workspace-api";

import { DesktopLink } from "./desktop-link";
import { openMaterialResult } from "@/lib/open-material-result";

type Item = { action: string; label: string; question: string; description?:string };
export type ResultSelection={sample_id:string;start?:number;end?:number};
export const AdviceHeaderSlot = createContext<HTMLElement | null>(null);
type AdviceProps = {dataset:Dataset;taskId?:string;version:string;onChoose:(id:string)=>void;onPlot?:(draft:string,run:string)=>void;resultSelection?:ResultSelection;onReviewSource?:()=>void;onSummary?:()=>void;onAnnotate?:()=>void};

export function MaterialSuggestions(props: AdviceProps) {
  const host = useContext(AdviceHeaderSlot);
  if (!host) return null;
  return createPortal(<AdvicePopover key={[props.dataset.id, props.taskId, props.version].join(":")} {...props} host={host}/>, host);
}

function AdvicePopover({dataset,taskId,version,onChoose,onPlot,resultSelection,onReviewSource,onSummary,onAnnotate,host}:AdviceProps & {host:HTMLElement}) {
  const [open,setOpen]=useState(false);
  const client=useQueryClient(), annotations=useWorkspaceAnnotations();
  const project=dataset.project_id, base=`/api/v1/projects/${project}/assistant/suggestions`;
  const selectionKey=JSON.stringify(resultSelection||null);
  const [settled,setSettled]=useState(selectionKey);
  useEffect(()=>{const timer=setTimeout(()=>setSettled(selectionKey),650);return()=>clearTimeout(timer);},[selectionKey]);
  const preference=["material-advice-enabled",project], key=["material-advice",project,taskId,dataset.id,dataset.sha256,version,settled];

  const pref=useQuery({queryKey:preference,enabled:open,queryFn:({signal})=>apiFetch<{enabled:boolean}>(base+"/preference",{signal}),staleTime:Infinity});
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[turn,setTurn]=useState("");
  const context = taskId || "project";
  const recentKey = ["material-result", project, context, dataset.id, dataset.sha256];
  const recent = useQuery({
    queryKey: recentKey,
    enabled: open,
    queryFn: ({ signal }) => projectAssistantApi.materialResult(project, dataset.id, context, signal),
    refetchOnWindowFocus: false,
  });
  const advice=useQuery({queryKey:key,enabled:open && pref.data?.enabled === true && recent.isSuccess && settled===selectionKey,staleTime:Infinity,retry:false,
    queryFn:({signal})=>apiFetch<{items:Item[]}>(base,{method:"POST",body:JSON.stringify({dataset_id:dataset.id,task_id:taskId,...JSON.parse(settled||"null")}),signal})});
  const chat=useQuery({queryKey:["material-action",project,turn],enabled:!!turn,queryFn:({signal})=>projectAssistantApi.read(project,turn,signal),
    refetchInterval:q=>q.state.data?.active_turn?1000:false});
  const finished = ["completed", "failed"].includes(chat.data?.messages.at(-1)?.status || "");
  useEffect(() => {
    if (finished && turn && !client.getQueryData(["material-advice-finished", project, turn])) { client.setQueryData(["material-advice-finished", project, turn], true); void recent.refetch(); void client.invalidateQueries({queryKey:["material-advice",project,taskId,dataset.id,dataset.sha256]}); }
  }, [finished, turn, recent.refetch, client, project, taskId, dataset.id, dataset.sha256]);
  useEffect(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{project:string;task:string;dataset:string}>).detail;
      if (detail?.project === project && detail.task === context && detail.dataset === dataset.id) {
        setTurn("");
        void client.invalidateQueries({queryKey:["material-result",project,context,dataset.id,dataset.sha256]});
        void client.cancelQueries({queryKey:["material-advice",project,taskId,dataset.id,dataset.sha256]})
          .then(() => client.invalidateQueries({queryKey:["material-advice",project,taskId,dataset.id,dataset.sha256]}));
      }
    };
    const analysisFinished = (event: Event) => {
      const detail = (event as CustomEvent<{project:string;dataset:string;id:string}>).detail;
      if (detail?.project !== project || detail.dataset !== dataset.id) return;
      const marker = `statforge:material-analysis:${detail.id}:${dataset.id}:${context}`;
      if (sessionStorage.getItem(marker)) return;
      sessionStorage.setItem(marker,"1");
      changed(new CustomEvent("statforge:material-context-changed",{detail:{project,task:context,dataset:dataset.id}}));
    };
    window.addEventListener("statforge:material-context-changed", changed);
    window.addEventListener("statforge:analysis-finished", analysisFinished);
    const latestAnalysis = sessionStorage.getItem(`statforge:analysis-update:${project}:${dataset.id}`);
    if (latestAnalysis) analysisFinished(new CustomEvent("statforge:analysis-finished", {detail:{project,dataset:dataset.id,id:latestAnalysis}}));
    return () => {window.removeEventListener("statforge:material-context-changed", changed);window.removeEventListener("statforge:analysis-finished", analysisFinished);};
  }, [client, project, taskId, context, dataset.id, dataset.sha256]);
  useEffect(() => { setTurn(""); setError(""); }, [project, context, dataset.id, dataset.sha256,selectionKey]);
  const conversation = (turn && chat.data?.context_key === context && chat.data.dataset_id === dataset.id ? chat.data : null) ?? (resultSelection ? undefined:recent.data?.item);
  const message = conversation?.messages.at(-1);
  const brief = recent.data?.item?.id === conversation?.id ? recent.data?.item?.material_brief : undefined;
  const results = (message?.results ?? []).filter(r => r.href.startsWith(`/projects/${project}/`));
  const outputs = results.filter(r => r.kind !== "profile");
  async function openPlot(result: AssistantResult) {
    setError("");
    try {
      const record = await workspaceApi.read(result.id);
      if (record.project_id !== project || record.payload.kind !== "plot_run")
        throw new Error("无法打开这份材料的图形。");
      if(onPlot)onPlot(record.payload.draft_id, record.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "图形暂时无法打开。"); }
  }
  async function toggle(){
    setBusy(true);setError("");
    try{const enabled=!pref.data?.enabled;
      if(!enabled) await client.cancelQueries({queryKey:["material-advice",project]});
      client.setQueryData(preference,await apiFetch(base+"/preference",{method:"PUT",body:JSON.stringify({enabled})}));
    }catch(e){setError(e instanceof Error?e.message:"设置未保存。");}finally{setBusy(false);}
  }
  async function accept(item:Item){
    if(item.action==="review_source"){onReviewSource?.();setOpen(false);return;}
    if(item.action==="view_summary"){onSummary?.();setOpen(false);return;}
    if(item.action==="annotate"){if(onAnnotate)onAnnotate();else annotations?.setOpen(true);setOpen(false);return;}
    setBusy(true);setError("");
    window.dispatchEvent(new CustomEvent("statforge:show-agent", {detail:{project,context}}));
    try{const conversation=await projectAssistantApi.send(project,{material_action:true,context_key:context,dataset_id:dataset.id,question:item.question+(resultSelection?`\n当前明确选中的样本：${resultSelection.sample_id}；开始秒：${resultSelection.start ?? "未指定"}；结束秒：${resultSelection.end ?? "未指定"}。请使用这个样本和范围，不改用其他已保存选段。`:""),request_id:crypto.randomUUID()});
      client.setQueryData(["material-action",project,conversation.id],conversation);setTurn(conversation.id);
      sessionStorage.setItem(`statforge:material-conversation:${project}:${context}`,conversation.id);
      window.dispatchEvent(new CustomEvent("statforge:material-conversation", {detail:{project,context,id:conversation.id}}));
      setOpen(false);
    }catch(e){setError(e instanceof Error?e.message:"无法开始。");}finally{setBusy(false);}
  }
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger className="rw-advice-trigger" title="当前内容的 AI 建议" aria-label="AI 建议"><SparklesIcon size={16} aria-hidden="true"/><span>AI 建议</span></Popover.Trigger>
    <Popover.Portal container={host}><Popover.Positioner className="rw-advice-positioner" align="end" sideOffset={8} collisionPadding={10}>
    <Popover.Popup className="rw-advice-popup">
    <header><Popover.Title>AI 建议</Popover.Title><Popover.Close aria-label="关闭 AI 建议"><XIcon size={16}/></Popover.Close></header>
    <Popover.Description className="rw-advice-source" title={dataset.original_filename}>{dataset.original_filename}</Popover.Description>
    <div className="rw-advice-preference"><button type="button" aria-pressed={!!pref.data?.enabled} disabled={busy||!pref.data} onClick={()=>void toggle()}>{pref.data?.enabled?"暂停 AI 建议":"启用 AI 建议"}</button></div>
    {pref.data?.enabled && <><div className="rw-advice-copy">
      {(advice.isFetching||settled!==selectionKey)&&<span role="status">正在查看当前内容…</span>}
      {settled===selectionKey&&!advice.isFetching&&advice.data?.items.map((item,i)=><div className="rw-advice-item" key={i}><p>{item.description||item.question}</p>{(item.action!=="review_source"||onReviewSource)&&(item.action!=="view_summary"||onSummary)&&(item.action!=="annotate"||onAnnotate||annotations)&&<button disabled={busy||!!chat.data?.active_turn} onClick={()=>void accept(item)}>{item.label}</button>}</div>)}
      {settled===selectionKey&&!advice.isFetching&&advice.data?.items.length===0&&<span className="rw-muted">暂无新的建议</span>}
      </div>
    {advice.isError && <span role="alert">{advice.error.message} <button onClick={()=>void advice.refetch()}>重试</button></span>}</>}
    {(error || pref.error || chat.error || recent.error) && <span role="alert">{error || pref.error?.message || chat.error?.message || recent.error?.message}</span>}
    {chat.data?.active_turn ? <span role="status">{chat.data.active_turn.progress || "正在处理…"} <button type="button" disabled={busy || chat.data.active_turn.status === "stopping"} onClick={() => { setBusy(true); void projectAssistantApi.stop(project,turn).catch(reason => setError(reason instanceof Error ? reason.message : "无法停止。" )).finally(() => { setBusy(false); void chat.refetch(); }); }}>停止</button></span>
      : message?.role === "assistant" && <details className="rw-material-result">
        <summary>{message.status === "failed" ? "查看失败原因" : "查看处理结果"}</summary>
        <button type="button" onClick={()=>{ if(conversation) { sessionStorage.setItem(`statforge:material-conversation:${project}:${context}`,conversation.id); window.dispatchEvent(new CustomEvent("statforge:show-agent",{detail:{project,context}})); window.dispatchEvent(new CustomEvent("statforge:material-conversation",{detail:{project,context,id:conversation.id}})); setOpen(false); } }}>在 Agent 中查看过程与回复</button>
        <div className="rw-inline-actions">
          {(outputs.length ? outputs : results).map(r => {
            const key = r.kind + ":" + r.id;
            if (r.kind === "dataset") return <button type="button" key={key} onClick={async () => {
              client.setQueryData(["material-output-view", project, context, r.id], true);
              try { await client.invalidateQueries({ queryKey: ["datasets", project] }); await openMaterialResult(r.id, onChoose); }
              catch(reason) { setError(reason instanceof Error ? reason.message : "无法打开这份结果，请重试。"); }
            }}>{r.title === "特征摘要" ? "查看摘要" : r.title}</button>;
            if (r.kind === "plot" && onPlot) return <button type="button" key={key} title={r.title} onClick={() => void openPlot(r)}>查看图形</button>;
            return <DesktopLink key={key} href={r.href}>{r.title}</DesktopLink>;
          })}
        </div>
      </details>}
    </Popover.Popup></Popover.Positioner></Popover.Portal>
  </Popover.Root>;
}
