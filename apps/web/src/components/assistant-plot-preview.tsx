"use client";
import { useEffect,useState } from "react";
import { plotsApi } from "@/lib/plots-api";
import { readApiFileBlob } from "@/lib/api-download";

export function AssistantPlotPreview({project,id,title}:{project:string;id:string;title:string}) {
  const [url,setUrl]=useState(""),[error,setError]=useState("");
  useEffect(()=>{
    const controller=new AbortController();let objectUrl="";setUrl("");setError("");
    void readApiFileBlob(plotsApi.file(project,id,"png"),controller.signal).then(blob=>{
      if(controller.signal.aborted)return;objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);
    }).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:"无法读取图形预览，请打开结果重试。");});
    return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[project,id]);
  return <figure className="pa-plot-preview">{url ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={url} alt={title}/> : <p role={error?"alert":"status"}>{error||"正在读取已生成图形…"}</p>}<figcaption>{title}</figcaption></figure>;
}
