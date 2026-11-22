"use client";

import {useEffect, useLayoutEffect, useMemo, useRef, useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {codingApi, themePath, type CodingRelation, type RelationKind, type RelationsRequest, type TextCodedSegment} from "@/lib/text-coding";
import {useWorkspaceAnnotations} from "./workspace-annotations";
import {CodingAttributeFilter} from "./coding-attribute-filter";
import {SearchSelect} from "./ui/search-select";
import {DownloadButton} from "./download-button";
import styles from "./coding-relations.module.css";

const relations:Record<RelationKind,string>={all:"全部关系",equal:"相同区间",contains:"完全包含",overlap:"部分重叠",touching:"相接",separate:"分离"};

export function CodingRelationsView({projectId,active}:{projectId:string;active:boolean}) {
  const context=useWorkspaceAnnotations();
  const state=context?.state;
  const [coder,setCoder]=useState("");
  const [excluded,setExcluded]=useState<string[]>([]);
  const [pair,setPair]=useState<string[]|undefined>();
  const [relation,setRelation]=useState<RelationKind>("all");
  const [page,setPage]=useState(0);
  const [tab,setTab]=useState<"matrix"|"relations">("matrix");
  const root=useRef<HTMLDivElement>(null), savedScroll=useRef(0);
  const themes=useMemo(()=>state?.payload.themes||[],[state?.payload.themes]);
  const selected=useMemo(()=>themes.filter(t=>t.is_codable!==false&&!excluded.includes(t.id)).map(t=>t.id),[themes,excluded]);
  const selectedCase=state?.case_scopes?.find(c=>c.key===context?.caseKey);
  const unavailable=!!context?.caseKey&&!selectedCase;
  const request:RelationsRequest={revision:state?.revision||0,case_collection_id:selectedCase?.collection_id,
    case_sample_id:selectedCase?.sample_id,coder_id:coder||undefined,theme_ids:selected,pair,relation,page};
  const query=useQuery({queryKey:["coding-relations",projectId,request],queryFn:({signal})=>codingApi.relations(projectId,request,signal),enabled:active&&!!state&&!unavailable});
  useEffect(()=>{setPair(undefined);setPage(0);},[state?.revision,context?.caseKey,coder,excluded]);
  useLayoutEffect(()=>{
    if(active&&root.current){const frame=requestAnimationFrame(()=>{const scroll=root.current?.closest(".rw-project-scroll");if(scroll)scroll.scrollTop=savedScroll.current;});return()=>cancelAnimationFrame(frame);}
  },[active]);
  const data=query.data;
  const names=new Map(data?.themes.map(t=>[t.id,t.name]));
  function reveal(segment:TextCodedSegment){savedScroll.current=root.current?.closest(".rw-project-scroll")?.scrollTop||0;context?.focus(segment);}
  function source(item:CodingRelation){const s=item.left.segment;return `${s.filename} · 第 ${s.locator.page} 页 · v${s.material_version}`;}
  function detailRows(){return data?.relations.map(item=><tr key={`${item.theme_a}:${item.theme_b}:${item.left.segment.id}:${item.right.segment.id}`}>
    <td className={styles.source}>{source(item)}<small>{item.left.segment.coder_name||"已记录编码者"}</small></td>
    {(["left","right"] as const).map((side,index)=><td key={side}><button className={styles.excerpt} onClick={()=>reveal(item[side].segment)}>
      <strong>{names.get(index===0?item.theme_a:item.theme_b)}</strong><span>{item[side].segment.locator.quote.replace(/\r\n/g,"\n")}</span><small>[{item[side].start}, {item[side].end}) · 回到原文</small>
    </button></td>)}
    <td>{relations[item.relation]}</td><td className={styles.number}>{item.overlap}</td><td className={styles.number}>{item.distance}</td>
  </tr>);}
  if(!context||!state)return <p role="status">正在读取标注…</p>;
  const canExport=!!data&&!query.isFetching&&!query.error&&!unavailable;
  return <div ref={root} className={styles.surface}>
    <div className={styles.scopeControls}>
      <label>案例范围<SearchSelect aria-label="共现案例范围" value={context.caseKey} onChange={e=>context.setCaseKey(e.target.value)}><option value="">全项目</option>{state.case_scopes?.map(c=><option key={c.key} value={c.key}>{c.title} · {c.group_title}</option>)}</SearchSelect></label>
      <label>编码者<SearchSelect aria-label="编码者" value={coder} onChange={e=>setCoder(e.target.value)}><option value="">全部（分别计算）</option>{data?.coders.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</SearchSelect></label>
      <details className={styles.themeChoice}><summary>主题 · {selected.length}</summary><div>{themes.filter(t=>t.is_codable!==false).map(t=><label key={t.id}><input type="checkbox" checked={!excluded.includes(t.id)} onChange={e=>setExcluded(old=>e.target.checked?old.filter(id=>id!==t.id):[...old,t.id])}/>{themePath(t,themes)}</label>)}</div></details>
    </div>
    <div className={styles.attributes}><CodingAttributeFilter state={state} busy={context.busy} change={context.change}/></div>
    <p className={styles.scope}>{data?.scope.scope_label || (selectedCase?`案例 · ${selectedCase.title}`:"全项目")} · 仅当前文字版本</p>
    <p className={styles.method}>按 Unicode 字符交集计数，换行统一为 LF；只比较同一文件版本、同一编码者，边界相接不计共现。</p>
    <div className={styles.toolbar}>
      <div className={styles.tabs}><button aria-pressed={tab==="matrix"} onClick={()=>setTab("matrix")}>共现矩阵</button><button aria-pressed={tab==="relations"} onClick={()=>{setTab("relations");setPair(undefined);setRelation("all");setPage(0);}}>关系明细</button></div>
      <DownloadButton identity={JSON.stringify({...request,format:tab})} disabled={!canExport} download={signal=>codingApi.exportRelations(projectId,request,tab,signal)}>{tab==="matrix"?"导出矩阵 CSV":"导出明细 CSV"}</DownloadButton>
    </div>
    {unavailable&&<p role="alert">此案例已不可用，请重新选择范围。</p>}
    {(query.error||context.error)&&<p role="alert">{query.error?.message||context.error}<button onClick={()=>void query.refetch()}>重新读取</button></p>}
    {query.isFetching&&<p role="status">正在计算…</p>}
    {data&&!query.isFetching&&!query.error&&!unavailable&&<>
      {!!data.unrecorded_annotations&&<p>{data.unrecorded_annotations} 条文字标注未记录编码者，未计入同编码者比较。</p>}
      {!data.comparable_annotations?<p className={styles.empty}>当前范围没有可比较的文字标注。</p>:tab==="matrix"?<>
        <p className={styles.count}>{data.comparable_annotations} 条文字标注 · {data.cooccurrences} 对正长度交集</p>
        <div className={styles.tableScroll}><table className={styles.matrix}><thead><tr><th scope="col">主题</th>{data.themes.map(t=><th scope="col" key={t.id}>{t.name}</th>)}</tr></thead><tbody>{data.themes.map((a,i)=><tr key={a.id}><th scope="row">{a.name}</th>{data.themes.map((b,j)=><td key={b.id}>{i===j?"—":<button disabled={!data.matrix[i][j]} aria-label={`${a.name}与${b.name}：${data.matrix[i][j]}次共现`} onClick={()=>{setPair([a.id,b.id]);setRelation("all");setPage(0);setTab("relations");}}>{data.matrix[i][j]}</button>}</td>)}</tr>)}</tbody></table></div>
      </>:<>
        <div className={styles.relationControls}><label>关系<SearchSelect aria-label="关系类型" value={relation} onChange={e=>{setRelation(e.target.value as RelationKind);setPage(0);}}>{Object.entries(relations).map(([id,label])=><option key={id} value={id}>{label}</option>)}</SearchSelect></label>{pair&&<p>{pair.map(id=>names.get(id)).join(" ↔ ")}<button onClick={()=>{setPair(undefined);setPage(0);}}>查看全部主题对</button></p>}</div>
        <p className={styles.count}>{data.total_relations} 对编码 · 重叠和距离单位：字符</p>
        {data.relations.length?<div className={styles.tableScroll}><table className={styles.details}><thead><tr><th>来源</th><th>编码 A</th><th>编码 B</th><th>关系</th><th>重叠</th><th>距离</th></tr></thead><tbody>{detailRows()}</tbody></table></div>:<p className={styles.empty}>当前条件没有匹配的编码关系。</p>}
        {data.total_relations>data.page_size&&<div className={styles.pagination}><button disabled={page===0} onClick={()=>setPage(value=>value-1)}>上一页</button><span>{page+1} / {Math.ceil(data.total_relations/data.page_size)}</span><button disabled={(page+1)*data.page_size>=data.total_relations} onClick={()=>setPage(value=>value+1)}>下一页</button></div>}
      </>}
    </>}
  </div>;
}
