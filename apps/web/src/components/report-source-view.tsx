"use client";
import type { ReportBlock } from "@/lib/workspace-api";
import { reportFigure, reportQuote, reportTables } from "@/lib/report-output";
import { useLanguage } from "@/providers/language-provider";
import { ResearchResultTable } from "@/app/projects/[projectId]/statistics/statistics-result";

export function ReportSourceView({ block }: { block: ReportBlock }) {
  const { locale } = useLanguage();
  const quote = reportQuote(block);
  let svg = "",
    error = "";
  try {
    svg = reportFigure(block, locale);
  } catch (failure) {
    error =
      failure instanceof Error
        ? failure.message
        : locale === "zh-CN"
          ? "图形来源暂时无法读取"
          : "Figure source is unavailable";
  }
  const tables = reportTables(block, locale);
  return (
    <div className="report-source-view">
      {quote && <blockquote>{quote}</blockquote>}
      {error && <p role="alert">{error}</p>}
      {svg && (
        <div
          className="report-figure"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
      {tables.map((table, index) => (
        <ResearchResultTable key={index} table={table} />
      ))}
    </div>
  );
}
