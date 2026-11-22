"use client";

import {useEffect,useLayoutEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";
import {materialApi} from "@/lib/material-api";
import {codingApi,themePath,type AgreementModality,type AgreementNumbers,type AgreementRequest,type CodingAgreement,type AnnotationTarget} from "@/lib/text-coding";
import {useWorkspaceAnnotations} from "./workspace-annotations";
import {CodingAttributeFilter} from "./coding-attribute-filter";
import {SearchSelect} from "./ui/search-select";
import {DownloadButton} from "./download-button";
import styles from "./coding-relations.module.css";

type Selection={modality:AgreementModality;coder_a:string;coder_b:string;theme_id:string;material_id:string;case_key:string;snapshot:string};
type Saved={id:string;title:string;saved_at:string;scope_label:string};
const empty:Selection={modality:"text",coder_a:"",coder_b:"",theme_id:"",material_id:"",case_key:"",snapshot:""};
const labels={text:"文字",image:"图片",audio:"音频",video:"视频"};
function restored(project:string):Selection {
  try{return {...empty,...JSON.parse(localStorage.getItem(`coding-agreement:${project}`)||"{}")};}catch{return empty;}
}
const number=(value:number|null|undefined)=>value==null?"—":value.toLocaleString("zh-CN",{maximumFractionDigits:6});
const percent=(value:number|null)=>value===null?"—":`${(value*100).toFixed(4)}%`;
function Numbers({value,unit,a,b}:{value:AgreementNumbers;unit:string;a:string;b:string}) {
  return <>
    <div className={styles.tableScroll}><table><thead><tr><th>编码状态</th><th className={styles.number}>{unit}</th></tr></thead><tbody>{[["双方编码",value.both],[`仅 ${a} 编码`,value.only_a],[`仅 ${b} 编码`,value.only_b],["双方未编码",value.neither]].map(([label,count])=><tr key={String(label)}><th scope="row">{label}</th><td className={styles.number}>{number(count as number|null)}</td></tr>)}</tbody></table></div>
    <dl className={styles.agreementStats}><div><dt>Cohen’s κ</dt><dd>{value.kappa===null?"—":value.kappa.toFixed(6)}</dd></div><div><dt>总体一致率</dt><dd>{percent(value.agreement)}</dd></div><div><dt>已编码交并比</dt><dd>{percent(value.coded_overlap)}</dd></div></dl>
  </>;
}
export function CodingAgreementView({projectId,active,snapshotId}:{projectId:string;active:boolean;snapshotId?:string}) {
  const context=useWorkspaceAnnotations(),state=context?.state,client=useQueryClient();
  const [selection,setSelection]=useState(()=>restored(projectId));
  const [page,setPage]=useState(0),[error,setError]=useState(""),[saving,setSaving]=useState(false);
  const root=useRef<HTMLDivElement>(null),scroll=useRef(0),restoreScroll=useRef(true);
  const snapshot=snapshotId||selection.snapshot,base=`/api/v1/projects/${projectId}/coding/agreement`;
  const choices=useQuery({queryKey:["coding-agreement-choices",projectId,state?.revision],queryFn:({signal})=>codingApi.agreementChoices(projectId,signal),enabled:active&&!!state&&!snapshot});
  const history=useQuery({queryKey:["coding-agreement-history",projectId],queryFn:({signal})=>apiFetch<Saved[]>(base+"/history",{signal}),enabled:active});
  const ready=!!snapshot||!!state&&!!selection.coder_a&&!!selection.coder_b&&!!selection.theme_id&&selection.coder_a!==selection.coder_b;
  const request:AgreementRequest={modality:selection.modality,coder_a:selection.coder_a,coder_b:selection.coder_b,theme_id:selection.theme_id,revision:state?.revision||0,material_id:selection.material_id||undefined,case_key:selection.case_key||undefined,page};
  const query=useQuery({queryKey:["coding-agreement",projectId,snapshot||request,page],queryFn:({signal})=>snapshot?apiFetch<CodingAgreement>(base+`/saved/${snapshot}?page=${page}`,{signal}):codingApi.agreement(projectId,request,signal),enabled:active&&ready});
  useEffect(()=>{localStorage.setItem(`coding-agreement:${projectId}`,JSON.stringify(selection));setPage(0);},[selection,projectId]);
  useEffect(()=>{if(!snapshot)setPage(0);},[state?.revision,snapshot]);
  const data=ready?query.data:undefined,available=!!data&&!query.isFetching&&!query.error;
  useLayoutEffect(()=>{if(!active){restoreScroll.current=true;return;}if(available&&restoreScroll.current){const frame=requestAnimationFrame(()=>{const pane=root.current?.closest(".rw-project-scroll");if(pane){pane.scrollTop=scroll.current;restoreScroll.current=false;}});return()=>cancelAnimationFrame(frame);}},[active,available]);
  function choose(key:keyof Selection,value:string){setSelection(old=>({...old,[key]:value,...(key==="modality"?{material_id:""}:{})}));setError("");}
  async function reveal(target:AnnotationTarget){setError("");try{if(target.material_id){const material=await materialApi.get(target.material_id);if(material.sha256!==target.material_sha256||material.version!==target.material_version)throw new Error("原材料版本已变化，请打开对应旧版本回查。");}scroll.current=root.current?.closest(".rw-project-scroll")?.scrollTop||0;context?.focus(target);}catch(e){setError(e instanceof Error?e.message:"无法打开对应原材料。");}}
  async function save(){setSaving(true);setError("");try{const result=await apiFetch<Saved>(base+"/save",{method:"POST",body:JSON.stringify(request)});await Promise.all([client.invalidateQueries({queryKey:["coding-agreement-history",projectId]}),client.invalidateQueries({queryKey:["datasets"]})]);choose("snapshot",result.id);}catch(e){setError(e instanceof Error?e.message:"保存失败，请重试。");}finally{setSaving(false);}}
  if(!context||!state)return <p role="status">正在读取标注…</p>;
  return <div className={styles.surface} ref={root}>
    <div className={styles.scopeControls} style={{marginBottom:16}}><label>查看<SearchSelect aria-label="编码比较版本" value={snapshot||""} onChange={e=>{choose("snapshot",e.target.value);if(snapshotId){if(e.target.value)context.openData?.(e.target.value);else{localStorage.setItem(`coding-agreement:${projectId}`,JSON.stringify({...selection,snapshot:""}));context.openAgreement?.();}}}}><option value="">当前标注</option>{history.data?.map(item=><option key={item.id} value={item.id}>{item.title} · {new Date(item.saved_at).toLocaleString()}</option>)}</SearchSelect></label></div>
    {!snapshot&&<>
      <div className={styles.scopeControls}>
        {(["coder_a","coder_b"] as const).map((key,index)=><label key={key}>编码者 {index===0?"A":"B"}<SearchSelect aria-label={`比较编码者${index===0?"A":"B"}`} value={selection[key]} onChange={e=>choose(key,e.target.value)}><option value="">选择编码者</option>{choices.data?.coders.map(c=><option key={c.id} value={c.id} disabled={selection[index===0?"coder_b":"coder_a"]===c.id}>{c.name}</option>)}</SearchSelect></label>)}
        <label>主题<SearchSelect aria-label="比较主题" value={selection.theme_id} onChange={e=>choose("theme_id",e.target.value)}><option value="">选择主题</option>{state.payload.themes.filter(t=>t.is_codable!==false).map(t=><option key={t.id} value={t.id}>{themePath(t,state.payload.themes)}</option>)}</SearchSelect></label>
      </div>
      <div className={styles.scopeControls} style={{marginTop:16}}>
        <label>材料类型<SearchSelect aria-label="比较材料类型" value={selection.modality} onChange={e=>choose("modality",e.target.value)}>{Object.entries(labels).map(([value,label])=><option value={value} key={value}>{label}</option>)}</SearchSelect></label>
        <label>{labels[selection.modality]}范围<SearchSelect aria-label="比较来源范围" value={selection.material_id} onChange={e=>choose("material_id",e.target.value)}><option value="">全部已读取{labels[selection.modality]}</option>{choices.data?.files.filter(f=>f.kind===selection.modality).map(f=><option key={f.id} value={f.id}>{f.name} · v{f.version}</option>)}</SearchSelect></label>
        <label>案例范围<SearchSelect aria-label="一致性案例范围" value={selection.case_key} onChange={e=>choose("case_key",e.target.value)}><option value="">全项目</option>{choices.data?.cases.map(c=><option key={c.key} value={c.key}>{c.title} · {c.group_title}</option>)}</SearchSelect></label>
      </div>
      <div className={styles.attributes}><CodingAttributeFilter state={state} busy={context.busy} change={context.change}/></div>
    </>}
    <div className={styles.toolbar}><span>{snapshot?`已保存比较 · ${data?.saved_at?new Date(data.saved_at).toLocaleString():""}`:"当前采用版本"}</span><div className={styles.tabs}>{!snapshot&&<button disabled={!available||!data?.sources.length||saving} onClick={()=>void save()}>{saving?"正在保存…":"保存比较"}</button>}<DownloadButton identity={JSON.stringify({request,snapshot})} disabled={!available} download={signal=>codingApi.exportAgreement(projectId,data?.request||request,signal,snapshot)}>导出比较 CSV</DownloadButton></div></div>
    {(error||choices.error||query.error||history.error||context.error)&&<p role="alert">{error||choices.error?.message||query.error?.message||history.error?.message||context.error}<button onClick={()=>{void choices.refetch();if(ready)void query.refetch();}}>重新读取</button></p>}
    {(choices.isFetching||query.isFetching)&&<p role="status">正在读取比较…</p>}
    {!ready&&<p className={styles.empty}>选择两位编码者和一个主题。</p>}
    {available&&<>
      <p className={styles.scope}>{data.theme.name} · {data.coder_a.name} / {data.coder_b.name}</p>
      <p className={styles.method}>{data.scope_label}</p>
      {!data.sources.length?<p className={styles.empty}>当前范围没有可比较的{labels[data.modality]}来源。</p>:<>
        {data.modality==="text"&&data.summary?<><Numbers value={data.summary} unit="字符数" a={data.coder_a.name} b={data.coder_b.name}/><p className={styles.count}>{data.sources.map(s=>`${s.filename} · v${s.material_version} · 第 ${s.page} 页 · ${number(s.total)} 字符`).join("；")}</p></>:data.sources.map(source=><section key={source.material_id} style={{marginTop:24}}><h3 className={styles.differenceHeading}>{source.filename} · v{source.material_version}</h3><p className={styles.method}>{source.width?`原图 ${source.width} × ${source.height} 像素`:`完整时长 ${number(source.total)} 秒`}</p><Numbers value={source} unit={source.unit||""} a={data.coder_a.name} b={data.coder_b.name}/>{source.duration_missing&&<p>尚无法读取完整时长；仅计算已知标注的交集与分歧。</p>}</section>)}
        <details style={{marginTop:16}}><summary>计算方法</summary><p className={styles.method}>{data.unit}。总体一致率包含双方未编码部分；交并比只计算至少一方编码的部分。无编码时交并比为空，编码状态无变化时 κ 为空。{data.modality!=="text"&&"每个原文件分别计算。"}</p></details>
        <h3 className={styles.differenceHeading}>编码分歧</h3>
        {data.differences.length?<div className={styles.tableScroll}><table className={styles.details}><thead><tr><th>来源</th><th>编码差异</th><th>位置</th></tr></thead><tbody>{data.differences.map((d,index)=>{const loc=d.target.locator;return <tr key={`${d.target.material_id}:${index}`}><td className={styles.source}>{d.target.filename}<small>v{d.target.material_version}{loc.kind==="text"?` · 第 ${loc.page} 页`:""}</small></td><td>仅 {d.kind==="only_a"?data.coder_a.name:data.coder_b.name} 编码<small>{d.area!=null?`${number(d.area)} 像素²`:`${number((d.end||0)-(d.start||0))} ${data.modality==="text"?"字符":"秒"}`}</small></td><td><button className={styles.excerpt} onClick={()=>void reveal(d.target)}>{loc.kind==="text"?<><span>{loc.quote}</span><small>回到原文 · [{d.start}, {d.end})</small></>:loc.kind==="region"?<><span>回到原图区域</span><small>({d.pixels?.[0]}, {d.pixels?.[1]}) — ({d.pixels?.[2]}, {d.pixels?.[3]})</small></>:<><span>播放原{data.modality==="audio"?"音频":"视频"}选段</span><small>{number(d.start)} — {number(d.end)} 秒</small></>}</button></td></tr>;})}</tbody></table></div>:<p className={styles.empty}>所选主题没有编码分歧。</p>}
        {data.total_differences>data.page_size&&<div className={styles.pagination}><button disabled={!page} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{page+1} / {Math.ceil(data.total_differences/data.page_size)}</span><button disabled={(page+1)*data.page_size>=data.total_differences} onClick={()=>setPage(p=>p+1)}>下一页</button></div>}
      </>}
      {!!data.unrecorded&&<p>{data.unrecorded} 条标注未记录编码者，未归入任一方。</p>}
      {!!data.excluded.length&&<p>{data.excluded.join("；")}</p>}
    </>}
  </div>;
}
