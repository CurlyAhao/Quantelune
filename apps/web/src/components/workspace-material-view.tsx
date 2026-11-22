"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageSquarePlusIcon } from "lucide-react";
import { materialApi } from "@/lib/material-api";
import { codepointOffset, type Material, type TextPage, type Location } from "@/lib/material-types";
import type { AnnotationLocation } from "@/lib/text-coding";
import { DownloadButton } from "./download-button";
import { MaterialViewer } from "./material-viewer";
import { PdfPageViewer } from "./pdf-page-viewer";
import { annotationColor, useAnnotationSource, useWorkspaceAnnotations } from "./workspace-annotations";
import styles from "./workspace-annotations.module.css";

type TextLocation = Extract<Location, { kind: "text" }>;
type DisplayPage = TextPage & { start: number };
const TEXT_RANGE_SIZE = 12000;
export function WorkspaceMaterial({ material, taskId, sampleId, sampleCollectionId }: { material: Material; taskId?: string; sampleId?: string; sampleCollectionId?: string }) {
  const textMaterial = ["text/plain", "text/markdown", "application/pdf"].includes(material.mime_type);
  return textMaterial ? <WorkspaceTextMaterial key={`${material.id}:${material.version}`} material={material} taskId={taskId} sampleId={sampleId} sampleCollectionId={sampleCollectionId} /> : <MaterialViewer material={material} page={1} location={null} taskId={taskId} sampleId={sampleId} sampleCollectionId={sampleCollectionId} />;
}

function WorkspaceTextMaterial({ material, taskId, sampleId, sampleCollectionId }: { material: Material; taskId?: string; sampleId?: string; sampleCollectionId?: string }) {
  const context=useWorkspaceAnnotations(),survey=material.metadata.survey_answer;
  const surveyCase=survey?context?.state?.case_scopes?.find(c=>c.group_id===survey.group_id&&c.sample_id===survey.case_id):undefined;
  const [selection, setSelection] = useState<TextLocation | null>(null);
  const [focus, setFocus] = useState<TextLocation | null>(null);
  const [selectionError, setSelectionError] = useState("");
  const [originalPage, setOriginalPage] = useState(1);
  const [originalOpen, setOriginalOpen] = useState(false);
  const [textPage, setTextPage] = useState<{ index: number } | { offset: number }>({ index: 0 });
  const longText = material.mime_type !== "application/pdf" && material.file_size > 128 * 1024;
  const source = { filename: material.filename, material_id: material.id, material_sha256: material.sha256, material_version: material.version, related_material_ids: material.metadata.annotation_material_ids, task_id: taskId, sample_id: sampleId||surveyCase?.sample_id, sample_collection_id: sampleCollectionId||surveyCase?.collection_id };
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => { setSelection(null); setFocus(null); setSelectionError(""); }, [material.id, material.sha256]);
  const annotations = useAnnotationSource(source, (locator: AnnotationLocation) => {
    if (locator.kind !== "text") return;
    setFocus(locator); setOriginalPage(locator.page);
    if (longText && locator.page === 1) setTextPage({ offset: locator.start });
    setOriginalOpen(true);
  });
  const text = useQuery({ queryKey: ["material-text", material.id, material.version], queryFn: ({ signal }) => materialApi.text(material.id, signal), enabled: !longText });
  const range = useQuery({
    queryKey: ["material-text-range", material.id, material.version, textPage],
    queryFn: ({ signal }) => materialApi.textRange(material.id, textPage, TEXT_RANGE_SIZE, signal),
    enabled: longText,
  });
  const rangeIndex = range.data?.index ?? 0;
  const pages: DisplayPage[] = longText ? (range.data ? [range.data] : []) : (text.data?.pages.map(page => ({ ...page, start: 0 })) ?? []);
  useEffect(() => {
    if (longText && range.data && !focus) root.current?.querySelector<HTMLElement>(".rw-text-preview")?.scrollTo({ top: 0 });
  }, [longText, range.data, focus]);
  useEffect(() => {
    if (!focus) return;
    const page = pages.find(item => item.page === focus.page);
    if (page && page.sha256 !== focus.text_sha256) { setSelectionError("原文已变化，无法定位这条标注。"); return; }
    root.current?.querySelector<HTMLElement>('[data-annotation-focused="true"]')?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focus, text.data, range.data]);
  function selectText(page: DisplayPage, element: HTMLElement) {
    annotations.activate();
    const selected = window.getSelection();
    if (!selected || selected.rangeCount !== 1 || selected.isCollapsed) { setSelection(null); return; }
    const range = selected.getRangeAt(0);
    if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) { setSelection(null); setSelectionError("请在同一页内选择文字。"); return; }
    const beforeStart = range.cloneRange(); beforeStart.selectNodeContents(element); beforeStart.setEnd(range.startContainer, range.startOffset);
    const beforeEnd = range.cloneRange(); beforeEnd.selectNodeContents(element); beforeEnd.setEnd(range.endContainer, range.endOffset);
    const start = codepointOffset(page.text, beforeStart.toString().length), end = codepointOffset(page.text, beforeEnd.toString().length);
    if (start === null || end === null || end <= start) return;
    const quote = Array.from(page.text).slice(start, end).join("");
    if (quote.length > 2000) { setSelectionError("这段文字较长，请缩短选段后标注。"); setSelection(null); return; }
    setSelection({ kind: "text", page: page.page, start: page.start + start, end: page.start + end, text_sha256: page.sha256, quote }); setSelectionError("");
  }
  function pageText(page: DisplayPage) {
    const chars = Array.from(page.text);
    const segments = annotations.segments.filter(segment => segment.material_id === material.id && segment.material_sha256 === material.sha256 && segment.material_version === material.version && segment.locator.kind === "text" && segment.locator.page === page.page && segment.locator.text_sha256 === page.sha256);
    const boundaries = new Set([0, chars.length]);
    for (const segment of segments) if (segment.locator.kind === "text") { boundaries.add(Math.max(0, segment.locator.start - page.start)); boundaries.add(Math.min(chars.length, segment.locator.end - page.start)); }
    if (focus?.page === page.page && focus.text_sha256 === page.sha256) { boundaries.add(Math.max(0, focus.start - page.start)); boundaries.add(Math.min(chars.length, focus.end - page.start)); }
    const offsets = Array.from(boundaries).filter(offset => offset >= 0 && offset <= chars.length).sort((a, b) => a - b);
    return offsets.slice(0, -1).map((start, index) => {
      const end = offsets[index + 1];
      const overlapping = segments.filter(segment => segment.locator.kind === "text" && segment.locator.start <= page.start + start && segment.locator.end >= page.start + end);
      const focused = focus?.page === page.page && focus.text_sha256 === page.sha256 && focus.start <= page.start + start && focus.end >= page.start + end;
      const value = chars.slice(start, end).join("");
      if (!overlapping.length && !focused) return <span key={start}>{value}</span>;
      const theme = annotations.themes.find(theme => overlapping.some(segment => segment.theme_ids.includes(theme.id)));
      const color = theme ? annotationColor(theme.color, theme.color_rgb) : "var(--rw-accent)";
      return <mark key={start} data-annotation-focused={focused || undefined} className={styles.textMark} style={{ "--annotation-color": color } as React.CSSProperties} title={overlapping.map(segment => segment.memo || segment.theme_ids.map(id => annotations.themes.find(theme => theme.id === id)?.name).filter(Boolean).join("、")).join("\n")} onDoubleClick={() => overlapping[0] && annotations.focus(overlapping[0])}>{value}</mark>;
    });
  }
  return <div ref={root} className="rw-material-view" onPointerDownCapture={annotations.activate}>
    {survey&&<div className="rw-inline-actions"><span>{survey.case_id} · {survey.question} · 回答 v{material.version}</span>
      <button onClick={()=>{sessionStorage.setItem("statforge:table-location:"+survey.source_id,JSON.stringify({row:survey.row,column:survey.question,sha256:survey.source_sha256,answer_id:material.metadata.source_dataset_id}));context?.openData?.(survey.source_id);}}>原表：{survey.source_name} · 第 {survey.row} 行</button>
      {surveyCase&&<button onClick={()=>context?.openData?.(surveyCase.collection_id)}>返回案例</button>}
    </div>}
    <div className={`rw-content-tools ${styles.previewTools}`}>
      <span>{material.filename}</span>
      <div className="rw-inline-actions">
        {annotations.enabled && <><button type="button" onClick={annotations.open}>标注 {annotations.segments.length || ""}</button><button type="button" disabled={!selection} onClick={() => selection && annotations.annotate(selection)}><MessageSquarePlusIcon size={15} />标注选中文字</button></>}
        <DownloadButton identity={material.id} download={signal => materialApi.download(material.id, material.filename, signal)}>导出原文件</DownloadButton>
      </div>
    </div>
    {selectionError && <p role="alert" className="rw-error">{selectionError}</p>}
    {(longText ? range.isPending : text.isPending) && <p role="status">正在读取内容…</p>}
    {(longText ? range.isError : text.isError) && <p role="alert">{(longText ? range.error : text.error)?.message}<button onClick={() => void (longText ? range.refetch() : text.refetch())}>重试</button></p>}
    {longText && range.data && <div className="rw-inline-actions" aria-label="文字分段">
      <button type="button" disabled={rangeIndex === 0} onClick={() => { setSelection(null); setFocus(null); setTextPage({ index: rangeIndex - 1 }); }}>上一段</button>
      <span>第 {rangeIndex + 1} / {Math.max(1, Math.ceil(range.data.total / TEXT_RANGE_SIZE))} 段</span>
      <button type="button" disabled={(rangeIndex + 1) * TEXT_RANGE_SIZE >= range.data.total} onClick={() => { setSelection(null); setFocus(null); setTextPage({ index: rangeIndex + 1 }); }}>下一段</button>
    </div>}
    {pages.map(page => <section key={page.page}>
      {material.mime_type === "application/pdf" && <><p className="rw-muted">第 {page.page} 页 <button type="button" onClick={() => { setOriginalOpen(originalPage !== page.page || !originalOpen); setOriginalPage(page.page); }}>{originalOpen && originalPage === page.page ? "收起原页" : "查看原页"}</button></p>{originalOpen && originalPage === page.page && <PdfPageViewer material={material} page={originalPage} onPageChange={setOriginalPage} />}</>}
      <pre className="rw-text-preview" data-page={page.page} onMouseUp={event => selectText(page, event.currentTarget)} onKeyUp={event => selectText(page, event.currentTarget)}>{pageText(page)}</pre>
    </section>)}
    {material.mime_type === "application/pdf" && text.data && !text.data.pages.length && <PdfPageViewer material={material} page={originalPage} onPageChange={setOriginalPage} />}
    {text.data && !text.data.pages.length && <p className="rw-muted">未提取到可选择的文字，可查看原文件。</p>}
  </div>;
}
