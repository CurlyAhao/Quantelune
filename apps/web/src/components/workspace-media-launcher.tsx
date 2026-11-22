"use client";

import {useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {apiFetch} from "@/lib/api-client";
import type {Dataset} from "@/lib/types";
import {SearchSelect} from "./ui/search-select";
import {WorkspaceToolDialog} from "./workspace-tool-dialog";
import {WorkspaceMediaSamples} from "./workspace-media-samples";
import {MediaAssociationEditor} from "./workspace-media-processing";
import type { CaseSourceContext } from "./workspace-cases";
import {datasetTitle} from "@/lib/workspace-files";

export function MediaProcessingLauncher({dataset,datasets,taskId,onChoose,range,onSaved,caseSource}: {
  dataset:Dataset;datasets:Dataset[];taskId?:string;onChoose:(id:string)=>void;
  range?:{start:number;end:number};onSaved?:()=>Promise<void>;caseSource?:CaseSourceContext;
}) {
  const [open,setOpen]=useState(false);
  const [groupId,setGroupId]=useState<string|null>(null);
  const [openedRange,setOpenedRange]=useState(range);
  const candidates=datasets.filter(d=>d.media_case && d.upload_status!=="deleted" && (!caseSource || (d.media_case.owner_case?.collection_id===caseSource.collectionId && d.media_case.owner_case.sample_id===caseSource.sampleId)));
  const groups=useQuery({
    queryKey:["material-groups-for-source",dataset.id,candidates.map(d=>d.id).join(",")],
    enabled:open,
    queryFn:async({signal})=>{
      const related=await Promise.all(candidates.map(async d=>{
        const result=await apiFetch<{samples:{video_id?:string;audio_id?:string}[]}>(`/api/v1/datasets/${d.id}/samples`,{signal});
        return result.samples.some(s=>s.video_id===dataset.id||s.audio_id===dataset.id)?d:null;
      }));
      return related.filter((d):d is Dataset=>!!d).sort((a,b)=>b.created_at.localeCompare(a.created_at));
    },
  });
  const selected=groupId ?? groups.data?.[0]?.id ?? "";
  const group=datasets.find(d=>d.id===selected);
  const ownerSample=caseSource && datasets.find(d=>d.id===caseSource.collectionId)?.media_case?.samples?.find(s=>s.sample_id===caseSource.sampleId && !s.case_deleted);
  const allowedIds=new Set([ownerSample?.primary_id,ownerSample?.video_id,ownerSample?.audio_id,ownerSample?.text_id,ownerSample?.pitch_id,...(ownerSample?.members || []).map(m=>m.id)].filter(Boolean));
  const eligibleDatasets=caseSource ? datasets.filter(d=>allowedIds.has(d.id)) : datasets;
  return <>
    <button type="button" onClick={()=>{setOpenedRange(range);setOpen(true);}}>{dataset.file_format==="wav"?"声音特征":"声音特征与取帧"}</button>
    <WorkspaceToolDialog open={open} onClose={()=>setOpen(false)} title={dataset.file_format==="wav"?"声音特征":"声音特征与取帧"} subtitle={dataset.display_name||dataset.original_filename}>
      {groups.isPending&&<p role="status">正在读取材料关联…</p>}
      {groups.isError&&<p role="alert">材料关联未能读取。<button onClick={()=>void groups.refetch()}>重试</button></p>}
      {open && groups.isSuccess&&<>
        {(groups.data.length>0||groupId)&&<label className="rw-field">材料组
          <SearchSelect value={selected} onChange={event=>setGroupId(event.target.value)}>
            {[...groups.data,...(group&&!groups.data.some(d=>d.id===group.id)?[group]:[])].map(d=><option key={d.id} value={d.id}>{datasetTitle(d,datasets)}</option>)}
            <option value="">新建材料组</option>
          </SearchSelect>
        </label>}
        {group?<WorkspaceMediaSamples key={group.id} mode="processing" dataset={group} datasets={datasets} taskId={taskId}
          initialSourceId={dataset.id} initialRange={openedRange} onSaved={onSaved}
          onChoose={id=>{if(datasets.find(d=>d.id===id)?.media_case)setGroupId(id);else{setOpen(false);onChoose(id);}}}/>
        : <MediaAssociationEditor embedded dataset={dataset} datasets={eligibleDatasets} taskId={taskId} ownerCase={caseSource} onChoose={setGroupId}/>}
      </>}
    </WorkspaceToolDialog>
  </>;
}
