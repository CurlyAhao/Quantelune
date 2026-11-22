"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCheckIcon, XIcon } from "lucide-react";
import { datasetsApi } from "@/lib/api-endpoints";
import { ApiError } from "@/lib/api-client";
import { sourceUpdatesApi, type SourceComparison } from "@/lib/source-updates";
import type { Dataset } from "@/lib/types";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";

export function SourceFileUpdate({
  dataset,
  onSelect,
}: {
  dataset: Dataset;
  onSelect: (id: string) => void;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const cache = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [sheets, setSheets] = useState<string[]>([]);
  const [sheet, setSheet] = useState("");
  const [encoding, setEncoding] = useState(
    dataset.csv_encoding === "utf-8" ? "auto" : dataset.csv_encoding || "auto",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [comparison, setComparison] = useState<SourceComparison | null>(null);
  const [preview, setPreview] = useState<"before" | "after">("after");
  const [newVersion, setNewVersion] = useState<Dataset | null>(null);
  const state = useQuery({
    queryKey: ["source-versions", dataset.id],
    queryFn: ({ signal }) => sourceUpdatesApi.versions(dataset.id, signal),
    retry: false,
  });
  useEffect(() => () => controller.current?.abort(), []);
  const source = state.data?.source || dataset;
  useEffect(() => {
    if (!file)
      setEncoding(
        source.csv_encoding === "utf-8"
          ? "auto"
          : source.csv_encoding || "auto",
      );
  }, [file, source.id, source.csv_encoding]);
  const versions = state.data?.versions || [source];
  const latest = versions.at(-1);
  const hasNewer =
    latest &&
    latest.id !== source.id &&
    (latest.source_version || 1) > (source.source_version || 1);
  function message(reason: unknown) {
    if (reason instanceof ApiError) {
      if (reason.errorCode === "FILE_CSV_ENCODING_REQUIRED")
        return copy(
          "请选择编码后重新核对。",
          "Choose an encoding and check again.",
        );
      if (reason.errorCode === "FILE_PARSE_ERROR")
        return copy(
          "无法读取文件，请检查格式和工作表。",
          "Cannot read this file. Check its format and worksheet.",
        );
      return reason.detail;
    }
    return copy(
      "核对未完成，请重新选择文件。",
      "Check incomplete. Choose the file again.",
    );
  }
  async function inspect(
    selected: File,
    selectedSheet: string,
    selectedEncoding: string,
  ) {
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setError("");
    setComparison(null);
    setNewVersion(null);
    setNotice("");
    try {
      const value = await sourceUpdatesApi.compare(
        dataset.id,
        selected,
        dataset.sha256,
        selectedSheet,
        selectedEncoding,
        abort.signal,
      );
      if (!abort.signal.aborted) setComparison(value);
    } catch (reason) {
      if (!abort.signal.aborted) setError(message(reason));
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }
  async function select(selected: File | null) {
    if (!selected || busy) return;
    setOpen(true);
    setFile(selected);
    setComparison(null);
    setError("");
    setNotice("");
    setNewVersion(null);
    setSheets([]);
    if (/\.xlsx$/i.test(selected.name)) {
      const abort = new AbortController();
      controller.current = abort;
      setBusy(true);
      try {
        const value = await datasetsApi.sheets(selected, abort.signal);
        if (abort.signal.aborted) return;
        setSheets(value.sheet_names);
        const active = value.sheet_names.includes(source.sheet_name || "")
          ? source.sheet_name!
          : value.sheet_names.length === 1
            ? value.sheet_names[0]
            : "";
        setSheet(active);
        setBusy(false);
        if (active) await inspect(selected, active, encoding);
      } catch (reason) {
        if (!abort.signal.aborted) {
          setError(message(reason));
          setBusy(false);
        }
      }
    } else {
      setSheet("");
      await inspect(selected, "", encoding);
    }
  }
  async function apply(mode: "version" | "separate" | "restore") {
    if (!file || !comparison || busy) return;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setError("");
    try {
      const value = await sourceUpdatesApi.apply(
        dataset.id,
        file,
        dataset.sha256,
        sheet,
        encoding,
        comparison,
        mode,
        abort.signal,
      );
      if (abort.signal.aborted) return;
      await cache.invalidateQueries({
        queryKey: ["datasets", dataset.project_id],
      });
      await cache.invalidateQueries({ queryKey: ["source-versions"] });
      if (mode === "restore") {
        setNotice(
          copy("原文件已核对并保存。", "Original file verified and saved."),
        );
        setComparison(null);
        setFile(null);
      } else {
        setNewVersion(value.dataset);
        setComparison(null);
        setFile(null);
        setNotice(
          value.outcome === "existing"
            ? copy(
                "项目中已有这一版本。",
                "This version is already in the project.",
              )
            : copy(
                "已保存，原数据和结果保留。",
                "Saved. Earlier data and results are kept.",
              ),
        );
        if (!state.data?.selected_is_prepared) onSelect(value.dataset.id);
      }
    } catch (reason) {
      if (!abort.signal.aborted) setError(message(reason));
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }
  const displayed = comparison?.[preview];
  return (
    <section
      className="source-file-update"
      aria-label={copy("文件版本", "File versions")}
    >
      <div className="source-update-line">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {copy("文件版本", "File versions")}
          {versions.length > 1 && ` · v${source.source_version || 1}`}
        </button>
        {hasNewer && (
          <button
            className="source-newer"
            onClick={() => {
              setOpen(true);
            }}
          >
            {copy(
              `有新版 v${latest.source_version}`,
              `New v${latest.source_version}`,
            )}
          </button>
        )}
        {state.data?.stored_available === false && (
          <span role="status">
            {copy("项目原文件无法读取", "Project source unavailable")}
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={busy || state.isPending || state.isError}
          onClick={() => input.current?.click()}
        >
          <FileCheckIcon className="size-4" />
          {copy("核对本地文件", "Check local file")}
        </Button>
        <input
          type="file"
          ref={input}
          hidden
          accept={`.${source.file_format}`}
          aria-label={copy("选择核对文件", "Choose file to check")}
          onChange={(event) => {
            void select(event.target.files?.[0] || null);
            event.target.value = "";
          }}
        />
      </div>
      {state.isError && (
        <p role="alert">
          {copy("文件版本暂时无法读取。", "File versions are unavailable.")}{" "}
          <button onClick={() => void state.refetch()}>
            {copy("重试", "Retry")}
          </button>
        </p>
      )}
      {open && (
        <div className="source-update-panel">
          <header>
            <h3>{copy("文件核对", "File check")}</h3>
            <Button
              variant="ghost"
              size="sm"
              aria-label={copy("关闭文件核对", "Close file check")}
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              <XIcon className="size-4" />
            </Button>
          </header>
          {state.data?.selected_is_prepared && (
            <p>
              {copy(
                `原始来源：${source.original_filename}`,
                `Original source: ${source.original_filename}`,
              )}
            </p>
          )}
          {versions.length > 1 && (
            <label className="source-version-picker">
              {copy("已保存版本", "Saved versions")}
              <SearchSelect
                aria-label={copy("已保存文件版本", "Saved file version")}
                value={source.id}
                disabled={busy}
                onChange={(event) => onSelect(event.target.value)}
              >
                {versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    v{item.source_version || 1} · {item.row_count}{" "}
                    {copy("行", "rows")} · {item.original_filename}
                  </option>
                ))}
              </SearchSelect>
            </label>
          )}
          {file && (
            <div className="source-selected-file">
              <strong title={file.name}>{file.name}</strong>
              {sheets.length > 1 && (
                <label>
                  {copy("工作表", "Worksheet")}
                  <SearchSelect
                    aria-label={copy("核对工作表", "Check worksheet")}
                    disabled={busy}
                    value={sheet}
                    onChange={(event) => {
                      setSheet(event.target.value);
                      setComparison(null);
                      setError("");
                    }}
                  >
                    <option value="">
                      {copy("选择工作表", "Choose a worksheet")}
                    </option>
                    {sheets.map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                  </SearchSelect>
                </label>
              )}
              {/\.csv$/i.test(file.name) && (
                <label>
                  {copy("编码", "Encoding")}
                  <SearchSelect
                    disabled={busy}
                    aria-label={copy("核对文件编码", "File check encoding")}
                    value={encoding}
                    onChange={(event) => {
                      setEncoding(event.target.value);
                      setComparison(null);
                      setError("");
                    }}
                  >
                    {[
                      ["auto", "UTF-8"],
                      ["gb18030", "GB18030 / GBK"],
                      ["big5", "Big5"],
                      ["cp1252", "Windows-1252"],
                      ["latin-1", "Latin-1"],
                    ].map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </SearchSelect>
                </label>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={busy || (sheets.length > 1 && !sheet)}
                onClick={() => void inspect(file, sheet, encoding)}
              >
                {copy("重新核对", "Check again")}
              </Button>
            </div>
          )}
          {busy && (
            <p role="status">{copy("正在核对文件…", "Checking file…")}</p>
          )}
          {error && (
            <p className="source-update-error" role="alert">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          {newVersion && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onSelect(newVersion.id)}
            >
              {copy("打开已保存文件", "Open saved file")}
            </Button>
          )}
          {comparison && (
            <div className="source-comparison">
              <p className="source-check-result">
                {comparison.same_content
                  ? copy("内容一致", "Content matches")
                  : comparison.bytes_identical
                    ? copy("读取方式有变化", "Reading options differ")
                    : copy("文件内容有变化", "File content changed")}
              </p>
              {!comparison.same_content && (
                <>
                  <div className="source-comparison-counts">
                    <span>
                      {copy("行", "Rows")} {comparison.before.row_count} →{" "}
                      {comparison.after.row_count}
                    </span>
                    <span>
                      {copy("列", "Columns")} {comparison.before.column_count} →{" "}
                      {comparison.after.column_count}
                    </span>
                  </div>
                  {!!comparison.column_changes.length && (
                    <details>
                      <summary>
                        {copy(
                          `字段变化 ${comparison.column_changes.length} 项`,
                          `${comparison.column_changes.length} column changes`,
                        )}
                      </summary>
                      <ul className="source-column-changes">
                        {comparison.column_changes.map((item) => (
                          <li key={item.column}>
                            <strong>{item.column}</strong>
                            <span>
                              {item.before || copy("无", "None")} →{" "}
                              {item.after || copy("无", "None")}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <details className="source-preview-details">
                    <summary>
                      {copy("查看前后数据", "Compare data previews")}
                    </summary>
                    <div className="source-preview-tabs">
                      {(["before", "after"] as const).map((value) => (
                        <button
                          key={value}
                          aria-pressed={preview === value}
                          onClick={() => setPreview(value)}
                        >
                          {value === "before"
                            ? copy("原版本预览", "Earlier preview")
                            : copy("所选文件预览", "Selected file preview")}
                        </button>
                      ))}
                    </div>
                    {displayed && (
                      <div className="data-table-scroll source-table">
                        <table className="data-preview-table">
                          <thead>
                            <tr>
                              {displayed.columns.map((column) => (
                                <th key={column.name}>{column.name}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {displayed.rows.map((row, index) => (
                              <tr key={index}>
                                {displayed.columns.map((column) => (
                                  <td key={column.name}>
                                    {row[column.name] == null
                                      ? "—"
                                      : String(row[column.name])}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </details>
                  <p>
                    {copy(
                      "原分析与整理副本保留。",
                      "Earlier analyses and prepared copies are kept.",
                    )}
                  </p>
                </>
              )}
              <div className="source-update-actions">
                {comparison.same_content ? (
                  <Button disabled={busy} onClick={() => void apply("restore")}>
                    {comparison.stored_available
                      ? copy("保存核对记录", "Save verification")
                      : copy("恢复项目原文件", "Restore project source")}
                  </Button>
                ) : (
                  <>
                    <Button
                      disabled={busy}
                      onClick={() => void apply("version")}
                    >
                      {copy("采用新版", "Use new version")}
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => void apply("separate")}
                    >
                      {copy("作为独立文件导入", "Import separately")}
                    </Button>
                  </>
                )}
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    setOpen(false);
                    setComparison(null);
                    setFile(null);
                  }}
                >
                  {copy("保留当前版本", "Keep current version")}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
