"use client";

import {useEffect, useMemo, useState} from "react";
import {ChevronRightIcon, FileTextIcon, LayersIcon} from "lucide-react";
import type {Dataset} from "@/lib/types";
import type {TaskRecord} from "@/lib/use-workspace-task";
import {runTime as formatCreatedTime} from "./workspace-result-navigation";
import {datasetTitle, workspaceOutputCategory, type WorkspaceInventory} from "@/lib/workspace-files";
import {analysisTableChoices, analysisTableVersions, tableDimensions, versionTitle} from "@/lib/analysis-tables";

function outputName(item:Dataset, datasets:Dataset[]):string {
  const cases=item.media_case?.samples || [];
  const names=cases.map(row=>row.case_title?.trim()).filter((name):name is string=>!!name);
  if(cases.length===1 && names.length===1)return names[0];
  if(names.length && names.length===cases.length)return names.slice(0,2).join("、")+(names.length>2?` 等 ${names.length} 个案例`:"");
  return datasetTitle(item,datasets);
}

export function WorkspaceOutputTree({datasets, sessions, generatedIds, tasks, selected, onChoose, onText, onTask}: {
  datasets:Dataset[]; sessions:WorkspaceInventory["sessions"]; generatedIds:ReadonlySet<string>; tasks:TaskRecord[];
  selected?:string; onChoose:(id:string)=>void; onText:(id:string)=>void; onTask:(task:string,dataset:string)=>void;
}) {
  const [expanded,setExpanded]=useState(true);
  const [categories,setCategories]=useState<string[]>([]);
  const tables = useMemo(() => analysisTableVersions(datasets), [datasets]);
  const groups=new Map<string,Dataset[]>();
  const currentSurvey=new Map<string,string>();
  for(const data of [...datasets].sort((a,b)=>b.created_at.localeCompare(a.created_at))) {
    if(data.media_case?.survey&&!currentSurvey.has(data.media_case.identity))currentSurvey.set(data.media_case.identity,data.id);
  }
  for(const data of analysisTableChoices(datasets, tables, selected ? [selected] : [])) {
    const category=data.media_case?.survey&&currentSurvey.get(data.media_case.identity)!==data.id?"处理版本":workspaceOutputCategory(data,generatedIds);
    if(category)groups.set(category,[...(groups.get(category)||[]),data]);
  }
  const labels=["材料组","特征摘要","完整特征","关联表","转录与校对","信号数据","主题汇总","编码比较","图像特征","派生音轨与视频帧","处理版本"];
  const total=Array.from(groups.values()).reduce((n,items)=>n+items.length,0);
  const activeCategory=Array.from(groups).find(([,items])=>items.some(d=>d.id===selected))?.[0];
  useEffect(()=>{if(activeCategory)setCategories(old=>old.includes(activeCategory)?old:[...old,activeCategory]);},[activeCategory]);
  return <section className="rw-output-tree" aria-label="处理文件">
    <button className="rw-output-heading" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>
      <LayersIcon size={16}/><span>处理文件</span><small>{total}</small><ChevronRightIcon size={13} className={expanded?"expanded":""}/>
    </button>
    {expanded&&<div className="rw-output-categories">{labels.map(label=>{
      const items=groups.get(label);if(!items?.length)return null;
      const summaryNames=new Map<string,number>();
      for(const item of items) {
        const name=outputName(item,datasets);
        summaryNames.set(name,(summaryNames.get(name)||0)+1);
      }
      return <details key={label} open={categories.includes(label)} onToggle={event=>{
        const open=event.currentTarget.open;
        setCategories(old=>open ? old.includes(label)?old:[...old,label] : old.filter(x=>x!==label));
      }}><summary><ChevronRightIcon size={12}/><span>{label}</span><small>{items.length}</small></summary>
        <ul>{items.slice().sort((a,b)=>b.created_at.localeCompare(a.created_at)).map(d=>{
          const table = tables.get(d.id);
          if (table) {
            const ids = new Set(table.copies.map(copy => copy.id));
            const links = tasks.flatMap(task => {
              const id = [task.payload.dataset_id, ...(task.payload.dataset_ids ?? [])].find(id => id && ids.has(id));
              return id ? [{task, id}] : [];
            });
            const active = !!selected && ids.has(selected);
            return <li key={table.copies.at(-1)!.id}>
              <button title={d.original_filename} aria-current={active ? "page" : undefined}
                className={active ? "active" : ""} onClick={()=>onChoose(d.id)}>
                <FileTextIcon size={13}/><span className="rw-output-name"><span>{versionTitle(table)}</span>
                  <small>{tableDimensions(d)}</small>{table.total > 1 && table.change && <small>{table.change}</small>}
                </span>
              </button>
              {!!links.length && <div className="rw-output-links">{links.map(({task,id}) =>
                <button key={task.id} title={"研究问题 · "+task.payload.title} onClick={()=>onTask(task.id,id)}>{task.payload.title}</button>)}</div>}
            </li>;
          }
          const cases=d.media_case?.samples || [];
          const name=outputName(d,datasets);
          // SQLite returns stored UTC timestamps without an offset.
          const createdAt=/(?:Z|[+-]\d{2}:\d{2})$/i.test(d.created_at) ? d.created_at : d.created_at+"Z";
          const createdTime=(summaryNames.get(name)||0)>1 || !!d.media_case || label==="处理版本" ? formatCreatedTime(createdAt) : "";
          const session=sessions.find(item=>item.id===d.id);
          const sourceName=d.media_case ? (cases.length===1 ? (cases[0].original_filename || cases[0].title || cases[0].display_title) : undefined) || session?.files.find(path=>/\.(mp4|wav)$/i.test(path))?.split(/[\\/]/).pop() : undefined;
          const runTime=d.media_result && name.match(/\d{2}-\d{2} \d{2}:\d{2}:\d{2}/)?.[0];
          const count=d.media_result && name.match(/\d+ 个样本/)?.[0];
          const shortName=runTime && ["summary_csv","arrays_npz"].includes(d.media_result?.kind || "") ? runTime+(count?" · "+count:"") : name;
          const owners=tasks.filter(task=>task.payload.dataset_id===d.id||task.payload.dataset_ids?.includes(d.id));
          return <li key={d.id}><button title={[name,sourceName,createdTime].filter(Boolean).join(" · ")} aria-current={selected===d.id?"page":undefined}
            className={selected===d.id?"active":""} onClick={()=>onChoose(d.id)}><FileTextIcon size={13}/><span className="rw-output-name"><span>{shortName}</span>{sourceName&&sourceName!==name&&<small>{sourceName}</small>}{createdTime&&<small><time dateTime={createdAt}>{createdTime}</time></small>}</span></button>
            {(d.transcription||owners.length>0)&&<div className="rw-output-links">
              {d.transcription&&<button onClick={()=>onText(d.id)}>打开{d.file_format==="srt"?"字幕":"文本"}</button>}
              {owners.map(task=><button key={task.id} title={"研究问题 · "+task.payload.title} onClick={()=>onTask(task.id,d.id)}>{task.payload.title}</button>)}
            </div>}
          </li>;
        })}</ul>
      </details>;
    })}{!total&&<p className="rw-sidebar-hint">暂无处理文件</p>}</div>}
  </section>;
}
