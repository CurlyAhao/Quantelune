"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { preparationApi, type PreparationRequest } from "./preparation-api";
import { registerProjectEditor } from "./project-save";
import { navigateToAppRoute } from "./desktop-navigation";

export function usePreparationDraft(
  project: string,
  dataset: string,
  restore: (value: PreparationRequest) => void,
) {
  const router = useRouter();
  const restoreRef = useRef(restore);
  restoreRef.current = restore;
  const latest = useRef<PreparationRequest | null>(null);
  const revision = useRef(0);
  const savedText = useRef("null");
  const dirty = useRef(false);
  const loaded = useRef(false);
  const serial = useRef<Promise<void>>(Promise.resolve());
  const loading = useRef<Promise<void>>(Promise.resolve());
  const [ready, setReady] = useState(false);
  const [hasDraft, setHasDraft] = useState(false);
  const [status, setStatus] = useState<"saved" | "unsaved" | "failed">("saved");
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    loading.current = preparationApi
      .draft(dataset, abort.signal)
      .then((value) => {
        if (abort.signal.aborted) return;
        revision.current = value.revision;
        latest.current = value.request;
        savedText.current = JSON.stringify(value.request);
        if (value.request) restoreRef.current(value.request);
        loaded.current = true;
        setReady(true);
        setHasDraft(!!value.request);
      })
      .catch((reason) => {
        if (!abort.signal.aborted) {
          setStatus("failed");
          setError(
            reason instanceof Error ? reason.message : "Draft unavailable",
          );
        }
      });
    return () => abort.abort();
  }, [dataset]);
  const flush = useCallback(async () => {
    await loading.current;
    if (!loaded.current)
      throw new Error("整理草稿尚未读取，请重新打开后继续。");
    serial.current = serial.current
      .catch(() => undefined)
      .then(async () => {
        const request = latest.current;
        const text = JSON.stringify(request);
        if (text === savedText.current) {
          dirty.current = false;
          return;
        }
        try {
          const result = await preparationApi.saveDraft(
            dataset,
            revision.current,
            request,
          );
          revision.current = result.revision;
          savedText.current = text;
          dirty.current = JSON.stringify(latest.current) !== text;
          setStatus(dirty.current ? "unsaved" : "saved");
          setError("");
        } catch (reason) {
          setStatus("failed");
          setError(
            reason instanceof Error ? reason.message : "Draft not saved",
          );
          throw reason;
        }
      });
    await serial.current;
  }, [dataset]);
  const change = useCallback((value: PreparationRequest | null) => {
    latest.current = value;
    dirty.current = JSON.stringify(value) !== savedText.current;
    setHasDraft(!!value);
    setStatus(dirty.current ? "unsaved" : "saved");
    setTick((value) => value + 1);
  }, []);
  useEffect(() => {
    if (!ready || !tick) return;
    const timer = setTimeout(() => {
      void flush().catch(() => undefined);
    }, 400);
    return () => clearTimeout(timer);
  }, [ready, tick, flush]);
  useEffect(() => registerProjectEditor(project, flush), [project, flush]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const leave = (event: MouseEvent) => {
      if (
        !dirty.current ||
        event.defaultPrevented ||
        event.button !== 0 ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.shiftKey
      )
        return;
      const link =
        event.target instanceof Element
          ? event.target.closest("a[href]")
          : null;
      if (!(link instanceof HTMLAnchorElement) || link.target || link.download)
        return;
      const target = new URL(link.href);
      if (target.origin !== location.origin || target.href === location.href)
        return;
      event.preventDefault();
      event.stopPropagation();
      void flush()
        .then(() =>
          navigateToAppRoute(
            router,
            target.pathname + target.search + target.hash,
          ),
        )
        .catch(() => undefined);
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", leave, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", leave, true);
    };
  }, [flush, router]);
  return { ready, hasDraft, status, error, change, flush };
}
