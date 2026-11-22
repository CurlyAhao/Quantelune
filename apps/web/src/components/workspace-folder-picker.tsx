"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpIcon, FileIcon, FolderIcon, RefreshCwIcon } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Button } from "./ui/button";
import { useLanguage } from "@/providers/language-provider";

type Entry = { name: string; path: string; directory: boolean; bytes: number };
type Listing = { path: string; parent: string | null; drives: string[]; entries: Entry[] };

export function WorkspaceFolderPicker({ onChoose, onClose }: {
  onChoose: (path: string) => void; onClose: () => void;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => locale === "zh-CN" ? zh : en;
  const [listing, setListing] = useState<Listing | null>(null);
  const [path, setPath] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const request = useRef(0);
  async function browse(next?: string) {
    const version = ++request.current;
    setBusy(true); setError("");
    try {
      const native = (window as unknown as { __TAURI_INTERNALS__: { invoke: (name: string, args: unknown) => Promise<Listing> } }).__TAURI_INTERNALS__;
      const result = await native.invoke("browse_workspace_directory", { path: next || null });
      if (version !== request.current) return;
      setListing(result); setPath(result.path); setSearch(""); setSelected("");
    } catch (reason) {
      if (version === request.current) setError(typeof reason === "string" ? reason : reason instanceof Error ? reason.message : copy("无法读取文件夹，请核对路径后重试。", "Cannot read this folder. Check the path and try again."));
    } finally { if (version === request.current) setBusy(false); }
  }
  useEffect(() => { void browse(); return () => { request.current++; }; }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const entries = listing?.entries.filter(entry => entry.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())) ?? [];
  const files = listing?.entries.filter(entry => !entry.directory) ?? [];
  function size(bytes: number) {
    return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toLocaleString(locale, { maximumFractionDigits: 1 })} MB` : bytes >= 1024 ? `${(bytes / 1024).toLocaleString(locale, { maximumFractionDigits: 1 })} KB` : `${bytes} B`;
  }
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent className="sf-folder-picker">
      <DialogTitle>{copy("打开文件夹", "Open folder")}</DialogTitle>
      <DialogDescription className="sr-only">{copy("选择要打开的文件夹。", "Choose a folder to open.")}</DialogDescription>
      <form className="sf-folder-address" onSubmit={event => { event.preventDefault(); void browse(path); }}>
        <Button type="button" variant="outline" size="icon" aria-label={copy("上一级", "Parent folder")} disabled={busy || !listing?.parent} onClick={() => void browse(listing?.parent ?? undefined)}><ArrowUpIcon size={17} /></Button>
        <input aria-label={copy("文件夹路径", "Folder path")} value={path} onChange={event => setPath(event.target.value)} placeholder="F:\CMU-MOSI" spellCheck={false} />
        <Button type="submit" variant="outline" disabled={busy}>{copy("前往", "Go")}</Button>
        <Button type="button" variant="ghost" size="icon" aria-label={copy("刷新文件夹", "Refresh folder")} disabled={busy} onClick={() => void browse(listing?.path)}><RefreshCwIcon size={17} /></Button>
      </form>
      <div className="sf-folder-location-tools">
        <nav aria-label={copy("磁盘", "Drives")}>{listing?.drives.map(drive => <button key={drive} disabled={busy} onClick={() => void browse(drive)}>{drive.slice(0, 2)}</button>)}</nav>
        <input aria-label={copy("查找当前文件夹中的文件", "Find files in this folder")} placeholder={copy("查找文件…", "Find files…")} value={search} disabled={busy} onChange={event => setSearch(event.target.value)} />
      </div>
      {error && <p className="sf-folder-error" role="alert">{error}</p>}
      <div className="sf-folder-entries" aria-busy={busy}>
        <table><thead><tr><th>{copy("名称", "Name")}</th><th>{copy("类型", "Type")}</th><th>{copy("大小", "Size")}</th></tr></thead>
          <tbody>{!busy && entries.map(entry => <tr key={entry.path} className={selected === entry.path ? "selected" : ""}>
            <td><button className="sf-folder-entry" title={entry.name} onClick={() => { if (entry.directory) void browse(entry.path); else setSelected(entry.path); }}>
              {entry.directory ? <FolderIcon size={18} /> : <FileIcon size={18} />}<span>{entry.name}</span>
            </button></td>
            <td>{entry.directory ? copy("文件夹", "Folder") : entry.name.includes(".") ? entry.name.split(".").at(-1)?.toUpperCase() : copy("文件", "File")}</td>
            <td>{entry.directory ? "—" : size(entry.bytes)}</td>
          </tr>)}</tbody>
        </table>
        {busy && <p className="sf-folder-empty" role="status">{copy("正在读取文件夹…", "Reading folder…")}</p>}
        {!busy && !entries.length && !error && <p className="sf-folder-empty" role="status">{search ? copy("没有匹配的文件", "No matching files") : copy("文件夹为空", "This folder is empty")}</p>}
      </div>
      <div className="sf-folder-footer">
        <p role="status">{listing ? copy(`${files.length} 个文件`, `${files.length} files`) : ""}</p>
        <div><Button variant="ghost" onClick={onClose}>{copy("取消", "Cancel")}</Button><Button disabled={busy || !!error || !listing} onClick={() => listing && onChoose(listing.path)}>{copy("打开", "Open")}</Button></div>
      </div>
    </DialogContent>
  </Dialog>;
}
