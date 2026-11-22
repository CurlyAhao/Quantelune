"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, apiUpload } from "@/lib/api-client";
import type { Material } from "@/lib/material-types";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";

type Summary = { pages: number; characters: number; extraction_status: string };
type Comparison = {
  same_content: boolean;
  stored_available: boolean;
  comparison_sha256: string;
  before: Summary;
  after: Summary & { filename: string; sha256: string };
};

export function MaterialSourceUpdate({
  material,
  disabled,
  beforeChange,
  onSelect,
  onBusy,
}: {
  material: Material;
  disabled: boolean;
  beforeChange: () => Promise<void>;
  onSelect: (id: string) => Promise<void>;
  onBusy: (value: boolean) => void;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const cache = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const versions = useQuery({
    queryKey: ["material-source-versions", material.id],
    queryFn: ({ signal }) =>
      apiFetch<{ versions: Material[]; stored_available: boolean }>(
        `/api/v1/materials/${material.id}/source-versions`,
        { signal },
      ),
    retry: false,
  });
  useEffect(() => () => controller.current?.abort(), []);
  const locked = busy || disabled;
  function message(reason: unknown) {
    if (reason instanceof ApiError) {
      if (reason.errorCode === "MATERIAL_FORMAT_UNSUPPORTED")
        return copy(
          "文件无法读取，请选择 UTF-8 文本、Markdown 或 PDF。",
          "Choose a UTF-8 text, Markdown or PDF file.",
        );
      if (reason.errorCode === "MATERIAL_SIZE_LIMIT")
        return copy(
          "文件过大，请分成较小的材料。",
          "The file is too large. Split it into smaller materials.",
        );
      return reason.detail;
    }
    return reason instanceof Error
      ? reason.message
      : copy("核对未完成，请重试。", "Check incomplete. Try again.");
  }
  async function check(selected: File) {
    if (locked) return;
    const abort = new AbortController();
    controller.current = abort;
    setOpen(true);
    setFile(selected);
    setComparison(null);
    setError("");
    setBusy(true);
    try {
      const form = new FormData();
      form.set("file", selected);
      form.set("source_sha256", material.sha256);
      const value = await apiUpload<Comparison>(
        `/api/v1/materials/${material.id}/source-check`,
        form,
        { signal: abort.signal },
      );
      if (!abort.signal.aborted) setComparison(value);
    } catch (reason) {
      if (!abort.signal.aborted) setError(message(reason));
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }
  async function select(id: string) {
    setError("");
    setBusy(true);
    try {
      await beforeChange();
      onBusy(true);
      await onSelect(id);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  async function apply(mode: "version" | "separate" | "restore") {
    if (!file || !comparison || locked) return;
    const abort = new AbortController();
    controller.current = abort;
    setError("");
    setBusy(true);
    try {
      await beforeChange();
      onBusy(true);
      if (abort.signal.aborted) return;
      const form = new FormData();
      form.set("file", file);
      form.set("source_sha256", material.sha256);
      form.set("comparison_sha256", comparison.comparison_sha256);
      form.set("mode", mode);
      const saved = await apiUpload<{ material: Material }>(
        `/api/v1/materials/${material.id}/source-update`,
        form,
        { signal: abort.signal },
      );
      await cache.invalidateQueries({ queryKey: ["material-source-versions"] });
      if (!abort.signal.aborted) {
        setComparison(null);
        setFile(null);
        await onSelect(saved.material.id);
      }
    } catch (reason) {
      if (!abort.signal.aborted) setError(message(reason));
    } finally {
      if (!abort.signal.aborted) setBusy(false);
      onBusy(false);
    }
  }
  return (
    <section
      className="source-file-update"
      aria-label={copy("材料版本", "Material versions")}
    >
      <div className="source-update-line">
        <button
          type="button"
          aria-expanded={open}
          disabled={locked}
          onClick={() => setOpen(!open)}
        >
          {copy("文件版本", "File versions")} · v{material.version}
        </button>
        <Button
          variant="ghost"
          size="sm"
          disabled={locked || versions.isPending || versions.isError}
          onClick={() => input.current?.click()}
        >
          {copy("核对本地文件", "Check local file")}
        </Button>
        <input
          ref={input}
          type="file"
          hidden
          accept={
            material.mime_type === "application/pdf" ? ".pdf" : ".txt,.md"
          }
          aria-label={copy("选择材料核对文件", "Choose material file to check")}
          onChange={(event) => {
            const selected = event.target.files?.[0];
            event.target.value = "";
            if (selected) void check(selected);
          }}
        />
      </div>
      {versions.isError && (
        <p role="alert">
          {copy("版本暂时无法读取。", "Versions are unavailable.")}{" "}
          <button onClick={() => void versions.refetch()}>
            {copy("重试", "Retry")}
          </button>
        </p>
      )}
      {open && (
        <div className="source-update-panel">
          {(versions.data?.versions.length ?? 0) > 1 && (
            <label className="source-version-picker">
              {copy("已保存版本", "Saved versions")}
              <SearchSelect
                aria-label={copy("已保存材料版本", "Saved material version")}
                value={material.id}
                disabled={locked}
                onChange={(event) => void select(event.target.value)}
              >
                {versions.data!.versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    v{item.version} · {item.filename}
                  </option>
                ))}
              </SearchSelect>
            </label>
          )}
          {versions.data?.stored_available === false && (
            <p>
              {copy(
                "项目原文件无法读取，可选择原文件恢复。",
                "Project source unavailable. Choose the original file to restore it.",
              )}
            </p>
          )}
          {busy && (
            <p role="status">{copy("正在核对材料…", "Checking material…")}</p>
          )}
          {error && (
            <p role="alert" className="source-update-error">
              {error}
            </p>
          )}
          {comparison && (
            <>
              <p>
                {comparison.same_content
                  ? copy("内容一致", "Content matches")
                  : copy("文件内容有变化", "File content changed")}
              </p>
              {!comparison.same_content && (
                <>
                  <p>
                    {copy("可读文字", "Extracted characters")}{" "}
                    {comparison.before.characters} →{" "}
                    {comparison.after.characters}
                  </p>
                  <p>
                    {copy(
                      "原编码与成果保留在原版本。",
                      "Earlier coding and reports stay with the original version.",
                    )}
                  </p>
                </>
              )}
              <div className="source-update-actions">
                {comparison.same_content ? (
                  <Button
                    disabled={locked}
                    onClick={() => void apply("restore")}
                  >
                    {comparison.stored_available
                      ? copy("保存核对记录", "Save verification")
                      : copy("恢复原文件", "Restore original")}
                  </Button>
                ) : (
                  <>
                    <Button
                      disabled={locked}
                      onClick={() => void apply("version")}
                    >
                      {copy("采用新版", "Use new version")}
                    </Button>
                    <Button
                      variant="outline"
                      disabled={locked}
                      onClick={() => void apply("separate")}
                    >
                      {copy("独立导入", "Import separately")}
                    </Button>
                  </>
                )}
                <Button
                  variant="ghost"
                  disabled={locked}
                  onClick={() => {
                    setComparison(null);
                    setFile(null);
                    setOpen(false);
                  }}
                >
                  {copy("保留当前版本", "Keep current version")}
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
