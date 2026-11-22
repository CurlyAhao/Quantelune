"use client";

import { useLanguage } from "@/providers/language-provider";

export type QdpxSummary = { sources: number; codes: number; cases: number; links: number; codings: number; journals?: number; memo?: boolean; items: string[] };

export function QdpxImportSummary({ summary }: { summary: QdpxSummary }) {
  const { locale } = useLanguage();
  const zh = locale === "zh-CN";
  const counts = zh
    ? `材料 ${summary.sources} · 代码 ${summary.codes} · 个案 ${summary.cases} · 关联 ${summary.links} · 编码 ${summary.codings}${summary.journals ? ` · 日志 ${summary.journals}` : ""}${summary.memo ? " · 项目备忘" : ""}`
    : `Sources ${summary.sources} · Codes ${summary.codes} · Cases ${summary.cases} · Links ${summary.links} · Codings ${summary.codings}`;
  return <div><p>{counts}</p>{summary.items.length > 0 && <ul>{summary.items.map((item, index) => <li key={index}>{item}</li>)}</ul>}</div>;
}
