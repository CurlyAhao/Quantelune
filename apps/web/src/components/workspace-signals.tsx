"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { datasetsApi } from "@/lib/api-endpoints";
import type { Dataset } from "@/lib/types";
import { DownloadButton } from "./download-button";
import { useAnnotationSource } from "./workspace-annotations";
import { MessageSquarePlusIcon } from "lucide-react";
import styles from "./workspace-annotations.module.css";

type Options = { timezone?: string; start?: number; end?: number; window?: number; window_seconds?: number; step_seconds?: number | null; e4_offset?: number; cgm_offset?: number; summary_tolerance?: number; cgm_tolerance?: number; task_id?: string; hapt_window_seconds?: number; hapt_step_seconds?: number; hapt_index_base?: number; hapt_end_inclusive?: boolean };
type Track = { id: string; name: string; rate: number | null; samples: number; missing: number; points: [number, number | null][]; events?: boolean };
type Window = { Index: string; Timestamp?: number; glucose?: number; start: number; end: number; end_inclusive?: boolean; matched_ecg_samples?: number };
type Activity = { activity_id: number; name: string; start_index: number; end_index_exclusive: number };
type Preview = { kind?: string; source_fingerprint?: string; default_channels?: string[]; time_precision?: { duplicate_display_times: number; maximum_deviation_seconds: number }; subject: string; session: string; timezone?: string; packaged: boolean; range: [number, number]; selection: [number, number]; common: [number, number] | null; tracks: Track[]; cgm: { Time: number; glucose: number | null }[]; windows: Window[]; activities?: Activity[] };
const utc = (value: number) => new Date(value * 1000).toISOString().slice(0, 23);
const viewKey = (value?: Options) => value ? JSON.stringify({
  start: value.start ?? null, end: value.end ?? null, window: value.window ?? null,
  timezone: value.timezone ?? null, e4_offset: value.e4_offset ?? 0, cgm_offset: value.cgm_offset ?? 0,
  summary_tolerance: value.summary_tolerance ?? 1, cgm_tolerance: value.cgm_tolerance ?? 330,
  window_seconds: value.window_seconds ?? 30, step_seconds: value.step_seconds ?? null, hapt_window_seconds: value.hapt_window_seconds ?? 2.56,
  hapt_step_seconds: value.hapt_step_seconds ?? 1.28, hapt_index_base: value.hapt_index_base ?? 1,
  hapt_end_inclusive: value.hapt_end_inclusive ?? true,
}) : "";
const time = (value: number) => new Date(value * 1000).toISOString().slice(11, 23);

export function WorkspaceSignals({ dataset, datasets, onChoose, taskId, onSaved }: { dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; taskId?: string; onSaved?: () => Promise<void> }) {
  const client = useQueryClient();
  const [options, setOptions] = useState<Options>({});
  const [data, setData] = useState<Preview>();
  const [applied, setApplied] = useState<Options>();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [hidden, setHidden] = useState<string[]>([]);
  const [remoteView, setRemoteView] = useState<Options>();
  const appliedRef = useRef<Options | undefined>(undefined); appliedRef.current = applied;
  const optionsRef = useRef(options); optionsRef.current = options;
  const busyRef = useRef(busy); busyRef.current = busy;
  const [made, setMade] = useState<{ package_id: string; summary_id: string; windows: number }>();
  const request = useRef(0);
  const alive = useRef(true);
  const channelsChosen = useRef(false);
  const drag = useRef<number | null>(null);
  const [dragEnd, setDragEnd] = useState<number | null>(null);
  const endpoint = `/api/v1/datasets/${dataset.id}/signals`;
  async function load(next: Options, persist = true) {
    const sequence = ++request.current;
    const before = viewKey(optionsRef.current);
    setBusy("正在读取与对齐…"); setError("");
    if (persist) setOptions(next);
    try {
      const result = await apiFetch<Preview>(`${endpoint}/preview${persist ? "" : "?persist=false"}`, { method: "POST", body: JSON.stringify({ ...next, task_id: taskId }) });
      if (!alive.current || sequence !== request.current) return;
      if (!persist && viewKey(optionsRef.current) !== before) { setRemoteView(next); return; }
      const snapshot = { ...next, start: result.selection[0], end: result.selection[1] };
      const changed = viewKey(snapshot) !== viewKey(appliedRef.current);
      setData(result); setOptions(snapshot); setApplied(snapshot); setRemoteView(undefined);
      if (changed) window.dispatchEvent(new CustomEvent("statforge:material-context-changed", { detail: {project: dataset.project_id, task: taskId || "project", dataset: dataset.id} }));
      if (!channelsChosen.current) {
        channelsChosen.current = true;
        setHidden(result.tracks.filter(track => !(result.default_channels ?? ['zephyr.ECG.EcgWaveform', 'e4.BVP.BVP']).includes(track.id)).map(track => track.id));
      }
    } catch (reason) { if (alive.current && sequence === request.current) setError(reason instanceof Error ? reason.message : "无法读取会话，请核对文件和时区。"); }
    finally { if (alive.current && sequence === request.current) setBusy(""); }
  }
  const loader = useRef(load); loader.current = load;
  const annotations = useAnnotationSource({ filename: dataset.original_filename, dataset_id: dataset.id, dataset_sha256: dataset.sha256, source_fingerprint: data?.source_fingerprint, task_id: taskId }, async locator => {
    if (locator.kind !== "signal_time") return;
    if (data) setHidden(data.tracks.filter(track => !locator.channel_ids.includes(track.id)).map(track => track.id));
    await loader.current({ ...options, ...(locator.window_index != null ? { window: locator.window_index } : {}), start: locator.start_seconds, end: locator.end_seconds });
  });
  useEffect(() => {
    alive.current = true;
    setData(undefined); setApplied(undefined); setMade(undefined); channelsChosen.current = false;
    const controller = new AbortController();
    void apiFetch<Options>(`${endpoint}/settings?task_id=${taskId || "project"}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) void loader.current(value, !Object.keys(value).length); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => { alive.current = false; controller.abort(); request.current++; };
  }, [endpoint, taskId]);
  useEffect(() => {
    let stopped = false, pending = false;
    const poll = async () => {
      if (stopped || pending || document.hidden) return;
      pending = true;
      try {
        const remote = await apiFetch<Options>(`${endpoint}/settings?task_id=${taskId || "project"}`);
        if (stopped || !appliedRef.current || !Object.keys(remote).length || viewKey(remote) === viewKey(appliedRef.current)) return;
        if (busyRef.current || viewKey(optionsRef.current) !== viewKey(appliedRef.current)) setRemoteView(remote);
        else await loader.current(remote, false);
      } catch { /* A transient settings read must not replace the current view. */ }
      finally { pending = false; }
    };
    const timer = window.setInterval(() => void poll(), 3000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [endpoint, taskId]);
  async function pack() {
    setBusy("正在分段打包…"); setError("");
    try {
      const result = await apiFetch<{ package_id: string; summary_id: string; windows: number }>(`${endpoint}/package`, { method: "POST", body: JSON.stringify({ ...applied, task_id: taskId }) });
      setMade(result);
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await onSaved?.();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "打包失败，原始文件仍保留。"); }
    finally { setBusy(""); }
  }
  const displayed = data?.tracks.filter(track => !hidden.includes(track.id)) ?? [];
  const relative = data?.kind === "bidmc" || data?.kind === "hapt";
  const hapt = data?.kind === "hapt";
  const formatTime = (value: number) => relative ? `${Number(value.toFixed(3))} 秒` : `${utc(value)} UTC`;
  const selected = data?.selection ?? [0, 1];
  const plotRange = [selected[0], Math.max(selected[1], ...(data?.cgm.map(row => row.Time) ?? []))];
  function zoom(factor: number) {
    if (!data) return;
    const center = (selected[0] + selected[1]) / 2, half = (selected[1] - selected[0]) * factor / 2;
    void load({ ...options, start: Math.max(data.range[0], center - half), end: Math.min(data.range[1], center + half) });
  }
  const packages = datasets.filter(row => row.source_dataset_id === dataset.id);
  const unapplied = !!applied && JSON.stringify(options) !== JSON.stringify(applied);
  return <section className="rw-signals" aria-label="原始信号" onPointerDownCapture={annotations.activate}>
    <div className="rw-content-tools"><h2>{data ? `${data.subject} · ${data.session}` : dataset.original_filename}</h2>
      {dataset.file_format === "pkl" && <DownloadButton identity={dataset.id} download={signal => datasetsApi.download(dataset.id, dataset.original_filename, signal)}>导出 PKL</DownloadButton>}
    </div>
    {hapt && <div className="rw-inline-actions" aria-label="实验会话">{datasets.filter(row => row.file_format === "hapt").map(row => <button key={row.id} type="button" disabled={row.id === dataset.id} onClick={() => onChoose(row.id)}>{row.original_filename.replace(/(?: · 联合信号|\.hapt)$/, "").replace("_", " · ")}</button>)}</div>}
    {(!data || !options.timezone) && dataset.file_format === "physiocgm" && <div className="rw-inline-actions">
      <label>记录时区 <input aria-label="记录时区" placeholder="例如 America/Chicago" value={options.timezone || ""} onChange={event => setOptions({ ...options, timezone: event.target.value })} list="physio-timezones" /></label>
      <datalist id="physio-timezones"><option value="America/Chicago" /><option value="UTC" /><option value="Asia/Shanghai" /></datalist>
      <button disabled={!!busy || !options.timezone} onClick={() => void load(options)}>读取会话</button>
    </div>}
    {busy && <p role="status">{busy}</p>}
    {error && <p role="alert" className="rw-error">{error}</p>}
    {remoteView && <p role="status">其他操作已更新信号选段；当前有未应用的修改。 <button type="button" disabled={!!busy} onClick={() => void load(remoteView, false)}>加载更新</button></p>}
    {data && <>
      {data.packaged && <label className="rw-field">窗口 <SearchSelect aria-label="信号窗口" disabled={!!busy} value={options.window ?? 0} onChange={event => void load({ ...options, window: Number(event.target.value), start: undefined, end: undefined })}>
        {data.windows.map((window, index) => <option key={index} value={index}>#{window.Index} · {relative ? `${formatTime(window.start)} — ${formatTime(window.end)}` : `${utc(window.Timestamp!)} UTC · ${window.glucose} mg/dL`}</option>)}
      </SearchSelect></label>}
      <div className={styles.signalToolbar}>
      <div className="rw-signal-range">
        <label>开始（{relative ? "秒" : "UTC"}）<input type={relative ? "number" : "datetime-local"} step="0.001" aria-label="选段开始" value={options.start === undefined ? "" : relative ? Number(options.start.toFixed(3)) : utc(options.start)} onChange={event => setOptions({ ...options, start: event.target.value ? (relative ? Number(event.target.value) : Date.parse(`${event.target.value}Z`) / 1000) : undefined })} /></label>
        <label>结束（{relative ? "秒" : "UTC"}）<input type={relative ? "number" : "datetime-local"} step="0.001" aria-label="选段结束" value={options.end === undefined ? "" : relative ? Number(options.end.toFixed(3)) : utc(options.end)} onChange={event => setOptions({ ...options, end: event.target.value ? (relative ? Number(event.target.value) : Date.parse(`${event.target.value}Z`) / 1000) : undefined })} /></label>
        {relative && !hapt && !data.packaged && <><label>每窗（秒）<input type="number" aria-label="窗口长度" min="0" step="any" value={options.window_seconds ?? 30} onChange={event => setOptions({ ...options, window_seconds: Number(event.target.value) })} /></label><label>步长（秒）<input type="number" aria-label="窗口步长" min="0" step="any" placeholder="默认等于窗长" value={options.step_seconds ?? ""} onChange={event => setOptions({ ...options, step_seconds: event.target.value === "" ? null : Number(event.target.value) })} /></label></>}
        {hapt && !data.packaged && <><label>每窗（秒）<input type="number" min="0.02" step="0.02" value={options.hapt_window_seconds ?? 2.56} onChange={event => setOptions({ ...options, hapt_window_seconds: Number(event.target.value) })} /></label><label>步长（秒）<input type="number" min="0.02" step="0.02" value={options.hapt_step_seconds ?? 1.28} onChange={event => setOptions({ ...options, hapt_step_seconds: Number(event.target.value) })} /></label></>}
        <button disabled={!!busy} onClick={() => void load(options)}>应用选段</button>
        <button disabled={!!busy} onClick={() => zoom(0.5)}>放大</button><button disabled={!!busy} onClick={() => zoom(2)}>缩小</button>
        <button disabled={!!busy} onClick={() => void load({ ...options, start: data.range[0], end: data.range[1] })}>全段</button>
        {annotations.enabled && <><button type="button" onClick={annotations.open}>标注 {annotations.segments.length || ""}</button><button type="button" disabled={!!busy || !displayed.length} onClick={() => annotations.annotate({ kind: "signal_time", start_seconds: selected[0], end_seconds: selected[1], channel_ids: displayed.map(track => track.id), ...(data.packaged ? { window_index: options.window ?? 0 } : {}) })}><MessageSquarePlusIcon size={15} />标注选段</button></>}
        {!data.packaged && <button className="rw-primary" disabled={!!busy || !applied || unapplied || !data.windows.length} onClick={() => void pack()}>分段并保存 PKL</button>}
      </div>
      {made && <p role="status">已保存 {made.windows} 个窗口 <button onClick={() => onChoose(made.package_id)}>打开 PKL</button> <button onClick={() => onChoose(made.summary_id)}>查看窗口摘要</button></p>}
      <div className={`rw-signal-axis ${styles.signalAxis}`}>{[0, .25, .5, .75, 1].map(fraction => <span key={fraction}>{formatTime(plotRange[0] + fraction * (plotRange[1] - plotRange[0]))}</span>)}</div>
      </div>
      {!data.packaged && unapplied && <p className="rw-muted">选段或参数已修改，请先应用。</p>}
      <p className="rw-muted">{data.windows.length} 个{relative ? "时间" : " CGM "}窗口 · {((selected[1] - selected[0]) / 60).toFixed(2)} 分钟</p>
      {data.common ? <p className="rw-muted">{relative ? "波形 / 参数" : "ECG / BVP"}共同范围：{formatTime(data.common[0])} — {formatTime(data.common[1])}</p> : <p role="status">当前选段没有共同记录，请核对时间范围。</p>}
      <details><summary>通道与时间设置</summary>
        <div className="rw-signal-channels">{data.tracks.map(track => <label key={track.id}><input type="checkbox" checked={!hidden.includes(track.id)} onChange={event => setHidden(event.target.checked ? hidden.filter(id => id !== track.id) : [...hidden, track.id])} />{track.name}</label>)}</div>
        {relative && !hapt && <><p className="rw-muted">波形按 125 Hz 样本序号恢复精细时间，CSV 原时间列保留。呼吸事件按两位标注者分别显示，样本号从 1 开始。</p><p className="rw-muted">各窗左闭右开，最后一窗包含所选结束时刻；不重采样、不填补缺失。</p><p className="rw-muted">BIDMC · Pimentel 等 · DOI: 10.13026/C2208R · ODC Attribution 1.0</p></>}
        {hapt && <>{!data.packaged && <div className="rw-signal-range"><label>活动索引起点<SearchSelect value={options.hapt_index_base ?? 1} onChange={event => setOptions({ ...options, hapt_index_base: Number(event.target.value) })}><option value={1}>1 基</option><option value={0}>0 基</option></SearchSelect></label><label><input type="checkbox" checked={options.hapt_end_inclusive ?? true} onChange={event => setOptions({ ...options, hapt_end_inclusive: event.target.checked })} />终点包含</label><button disabled={!!busy} onClick={() => void load(options)}>应用索引解释</button></div>}</>}
        {!relative && !data.packaged && <div className="rw-signal-range"><label>记录时区<input value={options.timezone || ""} onChange={event => setOptions({ ...options, timezone: event.target.value })} /></label>
          {([['e4_offset', 'E4 偏移（秒）', 0], ['cgm_offset', 'CGM 偏移（秒）', 0], ['summary_tolerance', 'Summary 容差（秒）', 1], ['cgm_tolerance', 'CGM 容差（秒）', 330]] as const).map(([key, label, fallback]) => <label key={key}>{label}<input type="number" step="any" value={options[key] ?? fallback} onChange={event => setOptions({ ...options, [key]: Number(event.target.value) })} /></label>)}
          <button disabled={!!busy} onClick={() => void load(options)}>更新对齐</button>
        </div>}
      </details>
      {hapt && <HaptIntervals activities={data.activities ?? []} selection={selected} />}
      <div className="rw-signal-plots" onPointerDown={event => {
        if (busy || !(event.target instanceof SVGElement)) return;
        const rectangle = event.currentTarget.getBoundingClientRect();
        drag.current = Math.max(0, Math.min(1, (event.clientX - rectangle.left) / rectangle.width));
        setDragEnd(drag.current); event.currentTarget.setPointerCapture(event.pointerId);
      }} onPointerMove={event => { if (drag.current !== null) { const r = event.currentTarget.getBoundingClientRect(); setDragEnd(Math.max(0, Math.min(1, (event.clientX - r.left) / r.width))); } }} onPointerUp={() => {
        const start = drag.current, end = dragEnd; drag.current = null; setDragEnd(null);
        if (start !== null && end !== null && Math.abs(end - start) > 0.01) void load({ ...options, start: plotRange[0] + Math.min(start, end) * (plotRange[1] - plotRange[0]), end: plotRange[0] + Math.max(start, end) * (plotRange[1] - plotRange[0]) });
      }} onPointerCancel={() => { drag.current = null; setDragEnd(null); }}>
        {displayed.map(track => <SignalTrack key={track.id} track={track} range={plotRange} relative={relative} />)}
        {!relative && <SignalTrack track={{ id: 'cgm', name: 'CGM · mg/dL', rate: null, samples: data.cgm.length, missing: 0, points: data.cgm.map(row => [row.Time, row.glucose]) }} range={plotRange} dots />}
        {annotations.segments.map(segment => {
          const locator = segment.locator;
          if (locator.kind !== "signal_time" || segment.dataset_sha256 !== dataset.sha256 || (segment.source_fingerprint && segment.source_fingerprint !== data.source_fingerprint) || locator.end_seconds <= plotRange[0] || locator.start_seconds >= plotRange[1]) return null;
          const left = Math.max(locator.start_seconds, plotRange[0]), right = Math.min(locator.end_seconds, plotRange[1]);
          return <button type="button" className={styles.signalAnnotation} key={segment.id} title={segment.memo || "查看时间段标注"} aria-label={`查看时间段标注：${formatTime(locator.start_seconds)} — ${formatTime(locator.end_seconds)}`} data-focused={annotations.focusedId === segment.id} style={{ left: `${(left - plotRange[0]) / (plotRange[1] - plotRange[0]) * 100}%`, width: `${(right - left) / (plotRange[1] - plotRange[0]) * 100}%` }} onPointerDown={event => event.stopPropagation()} onClick={event => { event.stopPropagation(); annotations.focus(segment); }} />;
        })}
        {drag.current !== null && dragEnd !== null && <div className="rw-signal-drag" style={{ left: `${Math.min(drag.current, dragEnd) * 100}%`, width: `${Math.abs(drag.current - dragEnd) * 100}%` }} />}
      </div>
      <div className="rw-signal-axis"><span>{formatTime(plotRange[0])}</span><span>{formatTime(plotRange[1])}</span></div>
      {!!packages.length && <details><summary>已保存的分段与摘要 · {packages.length}</summary><div className="rw-inline-actions">{packages.map((row, index) => <button key={row.id} title={row.original_filename} onClick={() => onChoose(row.id)}>{row.signal_session ? "分段 PKL" : "窗口摘要"} {index + 1}{row.row_count ? ` · ${row.row_count} 窗口` : ""}</button>)}</div></details>}
      <details><summary>窗口索引 · {data.windows.length}</summary><div className="rw-table-scroll"><table className="rw-data-table"><thead><tr><th>窗口</th><th>{relative ? "开始（秒）" : "CGM 时间 UTC"}</th><th>{relative ? "结束（秒）" : "mg/dL"}</th><th>时长（秒）</th></tr></thead><tbody>
        {data.windows.map((window, index) => <tr key={index}><td><button onClick={() => void load({ ...options, ...(data.packaged ? { window: index } : {}), start: window.start, end: window.end })}>#{window.Index}</button></td><td>{relative ? window.start : utc(window.Timestamp!)}</td><td>{relative ? window.end : window.glucose}</td><td>{(window.end - window.start).toFixed(2)}</td></tr>)}
      </tbody></table></div></details>
    </>}
  </section>;
}

function SignalTrack({ track, range, dots = false, relative = false }: { track: Track; range: number[]; dots?: boolean; relative?: boolean }) {
  const visible = track.points.filter(([x]) => x >= range[0] && x <= range[1]);
  const numbers = visible.flatMap(([, y]) => y === null ? [] : [y]);
  const low = numbers.length ? Math.min(...numbers) : 0, high = numbers.length ? Math.max(...numbers) : 1;
  const y = (value: number) => 95 - (value - low) / (high - low || 1) * 80;
  const x = (value: number) => (value - range[0]) / (range[1] - range[0]) * 1000;
  let drawing = false;
  const path = visible.map(([t, v]) => { if (v === null) { drawing = false; return ''; } const command = `${drawing ? 'L' : 'M'}${x(t).toFixed(2)},${y(v).toFixed(2)}`; drawing = true; return command; }).join(' ');
  return <section className="rw-signal-track"><div><strong>{track.name}</strong><span>{track.rate ? `${track.rate.toFixed(2)} Hz · ` : ''}{track.samples.toLocaleString()} 点{track.missing ? ` · ${track.missing} 缺失` : ''}</span></div>
    <svg viewBox="0 0 1000 110" preserveAspectRatio="none" role="img" aria-label={`${track.name}，${relative ? `${range[0]} 至 ${range[1]} 秒` : `${time(range[0])} 至 ${time(range[1])} UTC`}`}>
      {[0, 250, 500, 750, 1000].map(position => <line key={position} x1={position} x2={position} y1="0" y2="110" stroke="var(--rw-line)" />)}
      {!dots && !track.events && <path d={path} fill="none" stroke="var(--rw-accent)" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />}
      {track.events && visible.map(([t, sample], index) => <line key={index} x1={x(t)} x2={x(t)} y1="25" y2="85" stroke="var(--rw-accent)" vectorEffect="non-scaling-stroke"><title>{t.toFixed(3)} 秒 · 原样本号 {sample}</title></line>)}
      {dots && visible.map(([t, v], index) => v !== null && <circle key={index} cx={x(t)} cy={y(v)} r="3" fill="var(--rw-accent)"><title>{utc(t)} UTC · {v} mg/dL</title></circle>)}
      {!track.events && <><text x="6" y="13" fill="currentColor" fontSize="11">{high.toPrecision(4)}</text><text x="6" y="108" fill="currentColor" fontSize="11">{low.toPrecision(4)}</text></>}
      {!numbers.length && <text x="450" y="55" fill="currentColor" fontSize="13">无记录</text>}
    </svg>
  </section>;
}


/** Display every overlapping annotation and the complement of their union. */
function HaptIntervals({ activities, selection }: { activities: Activity[]; selection: number[] }) {
  const first = Math.ceil(selection[0] * 50 - 1e-9);
  const stop = Math.floor(selection[1] * 50 + 1e-9) + 1;
  const labeled = activities.map(event => ({
    name: `${event.name} · #${event.activity_id}`,
    start: Math.max(first, event.start_index), end: Math.min(stop, event.end_index_exclusive),
  })).filter(event => event.start < event.end).sort((a, b) => a.start - b.start || a.end - b.end);
  const gaps: { name: string; start: number; end: number }[] = [];
  let cursor = first;
  for (const event of labeled) {
    if (event.start > cursor) gaps.push({ name: "未标注", start: cursor, end: event.start });
    cursor = Math.max(cursor, event.end);
  }
  if (cursor < stop) gaps.push({ name: "未标注", start: cursor, end: stop });
  const intervals = [...labeled, ...gaps].sort((a, b) => a.start - b.start || a.end - b.end);
  return <section aria-label="活动与未标注区间">
    <strong>活动与未标注区间</strong>
    <p className="rw-muted">{labeled.length} 个活动区间 · {gaps.length} 段未标注</p>
    {intervals.length ? <ul style={{ maxHeight: "6em", overflowY: "auto", marginBlock: "0.5em" }}>{intervals.map((interval, index) => <li key={`${interval.name}-${interval.start}-${interval.end}-${index}`}>
      {interval.name}：{(interval.start / 50).toFixed(2)}–{((interval.end - 1) / 50).toFixed(2)} 秒
    </li>)}</ul> : <p className="rw-muted">当前选段没有有效样本。</p>}
  </section>;
}
