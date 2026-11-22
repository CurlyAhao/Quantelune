"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./api-client";
import { getActorEpoch } from "./auth-session";
import { mlColumns, type PreviewContext } from "./preview-types";
import { navigateToAppRoute } from "./desktop-navigation";
import { registerProjectEditor } from "./project-save";
import {
  workspaceApi,
  type AnalysisDraft,
  type WorkspaceRecord,
} from "./workspace-api";
import type { MLEditorState, MLExperimentCreateRequest } from "./types";

type DraftRecord = WorkspaceRecord<AnalysisDraft>;
const pendingSaves = new Map<string, Promise<DraftRecord | undefined>>();
const configText = (value: unknown) =>
  JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.keys(item)
            .sort()
            .map((key) => [key, item[key]]),
        )
      : item,
  );
export function useMLDraft(
  project: string,
  dataset: string,
  sha: string,
  initialId?: string | null,
  availableColumns: string[] = [],
  fresh = false,
) {
  const router = useRouter();
  const client = useQueryClient();
  const actor = useRef(getActorEpoch()).current;
  const [ready, setReady] = useState(false);
  const [hasSaved, setHasSaved] = useState(false);
  const [initialEditor, setInitialEditor] = useState<MLEditorState | null>(
    null,
  );
  const [initial, setInitial] = useState<{
    id: number;
    config: MLExperimentCreateRequest;
  } | null>(null);
  const [status, setStatus] = useState<"saved" | "unsaved" | "failed">("saved");
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const prior = useRef<DraftRecord | undefined>(undefined);
  const current = useRef<{
    config: MLExperimentCreateRequest;
    complete: boolean;
    editor: MLEditorState;
  } | null>(null);
  const savedText = useRef("");
  const baseline = useRef("");
  const dirty = useRef(false);
  const loaded = useRef(false);
  const columnsRef = useRef(availableColumns);
  columnsRef.current = availableColumns;
  const saveKey = `${actor}:${project}:${dataset}`;
  const alive = useRef(true);
  const serial = useRef<Promise<DraftRecord | undefined>>(
    Promise.resolve(undefined),
  );
  useEffect(() => {
    const abort = new AbortController();
    alive.current = true;
    void (async () => {
      await pendingSaves.get(saveKey);
      const record = initialId
        ? await workspaceApi.read(initialId, abort.signal)
        : fresh
          ? null
          : (
              await apiFetch<{ record: DraftRecord | null }>(
                `/api/v1/datasets/${dataset}/ml/draft`,
                { signal: abort.signal },
              )
            ).record;
      if (record) {
        if (
          record.project_id !== project ||
          record.payload.kind !== "analysis" ||
          (!record.payload.ml && !record.payload.ml_editor) ||
          record.payload.scope.dataset_id !== dataset ||
          record.payload.scope.dataset_sha256 !== sha
        )
          throw new Error("模型草稿与当前数据版本不符，请从项目历史重新打开。");
        if (abort.signal.aborted || getActorEpoch() !== actor) return;
        prior.current = record as DraftRecord;
        setHasSaved(true);
        savedText.current = configText({
          config: record.payload.ml ?? null,
          editor: record.payload.ml_editor ?? null,
        });
        setInitial(
          record.payload.ml ? { id: 0, config: record.payload.ml } : null,
        );
        setInitialEditor(record.payload.ml_editor ?? null);
        if (!initialId) {
          const url = new URL(window.location.href);
          if (
            url.pathname.endsWith("/machine-learning") &&
            url.searchParams.get("datasetId") === dataset
          ) {
            url.searchParams.set("draftId", record.id);
            window.history.replaceState(null, "", url.href);
          }
        }
      }
      if (!abort.signal.aborted && getActorEpoch() === actor) {
        loaded.current = true;
        setReady(true);
      }
    })().catch((reason) => {
      if (!abort.signal.aborted) {
        setError(reason);
        setStatus("failed");
      }
    });
    return () => {
      alive.current = false;
      abort.abort();
    };
  }, [actor, initialId, dataset, project, sha, fresh, saveKey]);
  const change = useCallback(
    (
      config: MLExperimentCreateRequest,
      complete: boolean,
      editor: MLEditorState,
    ) => {
      if (!loaded.current || getActorEpoch() !== actor) return;
      const text = configText({ config: complete ? config : null, editor });
      if (!baseline.current) baseline.current = text;
      current.current = { config, complete, editor };
      dirty.current = prior.current
        ? text !== savedText.current
        : text !== baseline.current || complete;
      setStatus(dirty.current ? "unsaved" : "saved");
      setTick((value) => value + 1);
    },
    [actor],
  );
  const flush = useCallback(() => {
    const task = serial.current
      .catch(() => undefined)
      .then(async () => {
        if (!loaded.current || getActorEpoch() !== actor)
          throw new Error("模型草稿尚未读取，请重新打开。");
        while (dirty.current) {
          const snapshot = current.current;
          if (!snapshot) throw new Error("草稿尚未读取，请重新打开。");
          const names = new Set(columnsRef.current);
          const selected = mlColumns(snapshot.config).filter(
            (name) => name && names.has(name),
          );
          const scopedColumns = selected.length
            ? selected.slice(0, 50)
            : columnsRef.current.slice(0, 1);
          const context = await apiFetch<PreviewContext>(
            "/api/v1/preview/context",
            {
              method: "POST",
              body: JSON.stringify({
                project_id: project,
                dataset_id: dataset,
                dataset_sha256: sha,
                columns: scopedColumns,
              }),
            },
          );
          if (getActorEpoch() !== actor)
            throw new Error("当前项目身份已变化。");
          const payload: AnalysisDraft = {
            ...(prior.current?.payload ?? {
              kind: "analysis",
              schema_version: 1,
              title: (snapshot.config.name || "模型实验").slice(0, 160),
              research_question: "",
              observation_unit: "",
              measurement_scales: {},
            }),
            scope: context.scope,
            statistics: null,
            ml: snapshot.complete ? snapshot.config : null,
            ml_editor: snapshot.editor,
          };
          const saved = (await workspaceApi.save(
            project,
            payload,
            prior.current,
          )) as DraftRecord;
          prior.current = saved;
          savedText.current = configText({
            config: snapshot.complete ? snapshot.config : null,
            editor: snapshot.editor,
          });
          dirty.current =
            !!current.current &&
            configText({
              config: current.current.complete ? current.current.config : null,
              editor: current.current.editor,
            }) !== savedText.current;
          if (getActorEpoch() !== actor)
            throw new Error("当前项目身份已变化。");
          if (alive.current) {
            const url = new URL(window.location.href);
            if (
              url.pathname.endsWith("/machine-learning") &&
              url.searchParams.get("datasetId") === dataset &&
              !url.searchParams.get("draftId")
            ) {
              url.searchParams.set("draftId", saved.id);
              window.history.replaceState(null, "", url.href);
            }
          }
        }
        if (alive.current) {
          setHasSaved(!!prior.current);
          setStatus("saved");
          setError(null);
        }
        void client.invalidateQueries({ queryKey: ["workspace-objects"] });
        void client.invalidateQueries({
          queryKey: ["project-history", project],
        });
        return prior.current;
      });
    serial.current = task;
    pendingSaves.set(saveKey, task);
    void task
      .finally(() => {
        if (pendingSaves.get(saveKey) === task) pendingSaves.delete(saveKey);
      })
      .catch(() => undefined);
    void task.catch((reason) => {
      if (alive.current) {
        setStatus("failed");
        setError(reason);
      }
    });
    return task;
  }, [actor, client, dataset, project, sha, saveKey]);
  useEffect(() => {
    if (!ready || !tick || !dirty.current) return;
    const timer = setTimeout(() => void flush().catch(() => undefined), 700);
    return () => clearTimeout(timer);
  }, [ready, tick, flush]);
  useEffect(
    () =>
      registerProjectEditor(project, async () => {
        await flush();
      }),
    [project, flush],
  );
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
        event.shiftKey ||
        event.altKey
      )
        return;
      const link =
        event.target instanceof Element
          ? event.target.closest("a[href]")
          : null;
      if (!(link instanceof HTMLAnchorElement) || link.target || link.download)
        return;
      const destination = new URL(link.href);
      if (
        destination.origin !== location.origin ||
        destination.href === location.href
      )
        return;
      event.preventDefault();
      event.stopPropagation();
      void flush()
        .then(() =>
          navigateToAppRoute(
            router,
            destination.pathname + destination.search + destination.hash,
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
  useEffect(
    () => () => {
      if (dirty.current) void flush().catch(() => undefined);
    },
    [flush],
  );
  return {
    ready,
    hasSaved,
    initial,
    initialEditor,
    status,
    error,
    change,
    flush,
  };
}
