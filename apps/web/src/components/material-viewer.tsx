"use client";

import { useEffect, useRef, useState } from "react";
import { MessageSquarePlusIcon, SquareDashedMousePointerIcon } from "lucide-react";
import { useVideoBytes } from "./use-video-bytes";
import { VideoSelection } from "./video-selection";
import { materialApi } from "@/lib/material-api";
import type { Location, Material } from "@/lib/material-types";
import type { AnnotationLocation } from "@/lib/text-coding";
import { DownloadButton } from "./download-button";
import { annotationColor, useAnnotationSource } from "./workspace-annotations";
import styles from "./workspace-annotations.module.css";

type Region = Extract<Location, { kind: "region" }>;
type Timed = Extract<Location, { kind: "time" }>;
type Drag = { x: number; y: number; mode: "new" | "move" | "resize"; region: Region | null };
const clamp = (value: number, min = 0, max = 1) => Math.min(max, Math.max(min, value));

export function MaterialViewer({ material, page, location, taskId, sampleId, sampleCollectionId, enableAnnotations = true, sharedRange, onTimeRangeChange, initialCursor, onCursorChange }: {
  material: Material; page: number; location: Location | null; taskId?: string; sampleId?: string; sampleCollectionId?: string; enableAnnotations?: boolean; sharedRange?: { start: number | null; end: number | null }; onTimeRangeChange?: (range: Timed) => void; initialCursor?: number; onCursorChange?: (seconds: number) => void;
}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [duration, setDuration] = useState<number | null>(null);
  const [current, setCurrent] = useState(0);
  const [playingSelection, setPlayingSelection] = useState(false);
  const [region, setRegion] = useState<Region | null>(location?.kind === "region" ? location : null);
  const [localRange, setRange] = useState<Timed>(location?.kind === "time" ? location : { kind: "time", start_seconds: 0, end_seconds: 0, note: "" });
  const range: Timed = sharedRange ? { kind: "time", start_seconds: sharedRange.start ?? 0,
    end_seconds: sharedRange.end ?? duration ?? 0, note: "" } : localRange;
  function editRange(next: Timed) {
    if (!sharedRange) setRange(next);
    else if (duration != null && next.start_seconds >= 0 && next.end_seconds > next.start_seconds && next.end_seconds <= duration + 1e-6)
      onTimeRangeChange?.(next);
  }
  const [drawing, setDrawing] = useState(false);
  const media = useRef<HTMLMediaElement | null>(null);
  const imageArea = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const boundedPlayback = useRef(false);
  const pendingReveal = useRef<Region | Timed | null>(null);
  const source = { filename: material.filename, material_id: material.id, material_sha256: material.sha256, material_version: material.version, related_material_ids: material.metadata.annotation_material_ids, task_id: taskId, sample_id: sampleId, sample_collection_id: sampleCollectionId };
  const annotations = useAnnotationSource(source, (locator: AnnotationLocation) => {
    if (locator.kind === "region") { setRegion(locator); if (imageArea.current) imageArea.current.scrollIntoView({ block: "center", behavior: "smooth" }); else pendingReveal.current = locator; }
    if (locator.kind === "time") {
      if (!locator.point) editRange(locator);
      boundedPlayback.current = !locator.point;
      if (media.current && Number.isFinite(media.current.duration)) {
        media.current.currentTime = locator.start_seconds; setCurrent(locator.start_seconds);
        if (locator.point) media.current.pause();
        else void media.current.play().catch(() => { boundedPlayback.current = false; setError("请点击播放选段继续。"); });
      } else pendingReveal.current = locator;
    }
  }, enableAnnotations);
  const notifyRange = useRef(onTimeRangeChange); notifyRange.current = onTimeRangeChange;
  useEffect(() => { if (!sharedRange && localRange.end_seconds > localRange.start_seconds) notifyRange.current?.(localRange); }, [localRange.start_seconds, localRange.end_seconds, !!sharedRange]);
  const image = material.mime_type.startsWith("image/");
  const audio = material.mime_type.startsWith("audio/");
  const video = material.mime_type.startsWith("video/");
  const timed = audio || video;
  const videoBytes = useVideoBytes(material);
  const validRange = duration !== null && range.start_seconds >= 0 && range.end_seconds > range.start_seconds && range.end_seconds <= duration + 1e-6;
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = "";
    setUrl(""); setError(""); setDuration(null); setCurrent(0); setDrawing(false); boundedPlayback.current = false;
    if (!pendingReveal.current) { setRegion(null); setRange({ kind: "time", start_seconds: 0, end_seconds: 0, note: "" }); }
    if (video) {
      if (videoBytes.data) { objectUrl = URL.createObjectURL(videoBytes.data); setUrl(objectUrl); }
      else if (videoBytes.error) setError(videoBytes.error.message);
      return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }
    const load = audio ? materialApi.playback(material.id,controller.signal).then(result=>{if(!controller.signal.aborted){setUrl(result.url);setDuration(result.duration);}}) : materialApi.blob(material.id, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      if (blob.type.split(";")[0] !== material.mime_type.split(";")[0]) throw new Error("文件类型与记录不符，请刷新工作区。");
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    });
    void load.catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "文件无法读取，请刷新后重试。"); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [material.id, material.sha256, material.mime_type, videoBytes.data, videoBytes.error]);
  useEffect(() => {
    if (url && pendingReveal.current?.kind === "region" && imageArea.current) {
      setRegion(pendingReveal.current);
      pendingReveal.current = null;
      imageArea.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [url]);
  useEffect(() => {
    if (location?.kind === "region") setRegion(location);
    if (location?.kind === "time") { setRange(location); if (media.current && duration && location.start_seconds < duration) media.current.currentTime = location.start_seconds; }
  }, [location, duration]);
  function metadata() {
    const reported = media.current?.duration;
    const verified = material.metadata.duration_seconds;
    const actual = reported && verified != null ? (Math.abs(reported-verified)<=0.001 ? verified : Math.min(reported,verified)) : reported;
    if (actual && Number.isFinite(actual) && actual > 0) {
      setDuration(actual);
      const pending = pendingReveal.current;
      if (pending?.kind === "time" && pending.end_seconds <= actual + 1e-6) {
        pendingReveal.current = null; if (!pending.point) setRange(pending); boundedPlayback.current = !pending.point;
        media.current!.currentTime = pending.start_seconds;
        if (pending.point) media.current!.pause();
        else void media.current!.play().catch(() => { boundedPlayback.current = false; setError("请点击播放选段继续。"); });
      } else {
        if (pending?.kind === "time") { pendingReveal.current = null; setError("标注时间超出当前媒体范围，请检查原文件。"); }
        const at = location?.kind === "time" ? location.start_seconds : initialCursor ?? sharedRange?.start ?? 0;
        if (media.current && Number.isFinite(at)) {
          media.current.currentTime = clamp(at, 0, actual);
          setCurrent(media.current.currentTime);
        }
        setRange(old => old.end_seconds > old.start_seconds ? old : { ...old, end_seconds: actual });
      }
    } else setError("无法读取媒体时长，请重新打开文件。");
  }
  function timeUpdate() {
    const node = media.current;
    if (!node) return;
    if (boundedPlayback.current && node.currentTime >= range.end_seconds) {
      boundedPlayback.current = false;
      node.pause(); node.currentTime = range.end_seconds;
    }
    setCurrent(node.currentTime);
    onCursorChange?.(node.currentTime);
  }
  function playRange() {
    const node = media.current;
    if (!node || !validRange) return;
    if (boundedPlayback.current && !node.paused) { node.pause(); return; }
    boundedPlayback.current = true;
    node.currentTime = range.start_seconds;
    void node.play().catch(() => { boundedPlayback.current = false; setPlayingSelection(false); setError("播放失败，请使用播放器重试。"); });
  }
  function paused() {
    boundedPlayback.current = false;
    setPlayingSelection(false);
  }
  // timeupdate can be hundreds of milliseconds apart, particularly on short clips.
  useEffect(() => {
    const node = media.current;
    if (!playingSelection || !node) return;
    let frame = 0;
    function checkEnd() {
      if (!node || node.paused || !boundedPlayback.current) return;
      if (node.currentTime >= range.end_seconds) {
        node.pause();
        node.currentTime = range.end_seconds;
      } else frame = requestAnimationFrame(checkEnd);
    }
    frame = requestAnimationFrame(checkEnd);
    return () => cancelAnimationFrame(frame);
  }, [playingSelection, range.end_seconds]);
  function point(event: React.PointerEvent) {
    const bounds = imageArea.current!.getBoundingClientRect();
    return { x: clamp((event.clientX - bounds.left) / bounds.width), y: clamp((event.clientY - bounds.top) / bounds.height) };
  }
  function startDrag(event: React.PointerEvent, mode: Drag["mode"]) {
    if (!drawing || !imageArea.current) return;
    event.preventDefault(); event.stopPropagation();
    annotations.activate();
    const at = point(event); drag.current = { ...at, mode, region };
    imageArea.current.setPointerCapture(event.pointerId);
    if (mode === "new") setRegion({ kind: "region", ...at, width: 0.001, height: 0.001, note: "" });
  }
  function moveDrag(event: React.PointerEvent) {
    const moving = drag.current;
    if (!moving) return;
    const at = point(event);
    if (moving.mode === "new") {
      const x = Math.min(moving.x, at.x), y = Math.min(moving.y, at.y);
      setRegion({ kind: "region", x, y, width: Math.max(0.001, Math.abs(at.x - moving.x)), height: Math.max(0.001, Math.abs(at.y - moving.y)), note: "" });
    } else if (moving.region && moving.mode === "move") {
      setRegion({ ...moving.region, x: clamp(moving.region.x + at.x - moving.x, 0, 1 - moving.region.width), y: clamp(moving.region.y + at.y - moving.y, 0, 1 - moving.region.height) });
    } else if (moving.region) {
      setRegion({ ...moving.region, width: clamp(at.x - moving.region.x, 0.001, 1 - moving.region.x), height: clamp(at.y - moving.region.y, 0.001, 1 - moving.region.y) });
    }
  }
  const regions = annotations.segments.filter(segment => segment.material_id === material.id && segment.material_version === material.version && segment.locator.kind === "region" && segment.material_sha256 === material.sha256);
  return <section className={styles.mediaView} onPointerDownCapture={enableAnnotations ? annotations.activate : undefined}>
    <div className={`rw-content-tools ${styles.previewTools}`}>
      <span>{material.filename}</span>
      <div className="rw-inline-actions">
        {annotations.enabled && <button type="button" onClick={annotations.open}>查看标注 {annotations.segments.length || ""}</button>}
        <DownloadButton identity={material.id} download={signal => materialApi.download(material.id, material.filename, signal)}>导出原文件</DownloadButton>
      </div>
    </div>
    {!url && !error && <p role="status">正在读取文件…</p>}
    {url && image && <>
      {!!annotations.comparison?.length && <p className={styles.comparisonLegend}>{annotations.comparison.map(side=><span key={side.side} style={{color:side.side==="a"?"#236c96":"#ac4f24"}}>{side.name} · {side.side==="a"?"实线":"虚线"}</span>)}<span>高亮：当前分歧</span></p>}
      {annotations.enabled && <div className={`rw-inline-actions ${styles.mediaActions}`}><button type="button" aria-pressed={drawing} onClick={() => setDrawing(!drawing)}><SquareDashedMousePointerIcon size={15} />{drawing ? "结束框选" : "框选区域"}</button><button type="button" disabled={!region} onClick={() => { if (!region) return; const x = Math.min(region.x, .999), y = Math.min(region.y, .999); annotations.annotate({ ...region, x, y, width: Math.min(region.width, 1 - x), height: Math.min(region.height, 1 - y) }); }}><MessageSquarePlusIcon size={15} />标注此区域</button></div>}
      <div ref={imageArea} className={styles.imageArea} data-drawing={drawing} onPointerDown={event => startDrag(event, "new")} onPointerMove={moveDrag} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        {/* Local bytes must stay in the browser, never pass through a remote image optimizer. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={url} alt={material.filename} draggable={false} />
        {annotations.comparison?.flatMap(side=>side.regions.map((loc,index)=><div key={`${side.side}:${index}`} className={styles.comparisonRegion} style={{left:`${loc.x*100}%`,top:`${loc.y*100}%`,width:`${loc.width*100}%`,height:`${loc.height*100}%`,borderColor:side.side==="a"?"#236c96":"#ac4f24",borderStyle:side.side==="a"?"solid":"dashed"}}/>))}
        {annotations.enabled && !annotations.comparison && regions.map(segment => {
          const locator = segment.locator;
          if (locator.kind !== "region") return null;
          const theme = annotations.themes.find(theme => segment.theme_ids.includes(theme.id));
          return <button key={segment.id} type="button" className={styles.savedRegion} aria-label={`查看标注：${segment.memo || theme?.name || "图像区域"}`} title={segment.memo || theme?.name || "图像标注"} style={{ left: `${locator.x * 100}%`, top: `${locator.y * 100}%`, width: `${locator.width * 100}%`, height: `${locator.height * 100}%`, borderColor: theme ? annotationColor(theme.color, theme.color_rgb) : "var(--rw-accent)" }} onPointerDown={event => { if (!drawing) event.stopPropagation(); }} onClick={event => { if (!drawing) { event.stopPropagation(); annotations.focus(segment); } }} />;
        })}
        {region && <div className={styles.region} style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%` }} onPointerDown={event => startDrag(event, "move")}><span className={styles.resizeHandle} onPointerDown={event => startDrag(event, "resize")} /></div>}
      </div>
    </>}
    {url && material.mime_type === "application/pdf" && <iframe key={`${url}:${page}`} title="PDF 原文件" className={styles.pdf} src={`${url}#page=${page}`} />}
    {url && timed && <>
      {audio ? <audio aria-label={material.filename} controls src={url} preload="metadata" ref={node => { media.current = node; }} onLoadedMetadata={metadata} onTimeUpdate={timeUpdate} onSeeked={timeUpdate} onPlay={()=>setPlayingSelection(boundedPlayback.current)} onPause={paused} onEnded={paused} onError={() => setError("无法播放此音频，请核对文件是否完整。每次重新打开材料可刷新播放链接。")} /> : <video aria-label={material.filename} className={styles.video} controls src={url} ref={node => { media.current = node; }} onLoadedMetadata={metadata} onTimeUpdate={timeUpdate} onSeeked={timeUpdate} onPlay={()=>setPlayingSelection(boundedPlayback.current)} onPause={paused} onEnded={paused} onError={() => setError("无法播放此视频，请核对编码与文件完整性。")} />}
      <VideoSelection duration={duration} start={range.start_seconds} end={range.end_seconds}
        cursor={current} playing={playingSelection} onPlay={playRange}
        onSelect={(start_seconds,end_seconds)=>editRange({...range,start_seconds,end_seconds})}
        readPosition={()=>media.current?.currentTime ?? current}
        onAnnotate={annotations.enabled ? point => {
          media.current?.pause();
          const at = media.current?.currentTime ?? current;
          annotations.annotate(point ? {kind:"time",start_seconds:at,end_seconds:at,point:true,note:""} : range);
        } : undefined}/>
      {duration !== null && !validRange && <p role="status">请选择文件范围内的起止时间。</p>}
    </>}
    {error && <p role="alert" className="rw-error">{error}</p>}
  </section>;
}
