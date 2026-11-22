"use client";

import {useEffect,useLayoutEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";
import {saveGeneratedFile} from "@/lib/api-download";
import {materialApi} from "@/lib/material-api";
import {themePath,type CodedSegment,type CodingScope,type CodingCaseScope} from "@/lib/text-coding";
import {annotationLocationLabel,useWorkspaceAnnotations} from "./workspace-annotations";
import {CodingAttributeFilter} from "./coding-attribute-filter";
import {SearchSelect} from "./ui/search-select";
import {DownloadButton} from "./download-button";
import styles from "./coding-relations.module.css";

type Selection={row_kind:"case"|"file";theme_id:string;case_key:string;snapshot:string};
type Cell={row:string;column:string}|null;
type Matrix={case_scopes?:CodingCaseScope[];row_kind:"case"|"file";coding_revision:number;scope:CodingScope;rows:{id:string;name:string;detail:string}[];columns:{id:string;name:string;is_group:boolean}[];matrix:number[][];entries:{row_id:string;column_ids:string[];segment:CodedSegment}[];unassociated:number;unavailable:number;saved_at?:string};
type Saved={id:string;title:string;saved_at:string;scope_label:string};
const initial:Selection={row_kind:"case",theme_id:"",case_key:"",snapshot:""};
function restored(key:string){try{return JSON.parse(localStorage.getItem(key)||"{}");}catch{return {};}}

export function CodingMatrixView({projectId,active,snapshotId,currentOnOpen=false}:{projectId:string;active:boolean;snapshotId?:string;currentOnOpen?:boolean}){
  const context=useWorkspaceAnnotations(),state=context?.state,client=useQueryClient();
  const storageKey=`coding-matrix:${projectId}:${snapshotId||"current"}`;
  const [selection,setSelection]=useState<Selection>(()=>({...initial,...restored(storageKey).selection,...(currentOnOpen?{snapshot:""}:{})}));
  const [cell,setCell]=useState<Cell>(()=>currentOnOpen?null:restored(storageKey).cell||null),[page,setPage]=useState(0),[error,setError]=useState(""),[saving,setSaving]=useState(false);
  const root=useRef<HTMLDivElement>(null),scroll=useRef<number>(currentOnOpen?0:restored(storageKey).scroll||0),restoreScroll=useRef(true);
  const base=`/api/v1/projects/${projectId}/coding/matrix`,snapshot=snapshotId||selection.snapshot;
  const request={revision:state?.revision||0,row_kind:selection.row_kind,case_key:selection.case_key||null,theme_ids:selection.theme_id?[selection.theme_id]:null};
  const history=useQuery({queryKey:["coding-matrix-history",projectId],queryFn:({signal})=>apiFetch<Saved[]>(base+"/history",{signal}),enabled:active});
  const query=useQuery({queryKey:["coding-matrix",projectId,snapshot||request],queryFn:({signal})=>apiFetch<Matrix>(snapshot?base+"/saved/"+snapshot:base,{signal,...(!snapshot?{method:"POST",body:JSON.stringify(request)}:{})}),enabled:active&&!!state});
  useEffect(()=>{localStorage.setItem(storageKey,JSON.stringify({selection,cell,scroll:scroll.current}));},[storageKey,selection,cell]);
  const data=query.data,entries=data?.entries.filter(e=>cell&&e.row_id===cell.row&&e.column_ids.includes(cell.column))||[];
  const available=!!data&&!query.isFetching&&!query.error;
  useLayoutEffect(()=>{
    if(!active){restoreScroll.current=true;return;}
    if(available&&restoreScroll.current){const frame=requestAnimationFrame(()=>{const pane=root.current?.closest(".rw-project-scroll");if(pane){pane.scrollTop=scroll.current;restoreScroll.current=false;}});return()=>cancelAnimationFrame(frame);}
  },[active,available]);
  function choose(key:keyof Selection,value:string){setSelection(old=>({...old,[key]:value}));setCell(null);setPage(0);setError("");}
  async function save(){setSaving(true);setError("");try{const result=await apiFetch<Saved>(base+"/save",{method:"POST",body:JSON.stringify(request)});await Promise.all([client.invalidateQueries({queryKey:["coding-matrix-history",projectId]}),client.invalidateQueries({queryKey:["datasets"]})]);setSelection(old=>({...old,snapshot:result.id}));}catch(e){setError(e instanceof Error?e.message:"保存失败，请重试。");}finally{setSaving(false);}}
  async function reveal(segment:CodedSegment){
    setError("");
    try{if(segment.material_id){const source=await materialApi.get(segment.material_id);if(source.sha256!==segment.material_sha256||source.version!==segment.material_version)throw new Error("原材料版本已变化，请打开对应旧版本回查这条证据。");}
      scroll.current=root.current?.closest(".rw-project-scroll")?.scrollTop||0;
      localStorage.setItem(storageKey,JSON.stringify({selection,cell,scroll:scroll.current}));context?.focus(segment);
    }catch(e){setError(e instanceof Error?e.message:"无法打开此证据的原材料。");}
  }
  async function download(format:"matrix"|"evidence",signal:AbortSignal){const result=await apiFetch<{csv:string;filename:string}>(base+`/export?format=${format}${snapshot?`&snapshot_id=${snapshot}`:""}`,{method:"POST",body:JSON.stringify(request),signal});return saveGeneratedFile(new Blob([result.csv],{type:"text/csv;charset=utf-8"}),result.filename,signal,undefined,"zh-CN");}
  if(!context||!state)return <p role="status">正在读取标注…</p>;
  return <div ref={root} className={styles.surface}>
    <div className={styles.scopeControls}>
      <label>查看<SearchSelect aria-label="证据矩阵版本" value={snapshot||""} onChange={e=>{if(snapshotId){if(e.target.value)context.openData?.(e.target.value);else context.openMatrix?.(true);}else choose("snapshot",e.target.value);}}><option value="">当前标注</option>{history.data?.map(item=><option key={item.id} value={item.id}>{item.title} · {new Date(item.saved_at).toLocaleString()} · {item.scope_label}</option>)}</SearchSelect></label>
      {!snapshot&&<><label>按什么查看<SearchSelect aria-label="矩阵行类型" value={selection.row_kind} onChange={e=>choose("row_kind",e.target.value)}><option value="case">案例</option><option value="file">文件</option></SearchSelect></label>
      <label>主题<SearchSelect aria-label="矩阵主题" value={selection.theme_id} onChange={e=>choose("theme_id",e.target.value)}><option value="">全部主题</option>{state.payload.themes.map(t=><option key={t.id} value={t.id}>{themePath(t,state.payload.themes)}{t.is_codable===false?" · 主题组":""}</option>)}</SearchSelect></label>
      <label>案例范围<SearchSelect aria-label="矩阵案例范围" value={selection.case_key} onChange={e=>choose("case_key",e.target.value)}><option value="">全项目</option>{(data?.case_scopes||state.case_scopes)?.map(c=><option key={c.key} value={c.key}>{c.title} · {c.group_title}</option>)}</SearchSelect></label></>}
    </div>
    {!snapshot&&<div className={styles.attributes}><CodingAttributeFilter state={state} busy={context.busy} change={context.change}/></div>}
    <p className={styles.scope}>{data?.scope.scope_label||"当前范围"}{snapshot?" · 已保存汇总":" · 当前采用版本"}</p>
    <div className={styles.toolbar}><span className={styles.method}>每格为证据条数，主题组按不同证据合计。</span><div className={styles.tabs}>
      {!snapshot&&<button disabled={!available||saving||!data?.rows.length||!data?.columns.length} onClick={()=>void save()}>{saving?"正在保存…":"保存汇总"}</button>}
      <DownloadButton identity={JSON.stringify({request,snapshot})+":matrix"} disabled={!available} download={signal=>download("matrix",signal)}>导出矩阵 CSV</DownloadButton>
      <DownloadButton identity={JSON.stringify({request,snapshot})+":evidence"} disabled={!available} download={signal=>download("evidence",signal)}>导出证据 CSV</DownloadButton>
    </div></div>
    {(error||query.error||history.error||context.error)&&<p role="alert">{error||query.error?.message||history.error?.message||context.error}</p>}
    {query.isFetching&&<p role="status">正在读取矩阵…</p>}
    {available&&<>
      {!!data.unavailable&&<p>{data.unavailable} 条标注缺少来源版本，未计入矩阵。</p>}
      {data.row_kind==="case"&&!!data.unassociated&&<p>{data.unassociated} 条证据未归入案例，可切换为文件查看。</p>}
      {data.rows.length&&data.columns.length?<div className={styles.tableScroll}><table className={styles.matrix}><thead><tr><th>{data.row_kind==="case"?"案例":"文件"}</th>{data.columns.map(c=><th key={c.id} scope="col">{c.name}{c.is_group?"（组）":""}</th>)}</tr></thead><tbody>{data.rows.map((r,i)=><tr key={r.id}><th scope="row">{r.name}<small className={styles.rowDetail}>{r.detail}</small></th>{data.columns.map((c,j)=><td key={c.id}><button disabled={!data.matrix[i][j]} aria-label={`${r.name} · ${c.name}：${data.matrix[i][j]}条证据`} aria-pressed={cell?.row===r.id&&cell.column===c.id} onClick={()=>{setCell({row:r.id,column:c.id});setPage(0);}}>{data.matrix[i][j]}</button></td>)}</tr>)}</tbody></table></div>:<p className={styles.empty}>当前范围没有可显示的案例、文件或主题。</p>}
      {cell&&<section className={styles.evidenceList} aria-label="矩阵证据"><div className={styles.toolbar}><h3>{data.rows.find(r=>r.id===cell.row)?.name} · {data.columns.find(c=>c.id===cell.column)?.name}</h3><span>{entries.length} 条证据</span></div>
        {entries.slice(page*50,(page+1)*50).map(({segment:s})=><article key={s.id}><button className={styles.evidenceLink} onClick={()=>void reveal(s)}>{s.filename} · {annotationLocationLabel(s.locator)}{s.material_version?` · v${s.material_version}`:""}</button>{s.locator.kind==="text"&&<p>{s.locator.quote}</p>}{s.memo&&<p className={styles.memo}>{s.memo}</p>}</article>)}
        {!entries.length&&<p className={styles.empty}>此格没有证据。</p>}
        {entries.length>50&&<div className={styles.pagination}><button disabled={!page} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{page+1} / {Math.ceil(entries.length/50)}</span><button disabled={(page+1)*50>=entries.length} onClick={()=>setPage(p=>p+1)}>下一页</button></div>}
      </section>}
    </>}
  </div>;
}
