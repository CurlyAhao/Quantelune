import { apiFetch } from "./api-client";
import { saveGeneratedFile } from "./api-download";
import type { AnnotationTarget, CodedSegment } from "./text-coding";

export type JournalCitation = {
  id: string; coding_id: string; coding_revision: number; segment: CodedSegment;
  themes: string[]; case_title: string; coder_name: string;
};
export type JournalReference = {id?:string;coding_id?:string;coding_revision?:number;segment_id?:string};
export type JournalDocument = {
  kind:"journal"|"project_memo";schema_version:1;title:string;body:string;author_id:string;author_name:string;
  created_at:string;updated_at:string;citations:JournalCitation[];deleted:boolean;
};
export type JournalRecord = {id:string;project_id:string;revision:number;payload:JournalDocument};
export type JournalSummary = {id:string;revision:number;title:string;author_name:string;created_at:string;updated_at:string;excerpt:string;citation_count:number};
export type JournalSave = {revision:number;title:string;body:string;citations:JournalReference[]};
const root = (project:string) => `/api/v1/projects/${project}/journals`;
export const journalApi = {
  list:(project:string,signal?:AbortSignal)=>apiFetch<JournalSummary[]>(root(project),{signal}),
  read:(project:string,id:string,signal?:AbortSignal)=>apiFetch<JournalRecord>(`${root(project)}/${id}`,{signal}),
  save:(project:string,body:JournalSave,id?:string)=>apiFetch<JournalRecord>(root(project)+(id?`/${id}`:""),{method:id?"PUT":"POST",body:JSON.stringify(body)}),
  remove:(project:string,id:string,revision:number)=>apiFetch(`${root(project)}/${id}?revision=${revision}`,{method:"DELETE"}),
  target:(project:string,id:string,citation:string)=>apiFetch<AnnotationTarget>(`${root(project)}/${id}/citations/${citation}/target`),
  export:async(project:string,signal:AbortSignal,id?:string)=>{
    const data=await apiFetch<{html:string;filename:string}>(root(project)+`/export${id?`?journal_id=${id}`:""}`,{signal});
    return saveGeneratedFile(new Blob([data.html],{type:"text/html;charset=utf-8"}),data.filename,signal,undefined,"zh-CN");
  },
};

export const projectMemoApi = {
  read:(project:string,signal?:AbortSignal)=>apiFetch<JournalRecord|null>(`${root(project)}/project-memo`,{signal}),
  save:(project:string,body:JournalSave)=>apiFetch<JournalRecord>(`${root(project)}/project-memo`,{method:"PUT",body:JSON.stringify({revision:body.revision,body:body.body,citations:body.citations})}),
  target:(project:string,citation:string)=>apiFetch<AnnotationTarget>(`${root(project)}/project-memo/citations/${citation}/target`),
  export:async(project:string,signal:AbortSignal)=>{
    const data=await apiFetch<{html:string;filename:string}>(`${root(project)}/project-memo/export`,{signal});
    return saveGeneratedFile(new Blob([data.html],{type:"text/html;charset=utf-8"}),data.filename,signal,undefined,"zh-CN");
  },
};
