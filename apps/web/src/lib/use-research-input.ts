"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "./api-client";
import { getActorEpoch } from "./auth-session";
import { registerProjectEditor } from "./project-save";
import {
  workspaceApi,
  type ResearchInputState,
  type WorkspaceRecord,
} from "./workspace-api";

type InputRecord = WorkspaceRecord<ResearchInputState>;
const blank = (datasetId?: string, contextKey = "project"): ResearchInputState => ({
  kind: "research_input",
  context_key: contextKey,
  schema_version: 1,
  title: "研究思路",
  question: "",
  workspace_id: null,
  session_id: null,
  dataset_id: datasetId || null,
  columns: {},
});

/** Local drafts use the existing CAS/versioned project store, never browser storage. */
export function useResearchInput(projectId: string, initialDatasetId?: string, contextKey = "project") {
  const [value, setValue] = useState(() => blank(initialDatasetId, contextKey));
  const [ready, setReady] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const current = useRef(value);
  const record = useRef<InputRecord | undefined>(undefined);
  const records = useRef<InputRecord[]>([]);
  const changed = useRef(false);
  const loaded = useRef(false);
  const alive = useRef(true);
  const writes = useRef<Promise<InputRecord | undefined>>(
    Promise.resolve(undefined),
  );
  const actor = useRef(getActorEpoch()).current;
  const canWrite = useCallback(() => getActorEpoch() === actor, [actor]);
  const apply = useCallback((next: ResearchInputState) => {
    current.current = next;
    setValue(next);
  }, []);
  const accept = useCallback(
    (saved: InputRecord) => {
      record.current = saved;
      records.current = [
        saved,
        ...records.current.filter((item) => item.id !== saved.id),
      ];
      changed.current = false;
      apply(saved.payload);
      setDirty(false);
      setError(null);
    },
    [apply],
  );
  useEffect(() => {
    const controller = new AbortController();
    alive.current = true;
    void apiFetch<{
      records: Array<InputRecord | { id: string; state: string }>;
    }>(
      `/api/v1/workspace/records?project_id=${projectId}&kind=research_input`,
      { signal: controller.signal },
    )
      .then((result) => {
        if (controller.signal.aborted || !canWrite()) return;
        records.current = result.records.filter(
          (item): item is InputRecord =>
            "payload" in item && item.payload.kind === "research_input" &&
            (item.payload.context_key ?? "project") === contextKey,
        );
        if (records.current[0]) accept(records.current[0]);
        loaded.current = true;
        setReady(true);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(failure);
      });
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [projectId, contextKey, canWrite, accept]);
  const flush = useCallback(() => {
    const pending = writes.current
      .catch(() => undefined)
      .then(async () => {
        if (!loaded.current || !canWrite())
          throw new Error("Research input is not available");
        while (changed.current) {
          const snapshot = current.current;
          const saved = (await workspaceApi.save(
            projectId,
            snapshot,
            record.current,
          )) as InputRecord;
          record.current = saved;
          records.current = [
            saved,
            ...records.current.filter((item) => item.id !== saved.id),
          ];
          if (current.current === snapshot) changed.current = false;
          if (!canWrite()) throw new Error("Research identity changed");
        }
        if (alive.current) {
          setDirty(false);
          setError(null);
        }
        return record.current;
      });
    writes.current = pending;
    void pending.catch((failure) => {
      if (alive.current) setError(failure);
    });
    return pending;
  }, [projectId, canWrite]);
  const update = useCallback(
    (patch: Partial<ResearchInputState>) => {
      if (!loaded.current || !canWrite()) return;
      const next = { ...current.current, ...patch };
      next.title = next.question.trim().slice(0, 80) || "研究思路";
      apply(next);
      changed.current = true;
      setDirty(true);
    },
    [apply, canWrite],
  );
  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      void flush().catch(() => undefined);
    }, 600);
    const unload = (event: BeforeUnloadEvent) => {
      if (changed.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", unload);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("beforeunload", unload);
    };
  }, [dirty, value, flush]);
  useEffect(
    () =>
      registerProjectEditor(projectId, async () => {
        await flush();
      }),
    [projectId, flush],
  );
  useEffect(
    () => () => {
      if (changed.current) void flush().catch(() => undefined);
    },
    [flush],
  );
  const choose = async (
    workspaceId: string | null,
    sessionId: string | null,
  ) => {
    await flush();
    const existing = sessionId
      ? records.current.find((item) => item.payload.session_id === sessionId)
      : undefined;
    if (existing) accept(existing);
    else {
      record.current = undefined;
      changed.current = false;
      apply({
        ...blank(initialDatasetId, contextKey),
        workspace_id: workspaceId,
        session_id: sessionId,
      });
      setDirty(false);
      setError(null);
    }
    changed.current = true;
    setDirty(true);
    await flush();
  };
  const chooseDraft = async (id: string) => {
    await flush();
    const existing = records.current.find(
      (item) => item.id === id && !item.payload.session_id,
    );
    if (!existing) throw new Error("Research draft is not available");
    accept(existing);
    changed.current = true;
    setDirty(true);
    await flush();
  };
  return {
    value,
    ready,
    dirty,
    error,
    update,
    flush,
    accept,
    choose,
    chooseDraft,
    recordId: record.current?.id,
    sessionIds: records.current.flatMap(item => item.payload.session_id ? [item.payload.session_id] : []),
    drafts: records.current.filter((item) => !item.payload.session_id),
  };
}

export type ResearchInputController = ReturnType<typeof useResearchInput>;
