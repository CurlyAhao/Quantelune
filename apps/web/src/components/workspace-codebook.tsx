"use client";

import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowDownIcon, ArrowUpIcon, FolderIcon, PencilIcon, PlusIcon, Undo2Icon, UploadIcon } from "lucide-react";
import { SearchSelect } from "./ui/search-select";
import { DownloadButton } from "./download-button";
import { codingApi, themePath, type CodingState, type CodingTheme, type CodingChange, type ThemeColor } from "@/lib/text-coding";
import styles from "./workspace-annotations.module.css";

const palette: Record<ThemeColor, string> = { green: "#34735A", blue: "#326BA0", amber: "#926414", purple: "#7953A1", rose: "#A44867", neutral: "#666B63" };
const colorNames: Record<ThemeColor, string> = { green: "绿色", blue: "蓝色", amber: "琥珀", purple: "紫色", rose: "玫红", neutral: "灰色" };
type Edit = { id?: string; name: string; memo: string; parent_id: string | null; color: ThemeColor; color_rgb: string | null; is_codable: boolean };

export function WorkspaceCodebook({ state, busy, change }: { state?: CodingState; busy: boolean; change: (body: CodingChange) => Promise<boolean> }) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [merge, setMerge] = useState(false);
  const [target, setTarget] = useState("");
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const themes = state?.payload.themes || [];
  const locked = busy || importing || !state;
  const ordered: (CodingTheme & { depth: number })[] = [];
  const visit = (parent: string | null, depth = 0) => themes.filter(t => t.parent_id === parent).forEach(t => { ordered.push({ ...t, depth }); visit(t.id, depth + 1); });
  visit(null);
  const isBelow = (id: string, ancestor?: string) => {
    let item = themes.find(t => t.id === id);
    while (item) { if (item.id === ancestor) return true; item = themes.find(t => t.id === item?.parent_id); }
    return false;
  };
  const siblings = themes.filter(t => t.parent_id === themes.find(t => t.id === edit?.id)?.parent_id);
  const position = siblings.findIndex(t => t.id === edit?.id);
  function select(theme?: CodingTheme, codable = true) {
    setEdit(theme ? { id: theme.id, name: theme.name, memo: theme.memo, parent_id: theme.parent_id, color: theme.color, color_rgb: theme.color_rgb || null, is_codable: theme.is_codable !== false }
      : { name: "", memo: "", parent_id: null, color: "green", color_rgb: null, is_codable: codable });
    setTarget(""); setMerge(false); setNotice("");
  }
  async function importFile(file?: File) {
    if (!file || !state || locked) return;
    setImporting(true); setError(""); setNotice("");
    try {
      const saved = await codingApi.change(state.project_id, state.revision, { operation: "import_codebook", codebook_xml: await file.text() }) as CodingState & {codebook_import?: {added: number; existing: number}};
      queryClient.setQueryData(["coding", state.project_id], saved);
      setEdit(null);
      setNotice(`已导入 ${saved.codebook_import?.added || 0} 项${saved.codebook_import?.existing ? `；${saved.codebook_import.existing} 项已存在，保留项目中的编辑` : ""}。`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "代码本未导入，请重新选择文件。"); }
    finally { setImporting(false); if (input.current) input.current.value = ""; }
  }
  async function save() {
    if (!edit) return;
    const { id, ...fields } = edit;
    if (await change({ operation: id ? "edit_theme" : "add_theme", theme_id: id, ...fields, name: fields.name.trim() })) setEdit(null);
  }
  return <details className={styles.manage}>
    <summary>标签管理</summary>
    <div className={`${styles.actions} ${styles.codebookTools}`}>
      <button type="button" disabled={locked} onClick={() => input.current?.click()}><UploadIcon size={14}/>{importing ? "正在导入…" : "导入 QDC"}</button>
      <input ref={input} hidden type="file" accept=".qdc,application/xml,text/xml" aria-label="选择 QDC 代码本" onChange={event => void importFile(event.target.files?.[0])}/>
      <DownloadButton identity={`${state?.revision}:qdc`} disabled={locked || !themes.length} download={signal => codingApi.export(state!.project_id, "qdc", signal)}>导出 QDC</DownloadButton>
      <button type="button" disabled={locked || !state?.payload.undo_stack.length} onClick={() => void change({ operation: "undo" }).then(ok => { if (ok) { setEdit(null); setNotice(""); } })}><Undo2Icon size={14}/>撤销</button>
    </div>
    {notice && <p role="status" className={styles.hint}>{notice}</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
    <div className={styles.themeList}>{ordered.map(theme => <div key={theme.id} style={{ paddingLeft: Math.min(theme.depth * 12, 96) }}>
      <button type="button" disabled={locked} aria-pressed={edit?.id === theme.id} title={themePath(theme, themes)} onClick={() => select(theme)}>
        {theme.is_codable === false ? <FolderIcon size={14}/> : <span className={styles.dot} style={{ background: theme.color_rgb || palette[theme.color] }}/>}
        <span className={styles.codebookName}>{theme.name}</span><small>{theme.is_codable === false ? "分组" : "标签"}</small><PencilIcon size={12}/>
      </button>
    </div>)}</div>
    <div className={styles.actions}>
      <button type="button" disabled={locked} onClick={() => select()}><PlusIcon size={14}/>新建标签</button>
      <button type="button" disabled={locked} onClick={() => select(undefined, false)}><FolderIcon size={14}/>新建分组</button>
    </div>
    {edit && <div className={styles.codebookEditor}>
      <label className={styles.field}>{edit.is_codable ? "标签名称" : "分组名称"}<input aria-label={edit.is_codable ? "标签名称" : "分组名称"} maxLength={80} value={edit.name} onChange={event => setEdit({ ...edit, name: event.target.value })}/></label>
      <label className={styles.field}>上级<SearchSelect aria-label="标签上级" value={edit.parent_id || ""} onChange={event => setEdit({ ...edit, parent_id: event.target.value || null })}>
        <option value="">根级</option>{ordered.filter(t => !isBelow(t.id, edit.id)).map(t => <option key={t.id} value={t.id}>{themePath(t, themes)}{t.is_codable === false ? " · 分组" : " · 标签"}</option>)}
      </SearchSelect></label>
      {edit.is_codable && <label className={styles.field}>颜色<SearchSelect aria-label="标签颜色" value={edit.color_rgb ? "original" : edit.color} onChange={event => { if (event.target.value !== "original") setEdit({ ...edit, color: event.target.value as ThemeColor, color_rgb: null }); }}>
        {edit.color_rgb && <option value="original">当前颜色 {edit.color_rgb}</option>}{Object.entries(colorNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </SearchSelect></label>}
      <label className={styles.field}>说明<textarea aria-label="主题说明" rows={2} maxLength={2000} value={edit.memo} onChange={event => setEdit({ ...edit, memo: event.target.value })}/></label>
      <div className={styles.actions}>
        <button type="button" className={styles.primary} disabled={locked || !edit.name.trim()} onClick={() => void save()}>{edit.id ? "保存修改" : edit.is_codable ? "创建标签" : "创建分组"}</button>
        <button type="button" disabled={locked} onClick={() => setEdit(null)}>取消</button>
        {edit.id && <><button type="button" aria-label="上移标签或分组" disabled={locked || position <= 0} onClick={() => void change({ operation: "move_theme", theme_id: edit.id, direction: "up" })}><ArrowUpIcon size={14}/>上移</button><button type="button" aria-label="下移标签或分组" disabled={locked || position < 0 || position >= siblings.length - 1} onClick={() => void change({ operation: "move_theme", theme_id: edit.id, direction: "down" })}><ArrowDownIcon size={14}/>下移</button></>}
      </div>
      {edit.id && <div className={styles.actions}>
        {edit.is_codable && <button type="button" disabled={locked} onClick={() => { setMerge(value => !value); setTarget(""); }}>合并到其他标签</button>}
        <button type="button" disabled={locked} onClick={() => void change({ operation: "delete_theme", theme_id: edit.id }).then(ok => { if (ok) setEdit(null); })}>{edit.is_codable ? "删除标签" : "删除分组"}</button>
      </div>}
      {merge && <div>
        <label className={styles.field}>目标标签<SearchSelect aria-label="合并目标标签" value={target} onChange={event => setTarget(event.target.value)}><option value="">选择标签</option>{ordered.filter(t => t.is_codable !== false && !isBelow(t.id, edit.id)).map(t => <option key={t.id} value={t.id}>{themePath(t, themes)}</option>)}</SearchSelect></label>
        {target && <><p className={styles.hint}>“{edit.name}”的 {state?.payload.segments.filter(s => s.theme_ids.includes(edit.id!)).length || 0} 条标注和下级移到“{themePath(themes.find(t => t.id === target)!, themes)}”。目标的名称、颜色和说明保留；已保存的汇总不变。</p><button type="button" disabled={locked} onClick={() => void change({ operation: "merge_theme", theme_id: edit.id, target_id: target }).then(ok => { if (ok) { setEdit(null); setNotice("已合并，可用“撤销”恢复。"); } })}>合并标签</button></>}
      </div>}
    </div>}
  </details>;
}
