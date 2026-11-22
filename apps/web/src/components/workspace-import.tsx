"use client";

import { FileImport } from "./file-import";
import { useRouter } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
export function WorkspaceImport({ projectId, onImported, onClose }: {
  projectId: string; onImported: () => void; onClose: () => void;
}) {
  const router = useRouter();
  return <section className="rw-import-panel" aria-label="导入项目文件">
    <div className="rw-content-tools"><h3>导入项目文件</h3><button onClick={onClose}>收起</button></div>
    <FileImport projectId={projectId} onImported={(openedId) => { onImported(); onClose(); if (openedId !== projectId) navigateToAppRoute(router, `/projects/${openedId}/workspace`); }} />
  </section>;
}
