"use client";

import { useRef, useState } from "react";
import { ChevronDownIcon, SaveIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { projectsApi } from "@/lib/api-endpoints";
import { apiFetch, isApiError } from "@/lib/api-client";
import { downloadApiFile } from "@/lib/api-download";
import { flushProjectEditors } from "@/lib/project-save";
import { useLanguage } from "@/providers/language-provider";
import { QdpxImportSummary, type QdpxSummary } from "./qdpx-import-summary";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { QdpxMerge } from "./qdpx-merge";

export function ProjectFileControls({ projectId }: { projectId: string }) {
  const { locale } = useLanguage();
  const mergeInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [showSummary, setShowSummary] = useState(false);
  const summary = useQuery({
    queryKey: ["qdpx-summary", projectId],
    queryFn: ({ signal }) => apiFetch<QdpxSummary | null>(`/api/v1/projects/${projectId}/qdpx-summary`, { signal }),
  });
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: ({ signal }) => projectsApi.get(projectId, signal),
  });
  async function save() {
    if (busy) return;
    setBusy(true);
    setStatus("");
    try {
      await flushProjectEditors(projectId);
      if (project.data?.workspace_path) {
        await apiFetch(`/api/v1/projects/${projectId}/save-local-workspace`, { method: "POST" });
        setStatus(locale === "zh-CN" ? "已保存到工作区 .statforge" : "Saved to workspace .statforge");
        return;
      }
      const outcome = await downloadApiFile(
        `/api/v1/projects/${projectId}/package`,
        `${project.data?.title || "project"}.statforge`,
      );
      setStatus(
        locale === "zh-CN"
          ? outcome === "saved"
            ? "已保存"
            : "项目文件已交给浏览器保存"
          : outcome === "saved"
            ? "Saved"
            : "Project file sent to browser",
      );
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError"))
        setStatus(
          isApiError(error)
            ? error.detail
            : error instanceof Error &&
                error.message !== "Download unavailable."
              ? error.message
              : locale === "zh-CN"
                ? "未保存，请重试"
                : "Could not save. Try again.",
        );
    } finally {
      setBusy(false);
    }
  }
  async function exportQdpx() {
    if (busy) return;
    setBusy(true);
    setStatus("");
    try {
      await flushProjectEditors(projectId);
      const outcome = await downloadApiFile(`/api/v1/projects/${projectId}/export-qdpx`, `${project.data?.title || "project"}.qdpx`);
      setStatus(locale === "zh-CN" ? (outcome === "saved" ? "QDPX 已保存" : "QDPX 已交给浏览器保存") : (outcome === "saved" ? "QDPX saved" : "QDPX sent to browser"));
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError"))
        setStatus(isApiError(error) ? error.detail : error instanceof Error && error.message !== "Download unavailable." ? error.message : locale === "zh-CN" ? "导出失败，请重试" : "Export failed. Try again.");
    } finally { setBusy(false); }
  }
  return (
    <div className="project-file-controls">
      <button type="button" disabled={busy} onClick={() => void save()}>
        <SaveIcon className="size-4" />
        {locale === "zh-CN"
          ? busy
            ? "保存中…"
            : "保存项目"
          : busy
            ? "Saving…"
            : "Save project"}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger className="project-file-menu">{locale === "zh-CN" ? "项目文件" : "Project files"}<ChevronDownIcon size={14}/></DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-40">
          <DropdownMenuItem disabled={busy} onClick={() => void exportQdpx()}>{locale === "zh-CN" ? "导出 QDPX" : "Export QDPX"}</DropdownMenuItem>
          <DropdownMenuItem onClick={() => mergeInput.current?.click()}>{locale === "zh-CN" ? "合并 QDPX" : "Merge QDPX"}</DropdownMenuItem>
          {summary.data && <DropdownMenuItem onClick={() => setShowSummary(true)}>{locale === "zh-CN" ? "导入摘要" : "Import summary"}</DropdownMenuItem>}
        </DropdownMenuContent>
      </DropdownMenu>
      <QdpxMerge projectId={projectId} onComplete={setStatus} inputRef={mergeInput}/>
      {summary.data && <Dialog open={showSummary} onOpenChange={setShowSummary}><DialogContent className="max-h-[80vh] overflow-auto">
        <DialogTitle>{locale === "zh-CN" ? "导入摘要" : "Import summary"}</DialogTitle><QdpxImportSummary summary={summary.data}/>
      </DialogContent></Dialog>}
      {status && <span role="status">{status}</span>}
    </div>
  );
}
