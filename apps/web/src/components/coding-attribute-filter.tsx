"use client";

import {useEffect,useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {SearchSelect} from "./ui/search-select";
import {codingApi,type CodingState,type CodingChange,type AttributeFilter,type AttributeBindingRequest,type AttributePreview,type AttributeCondition} from "@/lib/text-coding";
import styles from "./workspace-annotations.module.css";

const emptyFilter:AttributeFilter={mode:"all",conditions:[]};
const emptyBinding:AttributeBindingRequest={dataset_id:"",target:"case",key_column:"",key_kind:"sample_id",columns:[],file_choices:{}};
const ops={eq:"等于",gte:"大于等于",lte:"小于等于",contains:"包含",starts_with:"开头是"};

export function CodingAttributeFilter({state,busy,change}:{state:CodingState;busy:boolean;change:(body:CodingChange)=>Promise<boolean>}) {
  const [open,setOpen]=useState(false),[bindOpen,setBindOpen]=useState(false);
  const [draft,setDraft]=useState<AttributeFilter>(state.payload.attribute_filter||emptyFilter);
  const [binding,setBinding]=useState<AttributeBindingRequest>(emptyBinding);
  const [preview,setPreview]=useState<AttributePreview|null>(null),[error,setError]=useState(""),[checking,setChecking]=useState(false);
  useEffect(()=>{setDraft(state.payload.attribute_filter||emptyFilter);},[state.payload.attribute_filter]);
  const catalog=useQuery({queryKey:["coding-attribute-catalog",state.project_id,state.revision],queryFn:({signal})=>codingApi.attributeCatalog(state.project_id,signal),enabled:open&&bindOpen});
  const table=useQuery({queryKey:["dataset-preview",binding.dataset_id,"attributes"],queryFn:({signal})=>codingApi.attributeColumns(state.project_id,binding.dataset_id,signal),enabled:bindOpen&&!!binding.dataset_id});
  const bindings=state.payload.attribute_bindings||[];
  const columns=table.data?.columns||[];
  function updateBinding(patch:Partial<AttributeBindingRequest>){setBinding(old=>({...old,...patch}));setPreview(null);setError("");}
  async function check(){setChecking(true);setError("");try{setPreview(await codingApi.attributePreview(state.project_id,binding));}catch(reason){setError(reason instanceof Error?reason.message:"无法读取属性表。");}finally{setChecking(false);}}
  async function saveBinding(){if(await change({operation:"bind_attributes",attribute_binding:binding})){setBindOpen(false);setPreview(null);setBinding(emptyBinding);}}
  function setCondition(index:number,patch:Partial<AttributeCondition>){setDraft(old=>({...old,conditions:old.conditions.map((c,i)=>i===index?{...c,...patch}:c)}));}
  return <section aria-label="属性筛选">
    <button type="button" aria-expanded={open} onClick={()=>setOpen(value=>!value)}>属性筛选{state.payload.attribute_filter?.conditions.length?` · ${state.payload.attribute_filter.conditions.length} 条`:""}</button>
    {open&&<div className={styles.attributeEditor}>
      {draft.conditions.length>0&&<label className={styles.field}>组合条件<SearchSelect aria-label="属性条件组合" value={draft.mode} onChange={e=>setDraft({...draft,mode:e.target.value as "all"|"any"})}><option value="all">同时满足</option><option value="any">满足任一</option></SearchSelect></label>}
      {draft.conditions.map((condition,index)=>{const selected=bindings.find(b=>b.id===condition.binding_id),column=selected?.request.columns.find(c=>c.name===condition.column);return <div key={index} className={styles.attributeCondition}>
        <SearchSelect aria-label={`筛选属性 ${index+1}`} value={`${condition.binding_id}:${condition.column}`} onChange={e=>{const [id,...name]=e.target.value.split(":");setCondition(index,{binding_id:id,column:name.join(":"),operator:"eq",value:""});}}>{bindings.map(b=><optgroup key={b.id} label={`${b.request.target==="case"?"案例":"文件"} · ${b.source_name}`}>{b.request.columns.map(c=><option key={c.name} value={`${b.id}:${c.name}`}>{c.name}</option>)}</optgroup>)}</SearchSelect>
        <div className={styles.attributeConditionValue}><SearchSelect aria-label={`筛选关系 ${index+1}`} value={condition.operator} onChange={e=>setCondition(index,{operator:e.target.value as AttributeCondition["operator"]})}>{(column?.kind==="number"?["eq","gte","lte"]:["eq","contains","starts_with"]).map(op=><option key={op} value={op}>{ops[op as keyof typeof ops]}</option>)}</SearchSelect><input aria-label={`筛选值 ${index+1}`} type={column?.kind==="number"?"number":"text"} value={condition.value} onChange={e=>setCondition(index,{value:e.target.value})}/><button aria-label={`移除条件 ${index+1}`} onClick={()=>setDraft({...draft,conditions:draft.conditions.filter((_,i)=>i!==index)})}>移除</button></div>
      </div>;})}
      <div className={styles.actions}>{bindings.length>0&&<button onClick={()=>{const b=bindings[0];setDraft({...draft,conditions:[...draft.conditions,{binding_id:b.id,column:b.request.columns[0].name,operator:"eq",value:""}]});}}>添加条件</button>}
        {(draft.conditions.length>0||!!state.payload.attribute_filter?.conditions.length)&&<><button disabled={busy||draft.conditions.some(c=>c.value==="")} onClick={()=>void change({operation:"filter_attributes",attribute_filter:draft})}>应用筛选</button><button disabled={busy} onClick={()=>void change({operation:"filter_attributes",attribute_filter:emptyFilter})}>清空筛选</button></>}
        <button onClick={()=>{setBindOpen(value=>!value);setError("");}}>关联属性表</button>
      </div>
      {bindings.length>0&&!bindOpen&&<div className={styles.hint}>{bindings.map(b=><span key={b.id}>{b.request.target==="case"?"案例":"文件"} · {b.source_name} · {b.rows.length} 个对象 <button onClick={()=>{setBinding({...b.request,id:b.id});setBindOpen(true);setPreview(null);}}>修改关联</button><br/></span>)}</div>}
      {bindOpen&&<div className={styles.attributeBinding}>
        <label className={styles.field}>属性表<SearchSelect aria-label="属性表" value={binding.dataset_id} onChange={e=>updateBinding({dataset_id:e.target.value,key_column:"",columns:[],file_choices:{},case_choices:{}})}><option value="">选择已导入的表格</option>{catalog.data?.tables.map(t=><option key={t.id} value={t.id}>{t.title}</option>)}</SearchSelect></label>
        <label className={styles.field}>关联对象<SearchSelect aria-label="属性关联对象" value={binding.target} onChange={e=>updateBinding({target:e.target.value as "case"|"file",key_kind:e.target.value==="case"?"sample_id":"relative_path",file_choices:{},case_choices:{},group_id:undefined})}><option value="case">案例属性</option><option value="file">文件属性</option></SearchSelect></label>
        {binding.target==="case"?<label className={styles.field}>材料组<SearchSelect aria-label="属性材料组" value={binding.group_id||""} onChange={e=>updateBinding({group_id:e.target.value||undefined,case_choices:{}})}><option value="">项目内唯一的样本 ID</option>{Array.from(new Map(catalog.data?.cases.map(c=>[c.group_id,c])||[]).values()).map(c=><option key={c.group_id} value={c.group_id}>{c.group_title}</option>)}</SearchSelect></label>:<label className={styles.field}>文件对应方式<SearchSelect aria-label="文件对应方式" value={binding.key_kind} onChange={e=>updateBinding({key_kind:e.target.value as "relative_path"|"file_id",file_choices:{}})}><option value="relative_path">原文件相对路径</option><option value="file_id">文件 ID</option></SearchSelect></label>}
        <label className={styles.field}>{binding.target==="case"?"样本 ID 列":"文件关联列"}<SearchSelect aria-label="属性关联列" value={binding.key_column} onChange={e=>updateBinding({key_column:e.target.value,file_choices:{},case_choices:{}})}><option value="">选择字段</option>{columns.map(c=><option key={c.name} value={c.name}>{c.name}</option>)}</SearchSelect></label>
        {columns.length>0&&<fieldset className={styles.attributeColumns}><legend>选择属性</legend>{columns.filter(c=>c.name!==binding.key_column).map(c=>{const selected=binding.columns.find(col=>col.name===c.name);return <div key={c.name}><label><input type="checkbox" checked={!!selected} onChange={e=>updateBinding({columns:e.target.checked?[...binding.columns,{name:c.name,kind:/int|float|double|decimal|numeric/i.test(c.polars_type)?"number":"text"}]:binding.columns.filter(col=>col.name!==c.name)})}/>{c.name}</label>{selected&&<SearchSelect aria-label={`${c.name} 属性类型`} value={selected.kind} onChange={e=>updateBinding({columns:binding.columns.map(col=>col.name===c.name?{...col,kind:e.target.value as "number"|"text"}:col)})}><option value="text">文本</option><option value="number">数值</option></SearchSelect>}</div>;})}</fieldset>}
        <button disabled={checking||busy||!binding.dataset_id||!binding.key_column||!binding.columns.length} onClick={()=>void check()}>{checking?"正在匹配…":"查看匹配"}</button>
        {preview&&<><p role="status">已匹配 {preview.matched_count} / {preview.total_rows} 行 · {binding.target==="case"?"案例":"文件"}</p><ul>{preview.binding.rows.map(r=><li key={r.key}>{r.title}</li>)}</ul>{preview.unmatched.map(row=><label key={row.row} className={styles.field}>{row.value||`第 ${row.row+1} 行`} · {row.reason}{binding.target==="file"&&<SearchSelect aria-label={`指定文件 ${row.row+1}`} value={binding.file_choices[String(row.row)]||""} onChange={e=>updateBinding({file_choices:{...binding.file_choices,[row.row]:e.target.value}})}><option value="">选择对应文件</option>{catalog.data?.files.map(f=><option key={f.key} value={f.key}>{f.relative_path||f.title}{f.version?` · v${f.version}`:""} · {f.sha256.slice(0,8)}</option>)}</SearchSelect>}{binding.target==="case"&&<SearchSelect aria-label={`指定案例 ${row.row+1}`} value={binding.case_choices?.[String(row.row)]||""} onChange={e=>updateBinding({case_choices:{...binding.case_choices,[row.row]:e.target.value}})}><option value="">选择对应案例</option>{catalog.data?.cases.map(c=><option key={c.key} value={c.key}>{c.title} · {c.group_title}</option>)}</SearchSelect>}</label>)}<button disabled={busy||preview.unmatched.length>0||!preview.matched_count} onClick={()=>void saveBinding()}>保存属性关联</button></>}
        {(error||catalog.error||table.error)&&<p role="alert" className={styles.error}>{error||(catalog.error||table.error)?.message}</p>}
        <button onClick={()=>setBindOpen(false)}>取消关联</button>
      </div>}
    </div>}
  </section>;
}
