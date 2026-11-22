"use client";

import {useEffect,useLayoutEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {journalApi,projectMemoApi,type JournalCitation,type JournalDocument,type JournalRecord,type JournalReference} from "@/lib/journals";
import {themePath,type CodedSegment} from "@/lib/text-coding";
import {useWorkspaceAnnotations,annotationLocationLabel} from "./workspace-annotations";
import {DownloadButton} from "./download-button";
import {ApiError} from "@/lib/api-client";
import styles from "./research-journals.module.css";

type Draft = {id?:string;revision:number;document:JournalDocument;dirty:boolean;pending:Record<string,JournalReference>};
function fromRecord(row:JournalRecord):Draft{return {id:row.id,revision:row.revision,document:row.payload,dirty:false,pending:{}};}
const stamp=(value:string)=>new Date(value).toLocaleString("zh-CN",{hour12:false});
const MEMO="project-memo";
function citationPosition(c:JournalCitation){
  const loc=c.segment.locator;
  return loc.kind==="text"?`第 ${loc.page} 页 · 字符 ${loc.start}–${loc.end}`:annotationLocationLabel(loc);
}
function citationQuote(c:JournalCitation){
  const loc=c.segment.locator;
  return loc.kind==="text"?loc.quote:loc.kind==="time"?loc.transcription?.quote||loc.note:loc.kind==="region"?loc.note:"";
}

export function ResearchJournals({projectId,active,onMemoChange}:{projectId:string;active:boolean;onMemoChange:(memo:boolean)=>void}) {
  const context=useWorkspaceAnnotations(),client=useQueryClient();
  const key=`statforge:journal-drafts:${projectId}`;
  const [drafts,setDrafts]=useState<Record<string,Draft>>(()=>{
    try{return JSON.parse(sessionStorage.getItem(key)||"{}");}catch{return {};}
  });
  const [selected,setSelected]=useState(()=>{try{return sessionStorage.getItem(key+":selected")||"";}catch{return "";}});
  const [search,setSearch]=useState("");
  const [choosing,setChoosing]=useState(false),[codeSearch,setCodeSearch]=useState("");
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
  const [deleting,setDeleting]=useState(false);
  const [conflict,setConflict]=useState(false);
  const [memoLatest,setMemoLatest]=useState<JournalRecord|null>(null);
  const isMemo=selected===MEMO;
  useEffect(()=>onMemoChange(isMemo),[isMemo,onMemoChange]);
  const root=useRef<HTMLDivElement>(null),savedScroll=useRef(0);
  const list=useQuery({queryKey:["journals",projectId],queryFn:({signal})=>journalApi.list(projectId,signal),enabled:active});
  const record=useQuery({queryKey:["journal",projectId,selected],queryFn:({signal})=>journalApi.read(projectId,selected,signal),enabled:active&&!!selected&&!isMemo&&!selected.startsWith("new:")});
  const memo=useQuery({queryKey:["project-memo",projectId],queryFn:({signal})=>projectMemoApi.read(projectId,signal),enabled:active&&isMemo});
  useEffect(()=>{if(!selected&&list.data?.length)setSelected(list.data[0].id);},[list.data,selected]);
  useEffect(()=>{if(record.data)setDrafts(old=>{
    const previous=old[record.data.id];
    return previous&&(previous.dirty||previous.revision===record.data.revision)?old:{...old,[record.data.id]:fromRecord(record.data)};
  });},[record.data]);
  useEffect(()=>{
    if(memo.data===undefined)return;
    setDrafts(old=>{
      const previous=old[MEMO];
      if(previous?.dirty||previous&&previous.revision===(memo.data?.revision||0))return old;
      return {...old,[MEMO]:memo.data?fromRecord(memo.data):{revision:0,dirty:false,pending:{},document:{schema_version:1,kind:"project_memo",title:"项目备忘",body:"",author_id:"",author_name:"",created_at:"",updated_at:"",citations:[],deleted:false}}};
    });
  },[memo.data]);
  useEffect(()=>{try{sessionStorage.setItem(key,JSON.stringify(drafts));}catch{/* Existing saved journals remain available. */}},[key,drafts]);
  useEffect(()=>{try{sessionStorage.setItem(key+":selected",selected);}catch{}},[key,selected]);
  useLayoutEffect(()=>{
    if(active){const id=requestAnimationFrame(()=>{const scroll=root.current?.closest(".rw-project-scroll");if(scroll)scroll.scrollTop=savedScroll.current;});return()=>cancelAnimationFrame(id);}
  },[active]);
  const draft=drafts[selected],doc=draft?.document;
  const rows=[...Object.entries(drafts).filter(([id])=>id.startsWith("new:")).map(([id,d])=>({id,title:d.document.title,author_name:d.document.author_name,updated_at:d.document.updated_at,excerpt:d.document.body})),...(list.data||[])];
  const visible=rows.filter(row=>{
    const current=drafts[row.id]?.document;
    return `${current?.title??row.title}\n${current?.body??row.excerpt}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase());
  });
  // Search saved full bodies on demand; list excerpts alone must not hide matches.
  const fullSearch=useQuery({queryKey:["journal-search",projectId,search],queryFn:async({signal})=>{
    const {apiFetch}=await import("@/lib/api-client");
    return apiFetch<typeof rows>(`/api/v1/projects/${projectId}/journals?q=${encodeURIComponent(search)}`,{signal});
  },enabled:active&&!!search.trim()});
  const shown=search.trim()?[...visible,...(fullSearch.data||[]).filter(row=>!visible.some(item=>item.id===row.id))]:visible;
  function edit(update:Partial<JournalDocument>){setDrafts(old=>({...old,[selected]:{...old[selected],dirty:true,document:{...old[selected].document,...update}}}));setMessage("");}
  function choose(id:string){setSelected(id);setError("");setMessage("");setConflict(false);setMemoLatest(null);setDeleting(false);setChoosing(false);savedScroll.current=0;}
  function create(){
    const id=`new:${crypto.randomUUID()}`,now=new Date().toISOString();
    setDrafts(old=>({...old,[id]:{revision:0,dirty:true,pending:{},document:{schema_version:1,kind:"journal",title:"新日志",body:"",author_id:context?.state?.current_coder?.id||"",author_name:context?.state?.current_coder?.name||"本地研究者",created_at:now,updated_at:now,citations:[],deleted:false}}}));
    setSearch("");choose(id);
  }
  async function save(asCopy=false){
    if(!draft||busy)return;
    if(!doc?.title.trim()){setError("请填写日志标题。");return;}
    setBusy(true);setError("");
    try{
      const payload={revision:asCopy?0:draft.revision,title:doc.title.trim()+(asCopy?"（编辑副本）":""),body:doc.body,citations:doc.citations.map(c=>asCopy?{coding_id:c.coding_id,coding_revision:c.coding_revision,segment_id:c.segment.id}:draft.pending[c.id]||{id:c.id})};
      const row=isMemo?await projectMemoApi.save(projectId,payload):await journalApi.save(projectId,payload,asCopy?undefined:draft.id);
      const nextKey=isMemo?MEMO:row.id;
      setDrafts(old=>{const next={...old};delete next[selected];next[nextKey]=fromRecord(row);return next;});
      setSelected(nextKey);client.setQueryData(isMemo?["project-memo",projectId]:["journal",projectId,row.id],row);
      await client.invalidateQueries({queryKey:["journals",projectId]});
      await client.invalidateQueries({queryKey:["journal-search",projectId]});
      setMessage("已保存");setConflict(false);
    }catch(reason){setError(reason instanceof Error?reason.message:"内容未保存，请重试。");setConflict(reason instanceof ApiError&&["JOURNAL_CONFLICT","MEMO_CONFLICT"].includes(reason.errorCode||""));}finally{setBusy(false);}
  }
  function insert(segment:CodedSegment){
    const state=context?.state;if(!state?.id||!draft)return;
    const id=crypto.randomUUID();
    const citation:JournalCitation={id,coding_id:state.id,coding_revision:state.revision,segment,themes:state.payload.themes.filter(t=>segment.theme_ids.includes(t.id)).map(t=>themePath(t,state.payload.themes)),case_title:segment.sample_title||"",coder_name:segment.coder_name||"未记录编码者"};
    setDrafts(old=>({...old,[selected]:{...old[selected],dirty:true,pending:{...old[selected].pending,[id]:{coding_id:state.id!,coding_revision:state.revision,segment_id:segment.id}},document:{...old[selected].document,citations:[...old[selected].document.citations,citation]}}}));
    setMessage("已插入摘录，保存后可回查原位置。");
  }
  async function reveal(citation:JournalCitation){
    if(!draft?.id||draft.pending[citation.id])return;
    setError("");setBusy(true);
    try{
      const target=isMemo?await projectMemoApi.target(projectId,citation.id):await journalApi.target(projectId,draft.id,citation.id);
      savedScroll.current=root.current?.closest(".rw-project-scroll")?.scrollTop||0;
      await context?.focus(target);
    }catch(reason){setError(reason instanceof Error?reason.message:"无法打开这条摘录的原材料。");}finally{setBusy(false);}
  }
  async function latestMemo(){
    setBusy(true);try{setMemoLatest(await projectMemoApi.read(projectId));}catch(reason){setError(reason instanceof Error?reason.message:"最新备忘未能读取，请重试。");}finally{setBusy(false);}
  }
  function continueMemo(){
    if(!memoLatest||!draft)return;
    // Keep the researcher's editor intact; advance its base only after showing the saved text.
    const pending={...draft.pending};
    for(const c of doc!.citations)if(!memoLatest.payload.citations.some(other=>other.id===c.id))pending[c.id]={coding_id:c.coding_id,coding_revision:c.coding_revision,segment_id:c.segment.id};
    setDrafts(old=>({...old,[MEMO]:{...old[MEMO],id:memoLatest.id,revision:memoLatest.revision,pending,dirty:true}}));
    setError("");setConflict(false);setMemoLatest(null);
  }
  async function remove(){
    if(!draft)return;setBusy(true);setError("");
    try{
      if(draft.id)await journalApi.remove(projectId,draft.id,draft.revision);
      setDrafts(old=>{const next={...old};delete next[selected];return next;});
      client.setQueryData(["journals",projectId],(old:typeof list.data)=>old?.filter(row=>row.id!==selected));
      setSelected(list.data?.find(row=>row.id!==selected)?.id||"");setDeleting(false);
      await client.invalidateQueries({queryKey:["journals",projectId]});
      await client.invalidateQueries({queryKey:["journal-search",projectId]});
    }catch(reason){setError(reason instanceof Error?reason.message:"日志未删除。 ");}finally{setBusy(false);}
  }
  const codes=context?.state?.payload.segments||[],themes=context?.state?.payload.themes||[];
  const codeRows=codes.filter(s=>`${s.filename} ${s.coder_name||""} ${annotationLocationLabel(s.locator)} ${themes.filter(t=>s.theme_ids.includes(t.id)).map(t=>themePath(t,themes)).join(" ")}`.toLocaleLowerCase().includes(codeSearch.trim().toLocaleLowerCase()));
  return <div ref={root} className={styles.surface}>
    <div className={styles.toolbar}><button className={styles.memoEntry} aria-pressed={isMemo} onClick={()=>choose(MEMO)} disabled={busy}>项目备忘</button><input aria-label="查找日志" placeholder="查找标题或正文" value={search} onChange={e=>setSearch(e.target.value)}/><button onClick={create} disabled={busy}>新建日志</button><DownloadButton identity={`journals:${projectId}:${list.dataUpdatedAt}`} disabled={!list.data?.length||Object.entries(drafts).some(([id,d])=>id!==MEMO&&d.dirty)} download={signal=>journalApi.export(projectId,signal)}>导出全部 HTML</DownloadButton></div>
    <div className={styles.layout}>
      <nav className={styles.list} aria-label="研究日志列表">
        {list.isPending&&<p>正在读取日志…</p>}
        {shown.map(row=><button key={row.id} aria-pressed={selected===row.id} onClick={()=>choose(row.id)} disabled={busy}><strong>{drafts[row.id]?.document.title||row.title}{drafts[row.id]?.dirty?" · 未保存":""}</strong><small>{row.author_name} · {stamp(row.updated_at)}</small></button>)}
        {!list.isPending&&!shown.length&&<p>{search?"没有匹配的日志。":"还没有研究日志。"}</p>}
        {list.error&&<p role="alert">{list.error.message}</p>}
      </nav>
      <section className={styles.editor} aria-label={isMemo?"项目备忘编辑":"日志编辑"}>
        {doc&&draft?<>
          {!isMemo&&<label className={styles.title}>标题<input aria-label="日志标题" value={doc.title} maxLength={160} disabled={busy} onChange={e=>edit({title:e.target.value})}/></label>}
          {doc.created_at&&<p className={styles.meta}>作者：{doc.author_name} · 创建 {stamp(doc.created_at)}<br/>更新 {stamp(doc.updated_at)}</p>}
          <label className={styles.body}>正文<textarea aria-label={isMemo?"项目备忘正文":"日志正文"} value={doc.body} disabled={busy} onChange={e=>edit({body:e.target.value})}/></label>
          {isMemo&&<p className={styles.meta} role="status">{draft.dirty?"有未保存的修改":draft.id?"已保存":"尚未保存"}</p>}
          <div className={styles.actions}><button className={styles.primary} onClick={()=>void save()} disabled={busy||(!draft.dirty&&(!isMemo||!!draft.id))||(isMemo&&conflict)}>{busy?"正在保存…":isMemo?"保存备忘":"保存日志"}</button><button onClick={()=>setChoosing(value=>!value)} disabled={busy} aria-expanded={choosing}>插入编码摘录</button><DownloadButton identity={`journal:${selected}:${draft.revision}`} disabled={draft.dirty||!draft.id||busy} download={signal=>isMemo?projectMemoApi.export(projectId,signal):journalApi.export(projectId,signal,draft.id)}>{isMemo?"导出备忘 HTML":"导出此篇 HTML"}</DownloadButton>{!isMemo&&<button className={styles.delete} onClick={()=>setDeleting(true)} disabled={busy}>删除日志</button>}</div>
          {deleting&&<div className={styles.actions}><span>删除“{doc.title}”？</span><button onClick={()=>void remove()} disabled={busy}>确认删除</button><button onClick={()=>setDeleting(false)}>取消</button></div>}
          {(error||(isMemo?memo.error:record.error)||context?.error)&&<p role="alert">{error||(isMemo?memo.error:record.error)?.message||context?.error}</p>}
          {conflict&&(isMemo?<button onClick={()=>void latestMemo()} disabled={busy}>查看最新备忘</button>:<button onClick={()=>void save(true)} disabled={busy}>另存当前编辑</button>)}
          {isMemo&&conflict&&memoLatest&&<div className={styles.conflict}><p>最新保存内容 · {stamp(memoLatest.payload.updated_at)}</p><pre>{memoLatest.payload.body||"（空白）"}</pre>{memoLatest.payload.citations.map(c=><p key={c.id}>{c.themes.join("、")} · {c.segment.filename} · {citationQuote(c)}</p>)}<button onClick={continueMemo}>已核对，继续编辑</button></div>}
          {message&&(!isMemo||message!=="已保存")&&<p role="status" className={styles.meta}>{message}</p>}
          {choosing&&<div className={styles.picker}><div className={styles.toolbar}><input aria-label="查找编码摘录" placeholder="查找文件、主题、编码者或原文" value={codeSearch} onChange={e=>setCodeSearch(e.target.value)}/><button onClick={()=>setChoosing(false)}>完成选择</button></div><div className={styles.candidates}>{codeRows.slice(0,50).map(segment=>{
            const added=doc.citations.some(c=>c.segment.id===segment.id&&c.coding_revision===context?.state?.revision);
            return <article key={segment.id}><div><strong>{themes.filter(t=>segment.theme_ids.includes(t.id)).map(t=>themePath(t,themes)).join("、")||"仅备注"}</strong><p>{annotationLocationLabel(segment.locator)}</p><small>{segment.filename} · {segment.coder_name||"未记录编码者"} · {segment.material_version?`v${segment.material_version}`:"信号"}</small></div><button disabled={added||busy} onClick={()=>insert(segment)}>{added?"已插入":"插入"}</button></article>;
          })}{!codeRows.length&&<p>没有匹配的编码。</p>}{codeRows.length>50&&<p>还有 {codeRows.length-50} 条，输入关键词缩小范围。</p>}</div></div>}
          {!!doc.citations.length&&<section className={styles.citations} aria-label="日志编码摘录"><h2>编码摘录</h2>{doc.citations.map((citation,index)=><article key={citation.id}>
            <div className={styles.citationHead}><strong>{index+1}. {citation.themes.join("、")||"仅备注"}</strong><button onClick={()=>edit({citations:doc.citations.filter(c=>c.id!==citation.id)})} disabled={busy} aria-label={`移除摘录 ${index+1}`}>移除</button></div>
            <p className={styles.meta}>{citation.segment.filename} · {citation.segment.material_version?`材料 v${citation.segment.material_version}`:"信号"} · {citation.coder_name}{citation.case_title?` · ${citation.case_title}`:""}</p>
            {!!citationQuote(citation)&&<blockquote>{citationQuote(citation)}</blockquote>}
            <p className={styles.meta}>{citationPosition(citation)}</p>
            {!!citation.segment.memo&&<p>{citation.segment.memo}</p>}
            <button className={styles.sourceLink} onClick={()=>void reveal(citation)} disabled={busy||!draft.id||!!draft.pending[citation.id]}>{draft.pending[citation.id]?"保存后回查":citation.segment.locator.kind==="text"?"回到原文":citation.segment.locator.kind==="region"?"查看原图区域":"播放原片段"}</button>
          </article>)}</section>}
        </>:<p className={styles.empty}>{isMemo?(memo.error?memo.error.message:"正在读取备忘…"):record.isFetching?"正在读取日志…":"选择一篇日志，或新建日志开始记录。"}</p>}
      </section>
    </div>
  </div>;
}
