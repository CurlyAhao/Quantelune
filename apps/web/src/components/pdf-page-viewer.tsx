"use client";

import { useEffect, useState } from "react";
import { materialApi } from "@/lib/material-api";
import type { Material } from "@/lib/material-types";
import styles from "./workspace-annotations.module.css";

/** Displays a single original page; page changes from citations override navigation. */
export function PdfPageViewer({ material, page, onPageChange }: { material: Material; page: number; onPageChange?: (page: number) => void }) {
  const [current, setCurrent] = useState(page);
  const [count, setCount] = useState<number | null>(null);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { setCurrent(page); }, [page, material.id, material.sha256, material.version]);
  useEffect(() => {
    const controller = new AbortController();
    setCount(null); setError("");
    void materialApi.pdfPageCount(material, controller.signal).then(result => {
      if (!controller.signal.aborted) setCount(result.page_count);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法读取 PDF 页数。"); });
    return () => controller.abort();
  }, [material.id, material.sha256, material.version]);
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = "";
    setUrl("");
    if (count === null) return () => controller.abort();
    if (current < 1 || current > count || !Number.isInteger(current)) {
      setError("页码超出原文件范围。");
      return () => controller.abort();
    }
    setError("");
    void materialApi.pdfPage(material, current, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      if (blob.type !== "application/pdf") throw new Error("无法读取 PDF 页面。");
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法读取 PDF 页面。"); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [material.id, material.sha256, material.version, current, count]);
  return <div>
    <div className="rw-inline-actions">
      <button type="button" disabled={count === null || current <= 1} onClick={() => { setCurrent(current - 1); onPageChange?.(current - 1); }}>上一页</button>
      <span>第 {current} 页{count !== null ? ` / 共 ${count} 页` : ""}</span>
      <button type="button" disabled={count === null || current >= count} onClick={() => { setCurrent(current + 1); onPageChange?.(current + 1); }}>下一页</button>
    </div>
    {error && <p role="alert" className="rw-error">{error}</p>}
    {url && <iframe key={url} title={`${material.filename} 第 ${current} 页`} className={styles.pdf} src={url} />}
  </div>;
}
