"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

type Snapshot = { id: string; scroll: number; open: number[] };
export function usePreviewHistory(id: string | undefined, scrollSelector: string) {
  const [history, setHistory] = useState<Snapshot[]>([]);
  const pending = useRef<Snapshot | null>(null);
  const capture = () => {
    const root = document.querySelector<HTMLElement>(scrollSelector);
    return { id: id || "", scroll: root?.scrollTop || 0, open: Array.from(root?.querySelectorAll("details") || []).flatMap((detail, index) => detail.open ? [index] : []) };
  };
  const push = () => { if (id) setHistory(old => [...old, capture()]); };
  const reset = () => { pending.current = null; setHistory([]); };
  const back = (choose: (id: string) => void) => {
    const snapshot = history.at(-1);
    if (!snapshot) return;
    pending.current = snapshot;
    setHistory(old => old.slice(0, -1));
    choose(snapshot.id);
  };
  useEffect(() => {
    const snapshot = pending.current;
    if (!snapshot || snapshot.id !== id) return;
    const root = document.querySelector<HTMLElement>(scrollSelector);
    if (!root) return;
    // Previews may populate asynchronously; restore again when their details arrive.
    const restore = () => {
      const details = root.querySelectorAll("details");
      snapshot.open.forEach(index => { if (details[index]) details[index].open = true; });
      root.scrollTop = snapshot.scroll;
    };
    restore();
    const observer = new MutationObserver(restore);
    observer.observe(root, { childList: true, subtree: true });
    const timer = window.setTimeout(() => { observer.disconnect(); pending.current = null; }, 2000);
    return () => { observer.disconnect(); window.clearTimeout(timer); };
  }, [id, scrollSelector]);
  return { canBack: history.length > 0, previousId: history.at(-1)?.id, push, reset, back };
}

export function backLabel(item?: {media_case?:unknown;media_result?:{kind:string}|null;file_format:string;signal_session?:unknown}) {
  if (item?.media_case) return "返回材料组";
  if (item?.media_result?.kind === "arrays_npz") return "返回完整特征";
  if (item?.media_result?.kind === "summary_csv") return "返回特征摘要";
  if (item?.file_format === "mp4") return "返回视频";
  if (item?.file_format === "wav") return "返回录音";
  if (item?.signal_session) return "返回信号";
  return "返回材料";
}

export function ResizeHandle({ label, value, onChange, direction, min, max }: { label: string; value: number; onChange: (value: number) => void; direction: 1 | -1; min: number; max: number }) {
  const start = useRef({ x: 0, value: 0 });
  const clamp = (next: number) => Math.max(min, Math.min(max, next));
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => { start.current = { x: event.clientX, value }; event.currentTarget.setPointerCapture(event.pointerId); };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) onChange(clamp(start.current.value + direction * (event.clientX - start.current.x))); };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault(); onChange(clamp(value + direction * (event.key === "ArrowRight" ? 16 : -16)));
  };
  return <div className="rw-resize" role="separator" aria-label={label} aria-orientation="vertical" aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onKeyDown={onKeyDown} />;
}
