"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FolderOpenIcon, FilePlusIcon } from "lucide-react";
import { Button } from "./ui/button";
import { projectsApi } from "@/lib/api-endpoints";
import { apiFetch, apiUpload } from "@/lib/api-client";
import { isTauri } from "@/lib/tauri-adapter";
import type { Project } from "@/lib/types";
import { WorkspaceFolderPicker } from "./workspace-folder-picker";
import { useLanguage } from "@/providers/language-provider";
import { QdpxImportSummary, type QdpxSummary } from "./qdpx-import-summary";

type Entry = { file: File; path: string };
export function FileImport({ projectId, home = false, onImported }: {
  projectId?: string;
  home?: boolean;
  onImported?: (projectId: string, datasetId: string) => void;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => locale === "zh-CN" ? zh : en;
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(false);
  const target = useRef(projectId);
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState<Entry[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [importedQdpx, setImportedQdpx] = useState<{ project: { id: string }; summary: QdpxSummary } | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function receive(entries: Entry[], isFolder: boolean, retry = false) {
    if (active.current || !entries.length) return;
    active.current = true;
    setBusy(true);
    setErrors([]);
    setFailed([]);
    setImportedQdpx(null);
    setMessage("");
    controller.current = new AbortController();
    const signal = controller.current.signal;
    try {
      if (entries.some(entry => /\.qdpx$/i.test(entry.file.name))) {
        if (isFolder || entries.length !== 1) throw new Error(copy("请单独选择一个 QDPX 文件导入。", "Choose one QDPX file on its own."));
        if (!isTauri()) throw new Error(copy("请在桌面应用中导入 QDPX，以便选择工作区位置。", "Import QDPX in the desktop app to choose a workspace location."));
        const native = (window as unknown as { __TAURI_INTERNALS__: { invoke: (command: string, args: unknown) => Promise<unknown> } }).__TAURI_INTERNALS__;
        const directory = await native.invoke("plugin:dialog|open", { options: { title: copy("选择新工作区的存放位置", "Choose where to create the workspace"), directory: true, multiple: false } });
        if (directory === null || signal.aborted) return;
        if (typeof directory !== "string" || !directory) throw new Error(copy("无法选择存放位置，请重试。", "Could not select a location. Try again."));
        const form = new FormData();
        form.append("file", entries[0].file);
        form.append("directory", directory);
        setMessage(copy("正在导入 QDPX…", "Importing QDPX…"));
        const result = await apiUpload<{ project: { id: string }; workspace_path: string; summary: QdpxSummary }>("/api/v1/projects/import-qdpx", form, { signal, onProgress: percent => setMessage(copy(`正在上传 ${percent}%`, `Uploading ${percent}%`)) });
        await client.invalidateQueries({ queryKey: ["projects"] });
        await client.invalidateQueries({ queryKey: ["datasets"] });
        setMessage(copy("QDPX 已导入新工作区", "QDPX imported into a new workspace"));
        setImportedQdpx(result);
        return;
      }
      if (!projectId && !isFolder && entries.length === 1 && /\.statforge$/i.test(entries[0].file.name)) {
        setMessage(copy("正在打开项目…", "Opening project…"));
        const project = await projectsApi.openFile(entries[0].file, signal);
        await client.invalidateQueries({ queryKey: ["projects"] });
        onImported?.(project.id, "");
        return;
      }
      if (!retry && !projectId) target.current = undefined;
      if (!target.current) {
        setMessage(copy("正在创建项目…", "Creating project…"));
        const title = isFolder ? entries[0].path.split("/")[0] : entries[0].file.name.replace(/\.[^.]+$/, "");
        target.current = (await projectsApi.create({ title: title.slice(0, 200) || "新项目" })).id;
      }
      const failures: Entry[] = [], reasons: string[] = [];
      for (let index = 0; index < entries.length; index++) {
        if (signal.aborted) break;
        const entry = entries[index];
        const status = `${index + 1} / ${entries.length} · ${entry.path}`;
        setMessage(status);
        const form = new FormData();
        form.append("file", entry.file);
        form.append("source_path", entry.path);
        try {
          await apiUpload(`/api/v1/projects/${target.current}/source-files`, form, {
            signal,
            onProgress: (percent) => setMessage(`${status} · ${percent}%`),
          });
        } catch (reason) {
          if (signal.aborted) return;
          failures.push(entry);
          reasons.push(`${entry.path}：${reason instanceof Error ? reason.message : copy("导入失败", "Import failed")}`);
        }
      }
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      setMessage(copy(`已导入 ${entries.length - failures.length} 个文件`, `Imported ${entries.length - failures.length} files`));
      setFailed(failures);
      setErrors(reasons);
      if (!failures.length && !signal.aborted) onImported?.(target.current!, "");
    } catch (reason) {
      if (!signal.aborted) {
        if (!entries.some(entry => /\.qdpx$/i.test(entry.file.name))) setFailed(entries);
        setErrors([reason instanceof Error ? reason.message : copy("导入失败，请重试", "Import failed. Try again.")]);
      }
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  function choose(list: FileList | null, isFolder: boolean) {
    if (list) void receive(Array.from(list).map(file => ({ file, path: file.webkitRelativePath || file.name })), isFolder);
  }
  async function openWorkspace(path: string) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setBrowsing(false);
    setErrors([]);
    try {
      setMessage(copy("正在打开…", "Opening…"));
      const project = await apiFetch<Project>("/api/v1/local-workspaces/open", { method: "POST", body: JSON.stringify({ path }) });
      client.setQueryData(["project", project.id], project);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["projects"] }),
        client.invalidateQueries({ queryKey: ["datasets", project.id] }),
        client.invalidateQueries({ queryKey: ["workspace-files", project.id] }),
        client.invalidateQueries({ queryKey: ["workspace-materials", project.id] }),
        client.invalidateQueries({ predicate: query => query.queryKey[0] === "research-tasks" && query.queryKey[2] === project.id }),
      ]);
      onImported?.(project.id, "");
      setMessage("");
    } catch (reason) { setErrors([reason instanceof Error ? reason.message : "无法打开工作区文件夹"]); }
    finally { active.current = false; setBusy(false); }
  }
  async function openFolder() {
    if (!isTauri()) { folder.current?.click(); return; }
    if (!home) { setBrowsing(true); return; }
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setErrors([]);
    try {
      const native = (window as unknown as { __TAURI_INTERNALS__: { invoke: (command: string, args: unknown) => Promise<unknown> } }).__TAURI_INTERNALS__;
      const directory = await native.invoke("plugin:dialog|open", { options: { title: copy("选择工作区文件夹", "Choose a workspace folder"), directory: true, multiple: false } });
      if (directory === null) return;
      if (typeof directory !== "string" || !directory) throw new Error(copy("无法选择文件夹，请重试。", "Could not select a folder. Try again."));
      active.current = false;
      await openWorkspace(directory);
    } catch (reason) {
      setErrors([reason instanceof Error ? reason.message : copy("无法选择文件夹，请重试。", "Could not select a folder. Try again.")]);
    } finally { active.current = false; setBusy(false); }
  }
  return (
    <div className={home ? "file-import is-home home-file-import" : "rw-folder-import"}>
      {browsing && <WorkspaceFolderPicker onClose={() => setBrowsing(false)} onChoose={path => void openWorkspace(path)} />}
      <input ref={folder} type="file" hidden multiple {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)} onChange={event => { choose(event.target.files, true); event.target.value = ""; }} />
      <input ref={files} type="file" hidden multiple onChange={event => { choose(event.target.files, false); event.target.value = ""; }} />
      <div className="file-import-actions">
        <Button className="home-open-button" disabled={busy} onClick={() => void openFolder()}><FolderOpenIcon size={18} />{copy("打开文件夹", "Open folder")}</Button>
        <Button variant="ghost" disabled={busy} onClick={() => files.current?.click()}><FilePlusIcon size={18} />{copy("选择文件", "Choose files")}</Button>
      </div>
      {message && <p role="status" aria-live="polite">{busy ? copy("正在导入：", "Importing: ") : ""}{message}</p>}
      {importedQdpx && <div><QdpxImportSummary summary={importedQdpx.summary} /><Button onClick={() => onImported?.(importedQdpx.project.id, "")}>{copy("进入项目", "Open project")}</Button></div>}
      {errors.length > 0 && <div role="alert" className="rw-error">{errors.map((error, i) => <p key={i}>{error}</p>)}</div>}
      {!busy && failed.length > 0 && <div className="rw-inline-actions">
        <Button onClick={() => void receive(failed, false, true)}>{copy("重试未导入文件", "Retry remaining files")}</Button>
        {target.current && <Button variant="ghost" onClick={() => onImported?.(target.current!, "")}>{copy("打开已导入项目", "Open project")}</Button>}
      </div>}
    </div>
  );
}
