"use client";

import {useEffect,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";
import type {Dataset} from "@/lib/types";
import {SearchSelect} from "./ui/search-select";
import styles from "./survey-import.module.css";

type Column={name:string;role:"id"|"attribute"|"answer"|"ignore";kind:"number"|"text";example?:unknown};
type Options={columns:Column[];rows:number;collections:{id:string;title:string;count:number}[]};
type Preview={preview_sha256:string;new:number;updated:number;unchanged:number;rows:{row:number;id:string;status:"new"|"updated"|"unchanged";attributes:Record<string,unknown>;answer_count:number}[];errors:{row:number;id:string;message:string}[]};
const labels={new:"新增",updated:"更新",unchanged:"不变"};

export function SurveyImport({dataset,onChoose}:{dataset:Dataset;onChoose:(id:string)=>void}){
  const [open,setOpen]=useState(false);
  return <section className={styles.root}><button aria-expanded={open} onClick={()=>setOpen(value=>!value)}>作为问卷导入</button>
    {open&&<SurveyMapping key={dataset.id} dataset={dataset} onChoose={onChoose} close={()=>setOpen(false)}/>}</section>;
}
function SurveyMapping({dataset,onChoose,close}:{dataset:Dataset;onChoose:(id:string)=>void;close:()=>void}){
  const client=useQueryClient(),base=`/api/v1/projects/${dataset.project_id}/coding/survey`;
  const options=useQuery({queryKey:["survey-options",dataset.id,dataset.sha256],queryFn:({signal})=>apiFetch<Options>(base+`/${dataset.id}/options`,{signal}),staleTime:0});
  const [columns,setColumns]=useState<Column[]>([]),[collection,setCollection]=useState(""),[title,setTitle]=useState("问卷案例");
  const [empty,setEmpty]=useState<"keep"|"clear">("keep"),[preview,setPreview]=useState<Preview|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
  useEffect(()=>{if(options.data){setColumns(options.data.columns);setCollection(options.data.collections[0]?.id||"");}},[options.data]);
  function changeColumn(name:string,patch:Partial<Column>){setColumns(old=>old.map(c=>c.name===name?{...c,...patch}:patch.role==="id"&&c.role==="id"?{...c,role:"attribute"}:c));setPreview(null);}
  const body={dataset_id:dataset.id,collection_id:collection||null,title,empty,columns:columns.map(({name,role,kind})=>({name,role,kind}))};
  async function check(){setBusy(true);setError("");try{setPreview(await apiFetch<Preview>(base+"/preview",{method:"POST",body:JSON.stringify(body)}));}catch(e){setError(e instanceof Error?e.message:"无法读取问卷，请重新打开表格。");}finally{setBusy(false);}}
  async function save(){if(!preview)return;setBusy(true);setError("");try{
    const result=await apiFetch<{id:string}>(base+"/import",{method:"POST",body:JSON.stringify({...body,preview_sha256:preview.preview_sha256})});
    await Promise.all([client.invalidateQueries({queryKey:["datasets"]}),client.invalidateQueries({queryKey:["workspace-files"]}),client.invalidateQueries({queryKey:["coding",dataset.project_id]}),client.invalidateQueries({queryKey:["coding-attribute-catalog"]}),client.invalidateQueries({queryKey:["survey-options"]})]);
    onChoose(result.id);
  }catch(e){setError(e instanceof Error?e.message:"问卷未保存，请重试。");}finally{setBusy(false);}}
  return <div className={styles.mapping}>
    <div className="rw-section-heading"><h3>问卷字段</h3><button disabled={busy} onClick={close}>收起</button></div>
    {options.isPending&&<p role="status">正在读取表格…</p>}
    {options.data&&<>
      <div className={styles.settings}><label>归入案例组<SearchSelect aria-label="问卷案例组" value={collection} disabled={busy} onChange={e=>{setCollection(e.target.value);setPreview(null);}}><option value="">新建案例组</option>{options.data.collections.map(c=><option key={c.id} value={c.id}>{c.title} · {c.count} 个案例</option>)}</SearchSelect></label>
        {!collection&&<label>名称<input value={title} disabled={busy} onChange={e=>{setTitle(e.target.value);setPreview(null);}}/></label>}
        <label>空单元格<SearchSelect aria-label="问卷空单元格" value={empty} disabled={busy} onChange={e=>{setEmpty(e.target.value as "keep"|"clear");setPreview(null);}}><option value="keep">保留已有值</option><option value="clear">清空为缺测</option></SearchSelect></label></div>
      <p className="rw-muted">未映射的字段保留；新案例的空值记为缺测。</p>
      <div className="rw-table-scroll"><table className="rw-data-table"><thead><tr><th>原字段</th><th>用途</th><th>属性类型</th><th>内容示例</th></tr></thead><tbody>{columns.map(c=><tr key={c.name}><th scope="row">{c.name}</th><td><SearchSelect aria-label={`${c.name} 用途`} value={c.role} disabled={busy} onChange={e=>changeColumn(c.name,{role:e.target.value as Column["role"]})}><option value="id">案例 ID</option><option value="attribute">属性</option><option value="answer">开放回答</option><option value="ignore">忽略</option></SearchSelect></td><td>{c.role==="attribute"?<SearchSelect aria-label={`${c.name} 属性类型`} value={c.kind} disabled={busy} onChange={e=>changeColumn(c.name,{kind:e.target.value as Column["kind"]})}><option value="number">数值</option><option value="text">文本</option></SearchSelect>:"—"}</td><td className={styles.example}>{c.example==null?"—":String(c.example)}</td></tr>)}</tbody></table></div>
      <button disabled={busy||!columns.length||!title.trim()} onClick={()=>void check()}>{busy?"正在处理…":"查看导入预览"}</button>
      {preview&&<section aria-label="问卷导入预览"><p role="status">新增 {preview.new} · 更新 {preview.updated} · 不变 {preview.unchanged}</p>
        {!!preview.errors.length&&<ul role="alert">{preview.errors.map((e,i)=><li key={i}>原表第 {e.row} 行 · {e.id||"空 ID"} · {e.message}</li>)}</ul>}
        <div className="rw-table-scroll"><table className="rw-data-table"><thead><tr><th>原表行</th><th>案例 ID</th><th>本次操作</th><th>应用后的属性</th><th>开放题</th></tr></thead><tbody>{preview.rows.map(r=><tr key={r.row}><td>{r.row}</td><th scope="row">{r.id}</th><td>{labels[r.status]}</td><td>{Object.entries(r.attributes).map(([key,value])=>`${key}：${value==null?"—":value}`).join(" · ")}</td><td>{r.answer_count}</td></tr>)}</tbody></table></div>
        <button className="rw-primary" disabled={busy||!!preview.errors.length||!preview.rows.length} onClick={()=>void save()}>{busy?"正在保存…":`应用到 ${preview.rows.length} 个案例`}</button>
      </section>}
    </>}
    {(error||options.error)&&<p role="alert" className="rw-error">{error||options.error?.message}</p>}
  </div>;
}
