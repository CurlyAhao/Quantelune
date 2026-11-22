"use client";

import { createContext, useContext, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import type { Dataset } from "@/lib/types";
import type { TaskRecord } from "@/lib/use-workspace-task";
import { datasetTitle } from "@/lib/workspace-files";
import type { WorkspaceFile } from "./workspace-data-browser";

export type ResultTarget = {datasetId?:string;taskId?:string;analysisId?:string;figureId?:string;plotRunId?:string;collectionId?:string;runId?:string};
type Navigation = {project:{id:string;title:string};datasets:Dataset[];tasks:TaskRecord[];open:(target:ResultTarget)=>void;home:()=>void};
export const ResultNavigation = createContext<Navigation|null>(null);
export const runTime = (value?:string|null) => value ? new Date(value).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}) : "";
export type SavedMediaRun = {id:string;title:string;dataset_id:string;summary_id?:string;package_id?:string;started_at?:string;completed_at?:string;status:string};
export function useMediaRun(dataset:Dataset) {
  const meta=dataset.media_result;
  return useQuery({queryKey:["media-run-navigation",dataset.project_id,meta?.run_id],
    queryFn:({signal})=>apiFetch<SavedMediaRun>(`/api/v1/datasets/${meta!.collection_id}/media-runs/${meta!.run_id}`,{signal}),enabled:!!meta,staleTime:30000});
}
export function openMaterialResults(collection:string,run:string,taskId?:string) {
  sessionStorage.setItem("statforge:material-results:"+collection+":"+(taskId||"project"),JSON.stringify({view:"results",runId:run}));
}
export function MediaOwnership({dataset,datasets,onChoose,taskId}:{dataset:Dataset;datasets:Dataset[];onChoose:(id:string)=>void;taskId?:string}) {
  const nav=useContext(ResultNavigation), query=useMediaRun(dataset);
  const meta=dataset.media_result, run=query.data;
  if(!meta)return null;
  const group=datasets.find(item=>item.id===meta.collection_id);
  const openGroup=()=>{sessionStorage.removeItem("statforge:material-results:"+meta.collection_id+":"+(taskId||"project"));onChoose(meta.collection_id);};
  const openRun=()=>{openMaterialResults(meta.collection_id,meta.run_id,taskId);onChoose(meta.collection_id);};
  return <div className="rw-result-location">
    <nav aria-label="处理结果归属" className="rw-result-path">
      {nav&&<><button onClick={nav.home}>{nav.project.title}</button><span>›</span></>}
      <button onClick={openGroup}>{group?.display_name||group?.media_case?.title||group?.original_filename||"材料组"}</button><span>›</span>
      <button onClick={openRun}>处理 · {runTime(run?.started_at)||"读取中…"}</button><span>›</span>
      <span aria-current="page">{meta.kind==="summary_csv"?"特征摘要":meta.kind==="arrays_npz"?"完整特征":"派生产物"}</span>
    </nav>
    {run&&<div className="rw-inline-actions" aria-label="同次处理结果">
      <button disabled={!run.summary_id||run.summary_id===dataset.id} onClick={()=>onChoose(run.summary_id!)}>特征摘要</button>
      <button disabled={!run.package_id||run.package_id===dataset.id} onClick={()=>onChoose(run.package_id!)}>完整特征</button>
    </div>}
    {query.isError&&<p role="alert">{query.error.message}</p>}
  </div>;
}
export function AnalysisOwnership({taskId,title,scope,view,analysisId,revision,recordId,compact=false}:{taskId?:string;title:string;scope:{dataset_id?:string|null;statistical_run_id?:string|null;ml_run_id?:string|null};view:"结果"|"图表";analysisId?:string;revision?:number;recordId?:string;compact?:boolean}) {
  const nav=useContext(ResultNavigation);
  const analyses=useQuery({queryKey:["result-source-analyses",nav?.project.id],enabled:!!nav&&!analysisId,queryFn:({signal})=>apiFetch<{records:SearchRecord[]}>(`/api/v1/workspace/records?project_id=${nav!.project.id}&kind=analysis`,{signal})});
  if(!nav)return null;
  const linked=analyses.data?.records.find(item=>item.payload?.kind==="analysis"&&
    ((scope.statistical_run_id && item.payload.scope?.statistical_run_id===scope.statistical_run_id)||(scope.ml_run_id && item.payload.scope?.ml_run_id===scope.ml_run_id)));
  const sourceAnalysis=analysisId||linked?.id;
  const task=nav.tasks.find(item=>item.id===taskId)||nav.tasks.find(item=>(recordId&&item.payload.figure_ids.includes(recordId))||(sourceAnalysis&&item.payload.analysis_ids.includes(sourceAnalysis)));

  const source=nav.datasets.find(item=>item.id===scope.dataset_id);
  if(compact)return <div className="qp-sources">
    {source&&<button title={datasetTitle(source,nav.datasets)} onClick={()=>nav.open({datasetId:source.id})}><span>数据来源</span>{datasetTitle(source,nav.datasets)}</button>}
    {task&&sourceAnalysis&&<button onClick={()=>nav.open({taskId:task.id,analysisId:sourceAnalysis})}>来源分析 · {linked?.payload?.title||title}</button>}
  </div>;
  return <div className="rw-result-location">
    <nav aria-label="研究结果归属" className="rw-result-path">
      <button onClick={nav.home}>{nav.project.title}</button><span>›</span>
      {task&&<><button onClick={()=>nav.open({taskId:task.id})}>{task.payload.title}</button><span>›</span></>}
      <button disabled={!sourceAnalysis||!task} onClick={()=>nav.open({taskId:task?.id,analysisId:sourceAnalysis})}>{linked?.payload?.title||title}</button><span>›</span>
      <span aria-current="page">{view}{revision ? ` · 版本 ${revision}`:""}</span>
    </nav>
    {source&&<button className="rw-source-link" onClick={()=>nav.open({datasetId:source.id})}>数据来源 · {datasetTitle(source,nav.datasets)}</button>}
  </div>;
}

type SearchRecord={id:string;project_id:string;revision:number;payload?:{kind:string;title:string;dataset_id?:string;summary_id?:string;package_id?:string;started_at?:string;status?:string;scope?:{dataset_id?:string|null;statistical_run_id?:string|null;ml_run_id?:string|null};parameters?:{case?:{samples?:{title?:string;display_title?:string}[]}}}};
export function ProjectResultSearch({files,onFile,onClose}:{files:WorkspaceFile[];onFile:(file:WorkspaceFile)=>void;onClose:()=>void}) {
  const nav=useContext(ResultNavigation)!;
  const [term,setTerm]=useState("");
  const query=useQuery({queryKey:["project-search-records",nav.project.id],queryFn:({signal})=>apiFetch<{records:SearchRecord[]}>(`/api/v1/workspace/records?project_id=${nav.project.id}`,{signal})});
  const candidates:{key:string;type:string;name:string;owner:string;search:string;open:()=>void}[]=[];
  const add=(key:string,type:string,name:string,owner:string,open:()=>void,extra="")=>candidates.push({key,type,name,owner,open,search:[name,owner,extra].join(" ").toLocaleLowerCase()});
  for(const file of files)add(file.id,"文件",file.name,nav.project.title+" · "+file.path,()=>onFile(file));
  for(const data of nav.datasets.filter(item=>item.media_case||item.signal_session))
    add(data.id,"材料组",datasetTitle(data,nav.datasets),nav.project.title,()=>nav.open({datasetId:data.id}));
  for(const task of nav.tasks)add(task.id,"研究问题",task.payload.title,nav.project.title,()=>nav.open({taskId:task.id}));
  for(const row of query.data?.records||[]){
    const p=row.payload;if(!p||row.project_id!==nav.project.id)continue;
    if(p.kind==="media_run"&&p.dataset_id){
      const group=nav.datasets.find(item=>item.id===p.dataset_id);

      add(row.id,"处理运行",p.title,(group?datasetTitle(group,nav.datasets):"材料组")+" · "+({completed:"已完成",failed:"处理失败",running:"处理中",interrupted:"已中断"}[p.status||""]||"等待中"),
        ()=>nav.open({datasetId:p.summary_id||p.dataset_id,collectionId:p.dataset_id,runId:row.id}),runTime(p.started_at)+" "+(p.parameters?.case?.samples||[]).map(s=>[s.title,s.display_title].join(" ")).join(" "));
    }else if(["analysis","figure","plot_run"].includes(p.kind)){
      const task=nav.tasks.find(item=>(p.kind==="analysis"?item.payload.analysis_ids:p.kind==="figure"?item.payload.figure_ids:item.payload.plot_run_ids||[]).includes(row.id));
      if(!task)continue;
      const source=nav.datasets.find(item=>item.id===p.scope?.dataset_id);
      add(row.id,p.kind==="analysis"?"分析结果":"图表",p.title+(p.kind==="figure"?` · 版本 ${row.revision}`:""),task.payload.title+(source?" · "+datasetTitle(source,nav.datasets):""),
        ()=>nav.open({taskId:task.id,...(p.kind==="analysis"?{analysisId:row.id}:p.kind==="figure"?{figureId:row.id}:{plotRunId:row.id})}));
    }
  }
  const needle=term.trim().toLocaleLowerCase(), matches=needle?candidates.filter(item=>needle.split(/\s+/).every(word=>item.search.includes(word))):[];
  return <section className="rw-project-search" aria-label="项目内查找">
    <div className="rw-inline-actions"><input autoFocus type="search" aria-label="查找文件、材料组、运行或研究问题" placeholder="文件、材料组、运行时间或研究问题" value={term} onChange={e=>setTerm(e.target.value)} onKeyDown={e=>{if(e.key==="Escape")onClose();}}/><button onClick={onClose}>关闭查找</button></div>
    {query.isError&&<p role="alert">{query.error.message}<button onClick={()=>void query.refetch()}>重试</button></p>}
    {query.isFetching&&<span role="status">读取已保存结果…</span>}
    {needle&&<div className="rw-search-matches">{["文件","材料组","处理运行","研究问题","分析结果","图表"].map(type=>{
      const items=matches.filter(item=>item.type===type);return items.length>0&&<section key={type}><h3>{type} · {items.length}</h3>{items.map(item=><button key={item.key} onClick={()=>{item.open();onClose();}}><span>{item.name}</span><small>{item.owner}</small></button>)}</section>;
    })}{!matches.length&&!query.isFetching&&<p>没有找到匹配项。</p>}</div>}
  </section>;
}
