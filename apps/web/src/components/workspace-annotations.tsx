"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlusIcon, PencilIcon, Trash2Icon, Undo2Icon, XIcon } from "lucide-react";
import { codingApi, themePath, type AnnotationLocation, type AnnotationTarget, type CodedSegment, type CodingChange, type CodingState, type CodingTheme, type ThemeColor } from "@/lib/text-coding";
import { DownloadButton } from "./download-button";
import { WorkspaceCodebook } from "./workspace-codebook";
import { CodingAttributeFilter } from "./coding-attribute-filter";
import styles from "./workspace-annotations.module.css";

export type AnnotationSource = {
  filename: string;
  task_id?: string;
  material_id?: string;
  material_sha256?: string;
  material_version?: number;
  related_material_ids?: string[];
  dataset_id?: string;
  dataset_sha256?: string;
  source_fingerprint?: string;
  sample_id?: string;
  sample_collection_id?: string;
};
type Reveal = (locator: AnnotationLocation) => void | Promise<void>;
type Draft = { source: AnnotationSource; locator: AnnotationLocation; segment?: CodedSegment; token?: number };
type AnnotationContextValue = {
  comparisonTarget: AnnotationTarget | null;
  caseKey: string;
  setCaseKey: (key:string)=>void;
  openRelations?: ()=>void;
  openAgreement?: ()=>void;
  openMatrix?: (current?:boolean)=>void;
  openJournals?: ()=>void;
  openData?: (id:string)=>void;
  activeTaskId?: string;
  researchQuestion: string;
  open: boolean;
  setOpen: (value: boolean) => void;
  source: AnnotationSource | null;
  activate: (source: AnnotationSource, reveal: Reveal) => void;
  deactivate: (reveal: Reveal) => void;
  begin: (source: AnnotationSource, locator: AnnotationLocation, reveal: Reveal) => void;
  draft: Draft | null;
  setDraft: (draft: Draft | null) => void;
  focusedId: string;
  focus: (segment: AnnotationTarget) => void;
  preview: (locator: AnnotationLocation) => void;
  state: CodingState | undefined;
  busy: boolean;
  error: string;
  change: (change: CodingChange) => Promise<boolean>;
};
const AnnotationContext = createContext<AnnotationContextValue | null>(null);
const palette: Record<ThemeColor, string> = {
  green: "#34735a", blue: "#326ba0", amber: "#926414", purple: "#7953a1", rose: "#a44867", neutral: "#666b63",
};
function sourceKey(source: AnnotationSource | null) {
  return source ? `${source.material_id || source.dataset_id}:${source.material_sha256 || source.dataset_sha256}:${source.source_fingerprint || ""}:${source.task_id || ""}:${source.sample_id || ""}:${source.sample_collection_id || ""}` : "";
}
export function annotationMatches(segment: CodedSegment, source: AnnotationSource) {
  return source.material_id ? !!segment.material_id && (segment.material_id === source.material_id || !!source.related_material_ids?.includes(segment.material_id)) : segment.dataset_id === source.dataset_id;
}
function sameVersion(segment: AnnotationTarget, source: AnnotationSource) {
  return source.material_id ? segment.material_id === source.material_id && segment.material_sha256 === source.material_sha256 && segment.material_version === source.material_version : segment.dataset_id === source.dataset_id && segment.dataset_sha256 === source.dataset_sha256 && (!segment.source_fingerprint || segment.source_fingerprint === source.source_fingerprint);
}
export function transcriptVersionLabel(segment: CodedSegment, status?: "current" | "historical" | "unavailable", info?: {version?:number}) {
  const version = info?.version ?? (segment.locator.kind === "time" ? segment.locator.transcription?.version : undefined);
  if (!version && !status) return "";
  const label = status === "current" ? "当前" : status === "historical" ? "历史" : "待核验";
  return version ? `材料版本 ${version} · ${label}` : `材料版本 · ${label}`;
}
export function annotationLocationLabel(location: AnnotationLocation) {
  const seconds = (value: number) => Number(value.toFixed(3)).toLocaleString("zh-CN", { maximumFractionDigits: 3 });
  if (location.kind === "text") return `第 ${location.page} 页 · ${location.quote}`;
  if (location.kind === "region") return `图像区域 · ${Math.round(location.width * 100)}% × ${Math.round(location.height * 100)}%`;
  if (location.kind === "time" && location.point) return `时间点 · ${seconds(location.start_seconds)} 秒`;
  if (location.kind === "signal_time" && location.start_seconds > 1_000_000_000) return `${new Date(location.start_seconds * 1000).toISOString().replace("T", " ").slice(0, 23)} — ${new Date(location.end_seconds * 1000).toISOString().slice(11, 23)} UTC`;
  const range = `${seconds(location.start_seconds)} — ${seconds(location.end_seconds)} 秒`;
  return location.kind === "time" && location.transcription?.quote ? `${range} · ${location.transcription.quote}` : range;
}

function revealTranscript(segment: AnnotationTarget) {
  if (segment.locator.kind !== "time" || !segment.locator.transcription || !segment.material_id) return;
  window.dispatchEvent(new CustomEvent("statforge:reveal-transcript", {
    detail: { materialId: segment.material_id, locator: segment.locator },
  }));
}

export function WorkspaceAnnotationProvider({ projectId, open, onOpenChange, children, activeTaskId, researchQuestion = "", openData, onNavigateSegment, openRelations, openAgreement, openMatrix, openJournals }: {
  projectId: string; activeTaskId?: string; researchQuestion?: string; open: boolean; onOpenChange: (value: boolean) => void; children: React.ReactNode;
  openData?: (id:string)=>void;
  onNavigateSegment?: (segment: AnnotationTarget) => Promise<void>;
  openRelations?: ()=>void;
  openAgreement?: ()=>void;
  openMatrix?: (current?:boolean)=>void;
  openJournals?: ()=>void;
}) {
  const [caseKey, setCaseKey] = useState("");
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["coding", projectId], queryFn: ({ signal }) => codingApi.read(projectId, signal) });
  const state = useRef<CodingState | undefined>(query.data);
  if (query.data && (!state.current || query.data.revision >= state.current.revision)) state.current = query.data;
  const [source, setSource] = useState<AnnotationSource | null>(null);
  const sourceRef = useRef<AnnotationSource | null>(null);
  const revealRef = useRef<Reveal>(() => {});
  const pendingSegment = useRef<AnnotationTarget | null>(null);
  const pendingTranscript = useRef<AnnotationTarget | null>(null);
  const readyTranscripts = useRef(new Set<string>());
  useLayoutEffect(() => {
    const ready = (event: Event) => {
      const materialId = (event as CustomEvent<{ materialId: string }>).detail?.materialId;
      if (!materialId) return;
      readyTranscripts.current.add(materialId);
      const pending = pendingTranscript.current;
      if (pending?.material_id === materialId) {
        pendingTranscript.current = null;
        revealTranscript(pending);
      }
    };
    window.addEventListener("statforge:transcript-ready", ready);
    return () => window.removeEventListener("statforge:transcript-ready", ready);
  }, []);
  const queueTranscript = useCallback((segment: AnnotationTarget) => {
    if (segment.locator.kind !== "time" || !segment.locator.transcription || !segment.material_id) return;
    pendingTranscript.current = segment;
    if (readyTranscripts.current.has(segment.material_id)) {
      pendingTranscript.current = null;
      revealTranscript(segment);
    }
  }, []);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [focusedId, setFocusedId] = useState("");
  const [comparisonTarget, setComparisonTarget] = useState<AnnotationTarget|null>(null);
  const [busy, setBusy] = useState(false);
  const changing = useRef(false);
  const [error, setError] = useState("");
  const activate = useCallback((next: AnnotationSource, reveal: Reveal) => {
    if ((next.task_id || "project") !== (activeTaskId || "project")) return;
    revealRef.current = reveal;
    if (sourceKey(sourceRef.current) !== sourceKey(next)) {
      sourceRef.current = next;
      setSource(next); setDraft(null); setFocusedId(""); setError("");
    }
    const pending = pendingSegment.current;
    if (pending && sameVersion(pending, next) && (pending.task_id || "") === (next.task_id || "")) {
      pendingSegment.current = null;
      setFocusedId(pending.id || "");
      queueTranscript(pending);
      Promise.resolve(reveal(pending.locator)).catch(reason => setError(reason instanceof Error ? reason.message : "无法定位，请重新打开文件。"));
    }
  }, [activeTaskId, queueTranscript]);
  const deactivate = useCallback((reveal: Reveal) => {
    if (revealRef.current !== reveal) return;
    sourceRef.current = null;
    revealRef.current = () => {};
    setSource(null); setDraft(null); setFocusedId("");
  }, []);
  const begin = useCallback((next: AnnotationSource, locator: AnnotationLocation, reveal: Reveal) => {
    activate(next, reveal);
    setDraft({ source: next, locator, token: Date.now() }); setFocusedId(""); onOpenChange(true);
  }, [activate, onOpenChange]);
  const focus = useCallback((segment: AnnotationTarget) => {
    setComparisonTarget(segment.comparison ? segment : null);
    const current = sourceRef.current;
    if (current && sameVersion(segment, current) && (segment.task_id || "") === (current.task_id || "")) {
      setFocusedId(segment.id || "");
      queueTranscript(segment);
      Promise.resolve(revealRef.current(segment.locator)).catch(reason => setError(reason instanceof Error ? reason.message : "无法定位，请重新打开文件。"));
      return;
    }
    if (current && (segment.material_id || segment.dataset_id) === (current.material_id || current.dataset_id) && !sameVersion(segment, current)) {
      setError("原文件版本已变化，请打开对应旧版本查看这条标注。"); return;
    }
    if (!onNavigateSegment) { setError("请打开对应文件查看这条标注。"); return; }
    pendingSegment.current = segment;
    if (segment.locator.kind === "time" && segment.locator.transcription && segment.material_id) {
      readyTranscripts.current.delete(segment.material_id);
      pendingTranscript.current = segment;
    }
    Promise.resolve(onNavigateSegment(segment)).catch(reason => {
      pendingSegment.current = null;
      pendingTranscript.current = null;
      setError(reason instanceof Error ? reason.message : "无法打开标注对应的文件。");
    });
  }, [onNavigateSegment, queueTranscript]);
  const change = useCallback(async (body: CodingChange) => {
    if (changing.current || !state.current) return false;
    changing.current = true; setBusy(true); setError("");
    try {
      const saved = await codingApi.change(projectId, state.current.revision, body);
      state.current = saved;
      queryClient.setQueryData(["coding", projectId], saved);
      if (body.operation === "remove_segment" || body.operation === "undo") setFocusedId("");
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "标注未保存，请重试。");
      await queryClient.invalidateQueries({ queryKey: ["coding", projectId] });
      return false;
    } finally { changing.current = false; setBusy(false); }
  }, [projectId, queryClient]);
  const preview = useCallback((locator: AnnotationLocation) => { Promise.resolve(revealRef.current(locator)).catch(reason => setError(reason instanceof Error ? reason.message : "无法定位，请重新打开文件。")); }, []);
  const value = { comparisonTarget, caseKey, setCaseKey, openRelations, openAgreement, openMatrix, openJournals, openData, activeTaskId, researchQuestion, open, setOpen: onOpenChange, source, activate, deactivate, begin, draft, setDraft, focusedId, focus, preview, state: query.data, busy, error: error || (query.isError ? query.error.message : ""), change };
  return <AnnotationContext.Provider value={value}>{children}</AnnotationContext.Provider>;
}

/** Legacy viewers remain usable without an annotation provider. */
export function useWorkspaceAnnotations() { return useContext(AnnotationContext); }

export function useAnnotationSource(source: AnnotationSource, reveal: Reveal, enabled = true) {
  const context = useWorkspaceAnnotations();
  const sourceRef = useRef(source); sourceRef.current = source;
  const revealRef = useRef(reveal); revealRef.current = reveal;
  const revealCurrent = useCallback((locator: AnnotationLocation) => revealRef.current(locator), []);
  const activate = context?.activate;
  const deactivate = context?.deactivate;
  const key = sourceKey(source);
  useEffect(() => {
    if (!enabled) return;
    activate?.(sourceRef.current, revealCurrent);
    return () => deactivate?.(revealCurrent);
  }, [activate, deactivate, key, revealCurrent, enabled]);
  const segments = context?.state?.payload.segments.filter(segment => annotationMatches(segment, source)) ?? [];
  return {
    enabled: !!context && enabled,
    comparison: context?.comparisonTarget && sameVersion(context.comparisonTarget, source) ? context.comparisonTarget.comparison : undefined,
    themes: context?.state?.payload.themes ?? [],
    segments,
    focusedId: context?.focusedId ?? "",
    activate: () => context?.activate(sourceRef.current, revealCurrent),
    open: () => { context?.activate(sourceRef.current, revealCurrent); context?.setOpen(true); },
    annotate: (locator: AnnotationLocation) => context?.begin(sourceRef.current, locator, revealCurrent),
    focus: (segment: CodedSegment) => { context?.activate(sourceRef.current, revealCurrent); context?.focus(segment); context?.setOpen(true); },
  };
}
export function annotationColor(color: ThemeColor, rgb?: string | null) { return rgb || palette[color]; }

function themeDepth(theme: CodingTheme, themes: CodingTheme[]) {
  const seen = new Set([theme.id]); let parent = theme.parent_id, depth = 0;
  while (parent && depth < 8 && !seen.has(parent)) { seen.add(parent); depth++; parent = themes.find(item => item.id === parent)?.parent_id || null; }
  return depth;
}

function evidenceLocation(locator: AnnotationLocation) {
  if (locator.kind === "text") return locator.page ? `第 ${locator.page} 页` : "文字";
  if (locator.kind === "time") return `${locator.start_seconds.toFixed(2)}–${locator.end_seconds.toFixed(2)} 秒`;
  return annotationLocationLabel(locator);
}
function evidenceExcerpt(locator: AnnotationLocation) {
  const quote = locator.kind === "text" ? locator.quote : locator.kind === "time" ? locator.transcription?.quote : "";
  const chars = Array.from((quote || "").replace(/\s+/g, " ").trim());
  return chars.slice(0, 180).join("") + (chars.length > 180 ? "…" : "");
}
function evidenceSource(segment: CodedSegment) {
  const stem = (value: string) => value.replace(/\.[^/.]+$/, "").trim().toLocaleLowerCase();
  return segment.sample_title && stem(segment.sample_title) !== stem(segment.filename)
    ? `${segment.sample_title} · ${segment.filename}` : segment.filename;
}

export function WorkspaceAnnotationPanel() {
  const context = useWorkspaceAnnotations();
  const queryClient = useQueryClient();
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryId,setSummaryId]=useState("");
  const [summaryNotice, setSummaryNotice] = useState("");
  const [summaryError, setSummaryError] = useState("");
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [selectedThemes, setSelectedThemes] = useState<string[]>([]);
  const [memo, setMemo] = useState("");
  const [view, setView] = useState<"file" | "themes">("file");
  const caseKey = context?.caseKey || "";
  const setCaseKey = context?.setCaseKey || (()=>{});
  const [purpose, setPurpose] = useState("");
  const [suggested, setSuggested] = useState<{ proposal_id: string; theme_ids: string[]; suggestions: { theme_id: string; reason: string; evidence: string }[]; previous_context?:string } | null>(null);
  const [selectionSummary, setSelectionSummary] = useState<{ summary: string; evidence: string; previous_context?:string } | null>(null);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const selectionController = useRef<AbortController | null>(null);
  const [suggestionBusy, setSuggestionBusy] = useState(false);
  const [suggestionError, setSuggestionError] = useState("");
  const [adoptedSuggestion, setAdoptedSuggestion] = useState("");
  const [sharePrevious, setSharePrevious] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [newCoder, setNewCoder] = useState<string|null>(null);
  const suggestionController = useRef<AbortController | null>(null);
  const draft = context?.draft;
  useEffect(() => {
    setSelectedThemes(draft?.segment?.theme_ids ?? []); setMemo(draft?.segment?.memo ?? "");
    setPurpose((context?.researchQuestion || "").slice(0, 500)); setSelectionSummary(null); setSelectionError(""); setSelectionBusy(false); selectionController.current?.abort(); setSuggested(null); setAdoptedSuggestion(""); setSuggestionError(""); setSuggestionBusy(false); setSharePrevious(false);
    suggestionController.current?.abort();
    return () => { suggestionController.current?.abort(); selectionController.current?.abort(); };
  }, [draft?.token, draft?.segment?.id, context?.researchQuestion]);
  useEffect(() => { setFilter(""); setSearch(""); }, [context?.source?.material_id, context?.source?.dataset_id]);
  useEffect(() => { setSummaryNotice(""); setSummaryId(""); setSummaryError(""); }, [context?.state?.project_id]);
  const themes = context?.state?.payload.themes ?? [];
  const orderedThemes = useMemo(() => {
    const ordered: CodingTheme[] = [];
    const visit = (parent: string | null) => themes.filter(theme => theme.parent_id === parent).forEach(theme => { ordered.push(theme); visit(theme.id); });
    visit(null); return ordered;
  }, [themes]);
  const activeCase = context?.state?.case_scopes?.find(item => item.key === caseKey);
  const appliedScope = useQuery({
    queryKey: ["coding-scope", context?.state?.project_id, context?.state?.revision, caseKey],
    queryFn: ({signal}) => codingApi.scope(context!.state!.project_id, context!.state!.revision, activeCase, signal),
    enabled: !!context?.open && view === "themes" && !!context?.state && (!caseKey || !!activeCase),
  });
  useEffect(() => {setSummaryNotice("");setSummaryId("");setSummaryError("");},[context?.state?.revision]);
  if (!context || !context.open) return null;
  const { activeTaskId, source, state, busy, error, change, setDraft } = context;
  const validLocator = !draft || (draft.locator.kind === "time" || draft.locator.kind === "signal_time" ? Number.isFinite(draft.locator.start_seconds) && Number.isFinite(draft.locator.end_seconds) && draft.locator.start_seconds >= 0 && (draft.locator.kind === "time" && draft.locator.point ? draft.locator.end_seconds === draft.locator.start_seconds : draft.locator.end_seconds > draft.locator.start_seconds) : true);
  const status = state?.transcription_status || {};
  const cases = state?.case_scopes || [];
  const selectedCase = cases.find(item => item.key === caseKey);
  const scopeUnavailable = !!caseKey && !selectedCase;
  const matchedIds = new Set(appliedScope.data?.segment_ids || []);
  const scopedSegments = scopeUnavailable ? [] : state?.payload.segments.filter(segment => matchedIds.has(segment.id)) ?? [];
  const scopeLabel = appliedScope.data?.scope_label || (selectedCase ? `案例 · ${selectedCase.title}` : "全项目");
  const scopePending = appliedScope.isFetching || !!appliedScope.error;
  const caseNames = new Map<string, number>();
  cases.forEach(item => caseNames.set(item.title, (caseNames.get(item.title) || 0) + 1));
  const visibleVersion = (segment: CodedSegment) => showHistory || segment.id === context.focusedId || !status[segment.id] || status[segment.id] === "current";
  const segments = state?.payload.segments.filter(segment => visibleVersion(segment) && source && annotationMatches(segment, source) && (!filter || segment.theme_ids.includes(filter)) && (!search || `${annotationLocationLabel(segment.locator)} ${segment.memo}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))) ?? [];
  async function save() {
    if (!draft || !validLocator || (!selectedThemes.length && !memo.trim())) return;
    const { filename: _filename, related_material_ids: _related, ...identity } = draft.source;
    if (await change({ operation: draft.segment ? "edit_segment" : "add_segment", segment_id: draft.segment?.id, ...identity, locator: draft.locator, theme_ids: selectedThemes, memo: memo.trim(), proposal_id: adoptedSuggestion || undefined })) setDraft(null);
  }
  async function suggestThemes() {
    if (!draft || !state || !draft.source.material_id || !draft.source.material_sha256 || !draft.source.material_version) return;
    suggestionController.current?.abort();
    const controller = new AbortController(); suggestionController.current = controller;
    setSuggestionBusy(true); setSuggestionError(""); setSuggested(null); setAdoptedSuggestion("");
    try {
      const result = await codingApi.suggestThemes(state.project_id, { revision: state.revision, material_id: draft.source.material_id,
        material_sha256: draft.source.material_sha256, material_version: draft.source.material_version,
        locator: draft.locator, purpose: purpose.trim() || "依据当前选段和已有主题", annotation_id: draft.segment?.id, share_selection: true, share_previous_context: sharePrevious, task_id: activeTaskId || null }, controller.signal);
      if (!controller.signal.aborted) setSuggested(result);
    } catch (reason) {
      if (!controller.signal.aborted) setSuggestionError(reason instanceof Error ? reason.message : "主题建议暂不可用。");
    } finally { if (!controller.signal.aborted) setSuggestionBusy(false); }
  }
  async function summarizeSelection() {
    if (!draft || draft.locator.kind !== "text" || !state || !draft.source.material_id || !draft.source.material_sha256 || !draft.source.material_version) return;
    selectionController.current?.abort();
    const controller = new AbortController(); selectionController.current = controller;
    setSelectionBusy(true); setSelectionError(""); setSelectionSummary(null);
    try {
      const result = await codingApi.selectionSummary(state.project_id, { revision: state.revision, material_id: draft.source.material_id,
        material_sha256: draft.source.material_sha256, material_version: draft.source.material_version,
        locator: draft.locator, purpose: purpose.trim() || "依据当前选段和已有主题", annotation_id: draft.segment?.id, share_selection: true, share_previous_context: sharePrevious, task_id: activeTaskId || null }, controller.signal);
      if (!controller.signal.aborted) setSelectionSummary(result);
    } catch (reason) {
      if (!controller.signal.aborted) setSelectionError(reason instanceof Error ? reason.message : "选段摘要暂不可用。");
    } finally { if (!controller.signal.aborted) setSelectionBusy(false); }
  }
  function revealEvidence(evidence: string) {
    if (!draft) return;
    const locator = draft.locator;
    if (locator.kind === "text") {
      const offset = locator.quote.indexOf(evidence);
      if (offset >= 0 && evidence) {
        const start = locator.start + Array.from(locator.quote.slice(0, offset)).length;
        context.preview({ ...locator, start, end: start + Array.from(evidence).length, quote: evidence });
        return;
      }
    }
    if (locator.kind === "time" && locator.transcription && draft.source.material_id) {
      window.dispatchEvent(new CustomEvent("statforge:reveal-transcript", { detail: { materialId: draft.source.material_id, locator } }));
    }
    context.preview(locator);
  }
  async function createSummary() {
    if (!state || summaryBusy) return;
    setSummaryBusy(true); setSummaryNotice(""); setSummaryError("");
    try {
      const saved = await codingApi.sampleSummary(state.project_id, state.revision, activeTaskId, selectedCase);
      await queryClient.invalidateQueries({ queryKey: ["datasets"] });
      setSummaryId(saved.id);
      setSummaryNotice(`${saved.scope_label}：已生成 ${saved.sample_count} 个案例的主题汇总。${saved.unassociated_segments ? ` ${saved.unassociated_segments} 条标注未关联样本。` : ""}${saved.excluded_transcription_segments ? ` ${saved.excluded_transcription_segments} 条历史或不可核验转录标注未计入。` : ""}`);
    } catch (reason) {
      setSummaryError(reason instanceof Error ? reason.message : "无法生成主题汇总，请重试。");
    } finally { setSummaryBusy(false); }
  }
  return <aside className={styles.panel} aria-label="标注">
    <header className={styles.header}><h2>标注</h2><button type="button" aria-label="关闭标注" onClick={() => context.setOpen(false)}><XIcon size={18} /></button></header>
    <div className={styles.body}>
      <p className={styles.filename}>{source?.filename || "选择文件后开始标注"}</p>
      {state && <div className={styles.coderChoice}><label>当前编码者<SearchSelect aria-label="当前编码者" disabled={busy} value={state.current_coder?.id || ""} onChange={e=>void change({operation:"select_coder",coder_id:e.target.value})}>{state.local_coders?.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</SearchSelect></label>
        {newCoder===null?<button onClick={()=>setNewCoder("")}>添加编码者</button>:<form onSubmit={e=>{e.preventDefault();void change({operation:"add_coder",name:newCoder}).then(ok=>{if(ok)setNewCoder(null);});}}><input aria-label="编码者名称" placeholder="姓名" value={newCoder} onChange={e=>setNewCoder(e.target.value)} maxLength={80}/><button disabled={busy||!newCoder.trim()}>保存并使用</button><button type="button" onClick={()=>setNewCoder(null)}>取消</button></form>}
      </div>}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {!state && !error && <p role="status">正在读取标注…</p>}
      {draft && <section className={styles.editor} aria-label={draft.segment ? "编辑标注" : "新建标注"}>
        <h3>{draft.segment ? "编辑标注" : "新建标注"}</h3><p className={styles.hint}>编码者：{draft.segment ? (draft.segment.coder_name || "未记录") : state?.current_coder?.name}</p>
        <blockquote>{annotationLocationLabel(draft.locator)}</blockquote>
        {draft.locator.kind === "signal_time" && <p className={styles.hint}>{draft.locator.channel_ids.join(" / ")}</p>}
        {draft.locator.kind === "region" && <details><summary>调整区域</summary><div className={styles.row}>{(["x", "y", "width", "height"] as const).map(key => {
          const locator = draft.locator;
          if (locator.kind !== "region") return null;
          return <label className={styles.field} key={key}>{({ x: "横向", y: "纵向", width: "宽度", height: "高度" })[key]} %<input type="number" min={key === "width" || key === "height" ? .1 : 0} max="100" step="0.1" aria-label={`标注区域${key}`} value={Number((locator[key] * 100).toFixed(2))} onChange={event => {
            const next = { ...locator, [key]: Math.min(1, Math.max(0, Number(event.target.value) / 100)) };
            next.x = Math.min(.999, next.x); next.y = Math.min(.999, next.y);
            next.width = Math.max(.001, Math.min(next.width, 1 - next.x)); next.height = Math.max(.001, Math.min(next.height, 1 - next.y));
            setSuggested(null); setSelectionSummary(null); selectionController.current?.abort(); suggestionController.current?.abort(); setSelectionBusy(false); setSuggestionBusy(false); setAdoptedSuggestion(""); setDraft({ ...draft, locator: next }); context.preview(next);
          }} /></label>;
        })}</div></details>}
        {(draft.locator.kind === "time" || draft.locator.kind === "signal_time") && !(draft.locator.kind === "time" && draft.locator.point) && <details><summary>调整时间</summary><div className={styles.row}>{(["start_seconds", "end_seconds"] as const).map(key => {
          const locator = draft.locator;
          if (locator.kind !== "time" && locator.kind !== "signal_time") return null;
          const utc = locator.start_seconds > 1_000_000_000;
          return <label className={styles.field} key={key}>{key === "start_seconds" ? "开始" : "结束"}{utc ? "（UTC）" : "（秒）"}<input type={utc ? "datetime-local" : "number"} step="0.001" min={utc ? undefined : 0} aria-label={`标注${key === "start_seconds" ? "开始" : "结束"}`} value={utc ? new Date(locator[key] * 1000).toISOString().slice(0, 23) : Number(locator[key].toFixed(3))} onChange={event => { const value = utc ? Date.parse(`${event.target.value}Z`) / 1000 : Number(event.target.value); if (!Number.isFinite(value)) return; const next = { ...locator, [key]: value }; setSuggested(null); setSelectionSummary(null); selectionController.current?.abort(); suggestionController.current?.abort(); setSelectionBusy(false); setSuggestionBusy(false); setAdoptedSuggestion(""); setDraft({ ...draft, locator: next }); }} onBlur={() => { if (validLocator) context.preview(draft.locator); }} /></label>;
        })}</div>{!validLocator && <p role="status">结束时间应晚于开始时间。</p>}</details>}
        <div className={styles.tags} aria-label="选择标签">{orderedThemes.filter(theme => theme.is_codable !== false).map(theme => <button type="button" key={theme.id} aria-pressed={selectedThemes.includes(theme.id)} style={{ "--tag-color": annotationColor(theme.color, theme.color_rgb) } as React.CSSProperties} onClick={() => setSelectedThemes(ids => ids.includes(theme.id) ? ids.filter(id => id !== theme.id) : [...ids, theme.id])}>{themePath(theme, themes)}</button>)}</div>
        {!themes.length && <p className={styles.hint}>可直接写备注，或在下方新建标签。</p>}
        {draft.source.material_id && (themes.length > 0 || draft.locator.kind === "text") && <details>
          <summary>选段摘要与主题建议</summary>
          <label className={styles.field}>研究目的（可选）<input value={purpose} maxLength={500} onChange={event => { setPurpose(event.target.value); suggestionController.current?.abort(); selectionController.current?.abort(); setSuggestionBusy(false); setSelectionBusy(false); setSuggested(null); setSelectionSummary(null); setAdoptedSuggestion(""); }} /></label>
          <p className={styles.hint}>发送当前选段{draft.segment ? "及这条标注" : ""}、研究目的和相关主题给当前模型。</p>
          {(draft.locator.kind === "text" || (draft.locator.kind === "time" && !!draft.locator.transcription)) && <label><input type="checkbox" checked={sharePrevious} onChange={event=>{setSharePrevious(event.target.checked);setSuggested(null);setSelectionSummary(null);setAdoptedSuggestion("");suggestionController.current?.abort();selectionController.current?.abort();setSuggestionBusy(false);setSelectionBusy(false);}}/> 同时发送同一版本中紧邻的前文（最多 400 字）</label>}
          {draft.locator.kind === "text" && <button type="button" disabled={selectionBusy} onClick={() => void summarizeSelection()}>{selectionBusy ? "正在生成…" : "生成选段摘要"}</button>}
          {selectionError && <p role="alert" className={styles.error}>{selectionError}</p>}
          {selectionSummary && <div>{selectionSummary.previous_context && <p>发送的前文：“{selectionSummary.previous_context}”</p>}<p>{selectionSummary.summary}</p><button type="button" className={styles.segmentJump} onClick={() => revealEvidence(selectionSummary.evidence)}>“{selectionSummary.evidence}”</button></div>}
          {themes.length > 0 && <button type="button" disabled={suggestionBusy} onClick={() => void suggestThemes()}>{suggestionBusy ? "正在建议…" : "用选段建议主题"}</button>}
          {suggestionError && <p role="alert" className={styles.error}>{suggestionError}</p>}
          {suggested?.previous_context && <p>发送的前文：“{suggested.previous_context}”</p>}
          {suggested && !suggested.theme_ids.length && <p>没有合适的主题建议。</p>}
          {suggested?.suggestions.map(item => { const theme = themes.find(value => value.id === item.theme_id); return theme && <div key={item.theme_id}><p>{theme.name}：{item.reason}</p><button type="button" className={styles.segmentJump} onClick={() => revealEvidence(item.evidence)}>“{item.evidence}”</button><button type="button" onClick={() => { setSelectedThemes(ids => ids.includes(item.theme_id) ? ids : [...ids, item.theme_id]); setAdoptedSuggestion(suggested.proposal_id); }}>采用 {theme.name}</button></div>; })}
        </details>}
        <label className={styles.field}>备注<textarea aria-label="标注备注" rows={3} value={memo} onChange={event => setMemo(event.target.value)} placeholder="添加备注（可选）" /></label>
        <div className={styles.actions}><button type="button" className={styles.primary} disabled={busy || !validLocator || (!selectedThemes.length && !memo.trim())} onClick={() => void save()}>{busy ? "保存中…" : "保存标注"}</button><button type="button" disabled={busy} onClick={() => setDraft(null)}>取消</button></div>
      </section>}
      <div className={styles.scopeTabs} aria-label="标注范围"><button type="button" aria-pressed={view === "file"} onClick={() => setView("file")}>当前文件</button><button type="button" aria-pressed={view === "themes"} onClick={() => setView("themes")}>按主题查看</button></div>
      <label className={styles.historyToggle}><input type="checkbox" checked={showHistory} onChange={event => setShowHistory(event.target.checked)} /><span>显示历史版本标注</span></label>
      {view === "themes" && <div className={styles.themeSummary}>
        <label className={styles.field}>案例范围<SearchSelect aria-label="案例范围" value={caseKey} onChange={event => {
          setCaseKey(event.target.value); setSummaryNotice(""); setSummaryId(""); setSummaryError("");
        }}><option value="">全项目</option>{cases.map((item, index) => {
          const duplicate = (caseNames.get(item.title) || 0) > 1;
          const label = duplicate ? `${item.title} · ${item.group_title}` : item.title;
          const collision = duplicate && cases.some(other => other.key !== item.key && other.title === item.title && other.group_title === item.group_title);
          return <option key={item.key} value={item.key}>{label}{collision ? ` · 案例 ${index + 1}` : ""}</option>;
        })}</SearchSelect></label>
        {state && <CodingAttributeFilter state={state} busy={busy} change={change}/>}
        {scopeUnavailable && <p role="alert" className={styles.error}>此案例已不可用，请重新选择范围。</p>}
        {appliedScope.isFetching && <p role="status">正在筛选…</p>}
        {appliedScope.error && <p role="alert" className={styles.error}>{appliedScope.error.message}</p>}
        {!scopePending && !scopeUnavailable && appliedScope.data && <p role="status">{appliedScope.data.case_count} 个案例 · {appliedScope.data.file_count} 个文件 · {appliedScope.data.annotation_count} 条当前标注</p>}
        {!scopePending && !scopeUnavailable && appliedScope.data?.annotation_count===0 && <p className={styles.empty}>当前范围没有匹配标注。</p>}
        {orderedThemes.map(theme => {
          const matches = scopedSegments.filter(segment => (showHistory || !status[segment.id] || status[segment.id]==="current") && segment.theme_ids.includes(theme.id));
          return <details key={theme.id} style={{ marginLeft: themeDepth(theme, themes) * 12 }}>
            <summary>{theme.name} · {theme.is_codable === false ? "分组" : matches.length}</summary>
            {theme.memo && <p className={styles.hint}>{theme.memo}</p>}
            {matches.map(segment => <div className={styles.summaryMatch} key={segment.id}>
              <button type="button" className={`${styles.segmentJump} ${styles.evidenceJump}`} onClick={() => context.focus(segment)}>
                <span className={styles.evidenceSource} title={evidenceSource(segment)}>{evidenceSource(segment)}</span>
                <span className={styles.evidencePosition}>{evidenceLocation(segment.locator)}</span>
                {evidenceExcerpt(segment.locator) && <span className={styles.evidenceExcerpt}>{evidenceExcerpt(segment.locator)}</span>}
              </button>
              {segment.memo && <p className={styles.evidenceExcerpt}>{segment.memo}</p>}
              {transcriptVersionLabel(segment, status[segment.id], state?.transcription_info?.[segment.id]) && <small>{transcriptVersionLabel(segment, status[segment.id], state?.transcription_info?.[segment.id])} · </small>}<small>{({ manual: "人工", ai_accepted: "AI 建议已采用", mixed: "人工与 AI", legacy_ai: "历史标注与 AI 建议", undefined: "历史标注" } as Record<string, string>)[segment.origin || "undefined"]}</small>
            </div>)}
          </details>;
        })}
        {!themes.length && <p className={styles.hint}>先新建主题，再给内容添加主题。</p>}
        <p className={styles.hint}>汇总范围：{scopeLabel}。按标注计数，仅计当前采用版本；样本表只列有有效标注的已关联案例。</p>
        <div className={styles.actions}><button type="button" disabled={summaryBusy || scopePending || scopeUnavailable || !appliedScope.data?.annotation_count} onClick={() => void createSummary()}>{summaryBusy ? "正在生成…" : "生成样本汇总"}</button><DownloadButton identity={`${state?.revision}:coding-summary:${caseKey}`} disabled={scopePending || scopeUnavailable || !state} download={signal => codingApi.export(state!.project_id, "summary_csv", signal, selectedCase)}>导出主题汇总</DownloadButton></div>
        {summaryNotice && <p role="status">{summaryNotice} {summaryId && context.openData && <button onClick={()=>context.openData?.(summaryId)}>打开此次汇总</button>}</p>}{summaryError && <p role="alert" className={styles.error}>{summaryError}</p>}
      </div>}
      {view === "file" && <>
      <div className={styles.filters}><input aria-label="搜索当前文件标注" placeholder="搜索标注" value={search} onChange={event => setSearch(event.target.value)} /><SearchSelect aria-label="按标签筛选" value={filter} onChange={event => setFilter(event.target.value)}><option value="">全部标签</option>{orderedThemes.filter(theme => theme.is_codable !== false).map(theme => <option key={theme.id} value={theme.id}>{themePath(theme, themes)}</option>)}</SearchSelect></div>
      <div className={styles.actions}><span>{segments.length} 条标注</span><button type="button" disabled={busy || !state?.payload.undo_stack.length} onClick={() => void change({ operation: "undo" }).then(ok => { if (ok) setDraft(null); })}><Undo2Icon size={14} />撤销</button></div>
      <ol className={styles.segments}>{segments.map(segment => <li key={segment.id} data-focused={context.focusedId === segment.id}>
        <button className={styles.segmentJump} type="button" onClick={() => context.focus(segment)}>{annotationLocationLabel(segment.locator)}</button>
        {transcriptVersionLabel(segment, status[segment.id], state?.transcription_info?.[segment.id]) && <small>{transcriptVersionLabel(segment, status[segment.id], state?.transcription_info?.[segment.id])}</small>}
        <small>{segment.coder_name || "编码者未记录"}</small><div className={styles.tags}>{segment.theme_ids.map(id => { const theme = themes.find(item => item.id === id); return theme && <span key={id} style={{ "--tag-color": annotationColor(theme.color, theme.color_rgb) } as React.CSSProperties}>{theme.name}</span>; })}</div>
        {segment.memo && <p>{segment.memo}</p>}
        {source && !sameVersion(segment, source) ? <p className={styles.hint}>原文件已变化，无法定位这条标注</p> : <div className={styles.actions}><button type="button" disabled={busy} onClick={() => { context.focus(segment); setDraft({ source: source!, locator: segment.locator, segment, token: Date.now() }); }}><PencilIcon size={13} />编辑</button><button type="button" disabled={busy} onClick={() => void change({ operation: "remove_segment", segment_id: segment.id })}><Trash2Icon size={13} />删除</button></div>}
      </li>)}</ol>
      {source && !segments.length && !draft && <p className={styles.empty}><MessageSquarePlusIcon size={20} />在内容中选中文字、区域或时间段，点击“标注”。</p>}
      </>}
      {(context.openRelations || context.openAgreement || context.openMatrix || context.openJournals) &&
        <details className={styles.codingTools}><summary>编码分析</summary><div className={styles.actions}>
      {context.openRelations && <button type="button" onClick={context.openRelations}>共现与关系</button>}
      {context.openAgreement && <button type="button" onClick={context.openAgreement}>编码者一致性</button>}
      {context.openMatrix && <button type="button" onClick={()=>context.openMatrix?.()}>证据矩阵</button>}
      {context.openJournals && <button type="button" onClick={context.openJournals}>研究日志</button>}
        </div></details>}
      <WorkspaceCodebook key={state?.project_id} state={state} busy={busy} change={change}/>
    </div>
    <footer className={styles.footer}><DownloadButton identity={`${state?.revision}:coding-csv`} disabled={!state?.payload.segments.length} download={signal => codingApi.export(state!.project_id, "csv", signal)}>全项目标注 CSV</DownloadButton><DownloadButton identity={`${state?.revision}:coding-json`} disabled={!state?.payload.segments.length} download={signal => codingApi.export(state!.project_id, "json", signal)}>全项目标注 JSON</DownloadButton></footer>
  </aside>;
}
