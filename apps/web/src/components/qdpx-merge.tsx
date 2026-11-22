"use client";

import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch, isApiError } from "@/lib/api-client";
import { flushProjectEditors } from "@/lib/project-save";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

type MergePreview = {
  token: string; project_name: string; added_sources: number; reused_sources: number;
  added_codings: number; reused_codings: number; added_journals: number;
  sources: { name: string; saved_name: string; action: "add" | "reuse" }[];
  conflicts: { kind: string; name: string; detail: string }[]; items: string[];
};

export function QdpxMerge({ projectId, onComplete, inputRef }: { projectId: string; onComplete: (message: string) => void; inputRef?: React.RefObject<HTMLInputElement | null> }) {
  const localInput = useRef<HTMLInputElement>(null);
  const input = inputRef ?? localInput, client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<MergePreview | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const message = (reason: unknown) => isApiError(reason) ? reason.detail : "未能合并，请重新选择 QDPX 文件。";

  async function inspect(selected: File) {
    setFile(selected); setPreview(null); setError(""); setOpen(true); setBusy(true);
    try {
      await flushProjectEditors(projectId);
      const body = new FormData(); body.append("file", selected);
      setPreview(await apiFetch<MergePreview>(`/api/v1/projects/${projectId}/merge-qdpx/preview`, { method: "POST", body }));
    } catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  }
  async function merge() {
    if (!preview || !file || busy) return;
    setBusy(true); setError("");
    try {
      const body = new FormData(); body.append("file", file); body.append("token", preview.token);
      const result = await apiFetch<{ added_sources: number; added_codings: number }>(`/api/v1/projects/${projectId}/merge-qdpx`, { method: "POST", body });
      await client.invalidateQueries({ predicate: query => query.queryKey.includes(projectId) });
      onComplete(`已合并：新增 ${result.added_sources} 份材料、${result.added_codings} 条编码`);
      setOpen(false);
    } catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  }
  return <>
    {!inputRef && <button type="button" disabled={busy} onClick={() => input.current?.click()}>合并 QDPX</button>}
    <input ref={input} type="file" accept=".qdpx" aria-label="选择要合并的 QDPX" className="hidden" onChange={event => {
      const selected = event.target.files?.[0]; event.target.value = "";
      if (selected) void inspect(selected);
    }} />
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value); }}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto" showCloseButton={!busy}>
        <DialogTitle>合并到当前项目</DialogTitle>
        {busy && !preview && <p role="status">正在读取交换包…</p>}
        {preview && <>
          <p className="font-medium break-words">{preview.project_name}</p>
          <p>新增 {preview.added_sources} 份材料、{preview.added_codings} 条编码{preview.added_journals > 0 ? `、${preview.added_journals} 篇日志` : ""}
            {(preview.reused_sources > 0 || preview.reused_codings > 0) && <span> · 复用 {preview.reused_sources} 份材料、{preview.reused_codings} 条编码</span>}</p>
          <ul className="max-h-48 overflow-y-auto space-y-2">
            {preview.sources.map((source, index) => <li key={index} className="flex gap-3 items-start">
              <span className="shrink-0 text-muted-foreground">{source.action === "add" ? "新增" : "复用"}</span>
              <span className="break-all">{source.saved_name}</span>
            </li>)}
          </ul>
          {preview.conflicts.length > 0 && <div className="space-y-2">
            <h3 className="font-medium">版本差异</h3>
            {preview.conflicts.map((item, index) => <p key={index} className="break-words"><span className="font-medium">{item.name}</span><br />{item.detail}</p>)}
          </div>}
          {preview.items.length > 0 && <ul className="space-y-1">{preview.items.map((item, index) => <li className="break-words" key={index}>{item}</li>)}</ul>}
        </>}
        {error && <p role="alert" className="text-destructive">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>取消</Button>
          {error && file && <Button variant="outline" disabled={busy} onClick={() => void inspect(file)}>重新预览</Button>}
          <Button disabled={busy || !preview || !!error} onClick={() => void merge()}>{busy && preview ? "正在合并…" : "合并项目"}</Button>
        </div>
      </DialogContent>
    </Dialog>
  </>;
}
