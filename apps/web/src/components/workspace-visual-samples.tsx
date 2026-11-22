"use client";
import {watchProcessing} from "./workspace-run-notice";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { datasetsApi } from "@/lib/api-endpoints";
import type { Dataset } from "@/lib/types";
import { DownloadButton } from "./download-button";

type VisualRun = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted";
  progress: string;
  started_at: string;
  dataset_ids: string[];
  summary_id?: string | null;
  package_id?: string | null;
  error_message?: string | null;
};
type Match = { dataset_id: string; name: string; description: string; score: number; available: boolean };
type Props = { dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; taskId?: string; onSaved?: () => Promise<void> };

export function WorkspaceVisualSamples({ dataset, datasets, onChoose, taskId, onSaved }: Props) {
  const client = useQueryClient();
  const endpoint = `/api/v1/projects/${dataset.project_id}/visual-analysis/runs`;
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([dataset.id]);
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [comparison, setComparison] = useState<{ runId: string; items: Match[] } | null>(null);
  const [activeRun, setActiveRun] = useState("");
  const images = datasets.filter(item => item.upload_status !== "deleted" && /^(png|jpg|jpeg|gif|webp)$/i.test(item.file_format));
  const runs = useQuery({
    queryKey: ["visual-runs", dataset.project_id, dataset.id],
    queryFn: ({ signal }) => apiFetch<{ items: VisualRun[] }>(`${endpoint}?dataset_id=${encodeURIComponent(dataset.id)}`, { signal }),
    refetchInterval: query => query.state.data?.items.some(run => run.status === "queued" || run.status === "running") ? 1500 : false,
  });
  useEffect(() => { setSelected([dataset.id]); setDescriptions({}); setComparison(null); setError(""); setOpen(false); }, [dataset.id]);
  useEffect(() => {
    const run = runs.data?.items.find(item => item.id === activeRun);
    if (!run || run.status === "queued" || run.status === "running") return;
    setActiveRun("");
    void client.invalidateQueries({ queryKey: ["datasets"] });
    if (onSaved) void onSaved();
  }, [activeRun, runs.data, client, onSaved]);
  async function start(force = false) {
    if (busy || !selected.length) return;
    setBusy(true); setError("");
    try {
      const result = await apiFetch<VisualRun & { reused: boolean }>(endpoint, { method: "POST", body: JSON.stringify({
        dataset_ids: selected, task_id: taskId || null, descriptions: Object.fromEntries(selected.filter(id => descriptions[id]?.trim()).map(id => [id, descriptions[id].trim()])), force,
      }) });
      watchProcessing({project:dataset.project_id,task:taskId,source:dataset.id,url:endpoint+"/"+result.id,title:"图像比较"});
      setActiveRun(result.id);
      await runs.refetch();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "图像处理未开始，请重试。"); }
    finally { setBusy(false); }
  }
  async function compare(runId: string) {
    setBusy(true); setError("");
    try {
      const result = await apiFetch<{ items: Match[] }>(`${endpoint}/${runId}/similar?dataset_id=${encodeURIComponent(dataset.id)}`);
      setComparison({ runId, items: result.items });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取图像比较结果。"); }
    finally { setBusy(false); }
  }
  return <section className="rw-source-settings" aria-label="图像比较">
    <button type="button" onClick={() => setOpen(value => !value)}>{open ? "收起图像比较" : "比较图片"}</button>
    {open && <>
      <h4>ResNet18 · 图像相似度</h4>
      <div className="rw-source-versions">{images.map(item => <div key={item.id}>
        <label><input type="checkbox" checked={selected.includes(item.id)} disabled={item.id === dataset.id || busy}
          onChange={event => setSelected(previous => event.target.checked ? [...previous, item.id] : previous.filter(id => id !== item.id))} /> {item.original_filename}</label>
        {selected.includes(item.id) && <label className="rw-field">描述（可选）<input value={descriptions[item.id] || ""} maxLength={4000} onChange={event => setDescriptions(previous => ({ ...previous, [item.id]: event.target.value }))} /></label>}
      </div>)}</div>
      <div className="rw-inline-actions"><button className="rw-primary" type="button" disabled={busy || selected.length < 2} onClick={() => void start()}>提取并比较</button><span>{selected.length} 张图片</span></div>
    </>}
    {error && <p role="alert" className="rw-error">{error}</p>}
    {runs.isError && <p role="alert" className="rw-error">{runs.error.message}</p>}
    {!!runs.data?.items.length && <section><h3>图像处理记录</h3>{runs.data.items.map(run => <div key={run.id}>
      <p>{new Date(run.started_at).toLocaleString("zh-CN")} · {run.dataset_ids.length} 张 · ResNet18 · {run.status === "completed" ? "已完成" : run.status === "running" || run.status === "queued" ? run.progress : "未完成"}</p>
      {run.error_message && <p role="alert" className="rw-error">{run.error_message}</p>}
      <div className="rw-inline-actions">
        {run.status === "completed" && <button type="button" disabled={busy} onClick={() => void compare(run.id)}>查看相似图片</button>}
        {run.summary_id && <button type="button" onClick={() => onChoose(run.summary_id!)}>查看摘要</button>}
        {run.package_id && <DownloadButton identity={run.package_id} download={signal => datasetsApi.download(run.package_id!, "图像完整特征.npz", signal)}>导出完整特征</DownloadButton>}
      </div>
      {comparison?.runId === run.id && <div><p>与 {dataset.original_filename} 的余弦相似度</p>{comparison.items.map(item => <div key={item.dataset_id}>
        <button type="button" disabled={!item.available} onClick={() => onChoose(item.dataset_id)}>{item.name}</button> <meter min={-1} max={1} value={item.score} aria-label={`${item.name} 相似度`} /> {item.score.toFixed(3)} {item.description && <span>· {item.description}</span>}{!item.available && <span> · 原文件已变化</span>}
      </div>)}</div>}
    </div>)}</section>}
  </section>;
}
