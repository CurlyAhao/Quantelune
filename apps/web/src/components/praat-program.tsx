"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { isTauri } from "@/lib/tauri-adapter";

type PraatStatus = {available:boolean; version?:string; path?:string; message?:string};
const endpoint = "/api/v1/media-tools/praat";

export function PraatProgram() {
  const client = useQueryClient();
  const status = useQuery({queryKey:["praat-program"], queryFn:({signal})=>apiFetch<PraatStatus>(endpoint,{signal}),staleTime:30000,refetchOnWindowFocus:false});
  const [busy,setBusy] = useState(false), [error,setError] = useState("");
  async function choose() {
    setBusy(true);setError("");
    try {
      if (!isTauri()) throw new Error("请在桌面版中选择本机 Praat 程序。");
      const native=(window as unknown as {__TAURI_INTERNALS__:{invoke:(command:string,args:unknown)=>Promise<unknown>}}).__TAURI_INTERNALS__;
      const path=await native.invoke("plugin:dialog|open",{options:{title:"选择 Praat 程序",multiple:false,directory:false,filters:[{name:"Praat",extensions:["exe"]}],...(status.data?.path ? {defaultPath:status.data.path}:{})}});
      if (typeof path!=="string") return;
      const saved=await apiFetch<PraatStatus>(endpoint,{method:"PUT",body:JSON.stringify({path})});
      client.setQueryData(["praat-program"],saved);
    } catch(reason) {setError(reason instanceof Error?reason.message:"未能选择 Praat 程序，请重试。");}
    finally {setBusy(false);}
  }
  return <div>
    <div className="rw-inline-actions">
      <span>{status.data?.available ? "Praat "+status.data.version : status.isPending ? "正在读取 Praat…" : "尚未配置 Praat 7"}</span>
      <button type="button" disabled={busy} onClick={()=>void choose()}>{busy ? "正在读取…" : status.data?.available ? "更换 Praat 程序" : "选择 Praat 程序"}</button>
    </div>
    {(error || status.isError || status.data?.message) && <small className="rw-error" role="alert">{error || (status.isError ? "未能读取 Praat 配置，请重新选择程序。" : status.data?.message)}</small>}
  </div>;
}
