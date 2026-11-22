"use client";
import {useEffect,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";

type Watch={project:string;task?:string;source:string;url:string;title:string};
const storage=(project:string)=>"statforge:processing:"+project;
function saved(project:string):Watch[]{try{return JSON.parse(sessionStorage.getItem(storage(project))||"[]");}catch{return [];}}
export function watchProcessing(item:Watch){
  const list=saved(item.project).filter(w=>w.url!==item.url);
  sessionStorage.setItem(storage(item.project),JSON.stringify([...list,item]));
  window.dispatchEvent(new Event("statforge-processing"));
}
export function WorkspaceRunNotice({project,open}:{project:string;open:(dataset:string,task?:string)=>void}){
  const [list,setList]=useState<Watch[]>([]);
  useEffect(()=>{const sync=()=>setList(saved(project));sync();window.addEventListener("statforge-processing",sync);return()=>window.removeEventListener("statforge-processing",sync);},[project]);
  function dismiss(w:Watch){sessionStorage.setItem(storage(project),JSON.stringify(saved(project).filter(v=>v.url!==w.url)));window.dispatchEvent(new Event("statforge-processing"));}
  return list.length?<aside className="rw-run-notices" aria-label="处理状态">{list.map(w=><RunNotice key={w.url} w={w} open={open} dismiss={()=>dismiss(w)}/>)}</aside>:null;
}
function RunNotice({w,open,dismiss}:{w:Watch;open:(dataset:string,task?:string)=>void;dismiss:()=>void}){
  const client=useQueryClient();
  const run=useQuery({queryKey:["workspace-run-notice",w.url],queryFn:({signal})=>apiFetch<{status:string;progress:string;summary_id?:string;error_message?:string;failure_reason?:string}>(w.url,{signal}),refetchInterval:q=>q.state.data&&["queued","running"].includes(q.state.data.status)?1400:false,retry:false});
  const done=run.data?.status==="completed";
  const terminal=run.data?.status && ["completed","failed","interrupted"].includes(run.data.status);
  useEffect(()=>{if(terminal){void client.invalidateQueries({queryKey:["datasets",w.project]});void client.invalidateQueries({queryKey:["research-tasks"]});
    const marker=["material-context-event",w.url];
    const seen=`statforge:material-context-event:${w.url}`;
    if (!client.getQueryData(marker) && !sessionStorage.getItem(seen)) { client.setQueryData(marker,true); sessionStorage.setItem(seen,"1"); void client.invalidateQueries({queryKey:["material-advice",w.project,w.task,w.source],refetchType:"none"}); window.dispatchEvent(new CustomEvent("statforge:material-context-changed",{detail:{project:w.project,task:w.task||"project",dataset:w.source}})); }
  }},[terminal,client,w.project,w.task,w.source,w.url]);
  return <div role="status"><strong>{w.title}</strong> {run.isError ? "读取处理状态失败" : run.data?.status === "failed" ? "处理失败 · " + (run.data.failure_reason || run.data.error_message || "请打开处理记录查看。") : run.data?.progress || "正在读取…"} {done&&<button onClick={()=>{open(run.data?.summary_id||w.source,w.task);dismiss();}}>查看结果</button>} <button aria-label="收起此条状态" onClick={dismiss}>×</button></div>;
}
