"use client";

import { Message as UiMessage } from "@/components/message";
import { useLanguage } from "@/providers/language-provider";
import { useEffect, useRef, useState } from "react";
import {
  subscribeNativeWriteStart,
  type SaveOutcome,
} from "@/lib/api-download";

/** All binary download entries share cancellation and honest failure feedback. */
export function DownloadButton({
  identity,
  download,
  children,
  className,
  ariaLabel,
  limitMiB: _limitMiB,
  handoffMessage,
  errorMessage,
  disabled = false,
}: {
  identity: string;
  download: (signal: AbortSignal) => Promise<void | SaveOutcome>;
  children: React.ReactNode;
  className?: string;
  ariaLabel?: string;
  limitMiB?: number;
  handoffMessage?: string;
  errorMessage?: (error: unknown) => string | null;
  disabled?: boolean;
}) {
  const { t, locale } = useLanguage();
  const controller = useRef<AbortController | null>(null);
  const writing = useRef(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [handedOff, setHandedOff] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setBusy(false);
    writing.current = false;
    setSaving(false);
    setError(false);
    setErrorDetail(null);
    setHandedOff(false);
    setSaved(false);
    return () => {
      controller.current?.abort();
      controller.current = null;
    };
  }, [identity]);
  async function start() {
    if (disabled) return;
    if (controller.current) {
      if (writing.current) return;
      controller.current.abort();
      controller.current = null;
      setBusy(false);
      return;
    }
    const pending = new AbortController();
    controller.current = pending;
    writing.current = false;
    setSaving(false);
    const unsubscribe = subscribeNativeWriteStart(pending.signal, () => {
      if (controller.current === pending) {
        writing.current = true;
        setSaving(true);
      }
    });
    setBusy(true);
    setError(false);
    setErrorDetail(null);
    setHandedOff(false);
    setSaved(false);
    try {
      const outcome = await download(pending.signal);
      if (controller.current === pending && !pending.signal.aborted) {
        setSaved(outcome === "saved");
        setHandedOff(outcome !== "saved");
      }
    } catch (error) {
      if (
        controller.current === pending &&
        !pending.signal.aborted &&
        !(error instanceof DOMException && error.name === "AbortError")
      ) {
        setError(true);
        setErrorDetail(errorMessage?.(error) ?? null);
      }
    } finally {
      unsubscribe();
      if (controller.current === pending) {
        controller.current = null;
        writing.current = false;
        setSaving(false);
        setBusy(false);
      }
    }
  }
  return (
    <>
      <button
        type="button"
        aria-label={
          saving
            ? t("download.saving")
            : busy
              ? t("download.cancel")
              : ariaLabel
        }
        disabled={saving || disabled}
        className={className}
        onClick={() => void start()}
      >
        {saving ? (
          <UiMessage id="download.saving" />
        ) : busy ? (
          <UiMessage id="ui.displayBranch.download.cancel" />
        ) : (
          children
        )}
      </button>
      {handedOff && handoffMessage && <p role="status">{handoffMessage}</p>}
      {saved && (
        <p role="status">
          <UiMessage id="ui.common.theFileWasWrittenToTheSelectedLocationOpenItIndependentlyToVerifyItsContents" />
        </p>
      )}
      {error && errorDetail && <p role="alert">{errorDetail}</p>}
      {error && !errorDetail && (
        <p role="alert">
          {locale === "zh-CN"
            ? "无法下载，请检查文件访问权限、来源和磁盘空间后重试。"
            : "Download failed. Check access, source and disk space, then retry."}
        </p>
      )}
    </>
  );
}
