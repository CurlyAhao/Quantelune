"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  workspaceApi,
  type ResearchTask,
  type WorkspaceRecord,
} from "./workspace-api";
import { registerProjectEditor } from "./project-save";
import { getActorEpoch } from "./auth-session";
import { ApiError } from "./api-client";

export type TaskRecord = WorkspaceRecord<ResearchTask>;

const normalized = (value: ResearchTask): ResearchTask => ({
  ...value,
  dataset_ids: value.dataset_ids ?? [],
  plot_run_ids: value.plot_run_ids ?? [],
});
// The API canonicalizes object keys; ordering alone is not a draft edit.
const ordered = (_key: string, value: unknown): unknown =>
  value && typeof value === "object" && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)))
    : value;
export const sameTaskValue = (first: unknown, second: unknown) =>
  JSON.stringify(first, ordered) === JSON.stringify(second, ordered);
const same = sameTaskValue;
const links = ["analysis_ids", "figure_ids", "dataset_ids", "plot_run_ids", "archived_analysis_ids", "archived_figure_ids"] as const;

/** Three-way merge preserves local edits and accepts background additions. */
function mergeTask(base: ResearchTask, local: ResearchTask, remote: ResearchTask): ResearchTask {
  const result = { ...remote };
  for (const key of Object.keys(local) as (keyof ResearchTask)[]) {
    if (!same(local[key], base[key])) Object.assign(result, { [key]: local[key] });
  }
  for (const key of links) {
    const before = new Set(base[key] ?? []);
    const mine = new Set(local[key] ?? []);
    const theirs = new Set(remote[key] ?? []);
    result[key] = [...new Set([...(local[key] ?? []), ...(remote[key] ?? [])])].filter(
      (id) => mine.has(id) !== before.has(id) ? mine.has(id) : theirs.has(id),
    );
  }
  // A background publication must not change the user's adopted result.
  if (local.adopted_id) {
    result.adopted_id = local.adopted_id;
    if (!result.analysis_ids.includes(local.adopted_id)) result.analysis_ids.push(local.adopted_id);
  }
  return normalized(result);
}

/** Serialized CAS saves let a background run append to the latest task draft. */
export function useWorkspaceTask(
  initial: TaskRecord,
  onSaved: (row: TaskRecord) => void,
) {
  const [value, setValue] = useState(() => normalized(initial.payload));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const draft = useRef(value),
    saved = useRef(initial),
    dirty = useRef(false);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const epoch = useRef(getActorEpoch()).current;
  const notify = useRef(onSaved);
  notify.current = onSaved;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const reconcile = useCallback((row: TaskRecord) => {
    if (getActorEpoch() !== epoch || row.id !== initial.id ||
        row.project_id !== initial.project_id || row.payload.kind !== "research_task" ||
        row.revision <= saved.current.revision) return;
    const next = dirty.current
      ? mergeTask(saved.current.payload, draft.current, row.payload)
      : normalized(row.payload);
    saved.current = row;
    draft.current = next;
    dirty.current = !same(next, normalized(row.payload));
    if (mounted.current) {
      setValue(next);
      setSaving(dirty.current);
    }
    notify.current(row);
  }, [epoch, initial.id, initial.project_id]);
  useEffect(() => reconcile(initial), [initial, reconcile]);
  const flush = useCallback(() => {
    const pending = queue.current
      .catch(() => undefined)
      .then(async () => {
        let conflicts = 0;
        while (dirty.current) {
          if (getActorEpoch() !== epoch)
            throw new Error("登录状态已改变，请重新打开项目");
          const snapshot = draft.current;
          if (mounted.current) setSaving(true);
          let row: TaskRecord;
          try {
            row = (await workspaceApi.save(initial.project_id, snapshot, saved.current)) as TaskRecord;
          } catch (reason) {
            if (!(reason instanceof ApiError) || reason.errorCode !== "WORKSPACE_REVISION_CONFLICT" || conflicts++ >= 1) throw reason;
            const latest = await workspaceApi.read(initial.id) as TaskRecord;
            reconcile(latest);
            continue;
          }
          if (getActorEpoch() !== epoch)
            throw new Error("登录状态已改变，请重新打开项目");
          if (saved.current.revision > row.revision) continue;
          const next = draft.current === snapshot
            ? normalized(row.payload)
            : mergeTask(snapshot, draft.current, row.payload);
          saved.current = row;
          draft.current = next;
          dirty.current = !same(next, normalized(row.payload));
          if (mounted.current) setValue(next);
          notify.current(row);
        }
        if (mounted.current) {
          setSaving(false);
          setError("");
        }
        return saved.current;
      });
    queue.current = pending;
    void pending.catch((reason) => {
      if (mounted.current) {
        setSaving(false);
        setError(
          reason instanceof Error ? reason.message : "任务未保存，请重试",
        );
      }
    });
    return pending;
  }, [epoch, initial.id, initial.project_id, reconcile]);
  const refresh = useCallback(() => {
    const pending = queue.current.catch(() => undefined).then(async () => {
      const row = await workspaceApi.read(initial.id) as TaskRecord;
      reconcile(row);
      return row;
    });
    queue.current = pending;
    return pending;
  }, [initial.id, reconcile]);
  const removePlotVersion = useCallback((id: string, group: string, order: string[]) => {
    // Only publish the removal to the reader once the versioned task save succeeds.
    const pending = queue.current.catch(() => undefined).then(async () => {
      for (let attempt = 0; ; attempt++) {
        if (getActorEpoch() !== epoch) throw new Error("请重新打开项目后重试");
        const prior = saved.current;
        const ids = prior.payload.plot_run_ids ?? [];
        if (!ids.includes(id)) return;
        const history = prior.payload.plot_version_order ?? {};
        try {
          const row = await workspaceApi.save(initial.project_id, {
            ...prior.payload,
            plot_run_ids: ids.filter(other => other !== id),
            plot_version_order: { ...history, [group]: [...new Set([...(history[group] ?? []), ...order])] },
          }, prior);
          reconcile(row as TaskRecord);
          return;
        } catch (reason) {
          if (attempt || !(reason instanceof ApiError) || reason.errorCode !== "WORKSPACE_REVISION_CONFLICT") throw reason;
          reconcile(await workspaceApi.read(initial.id) as TaskRecord);
        }
      }
    });
    queue.current = pending;
    return pending;
  }, [epoch, initial.id, initial.project_id, reconcile]);
  const update = useCallback(
    (
      patch: Partial<ResearchTask> | ((prior: ResearchTask) => ResearchTask),
    ) => {
      if (getActorEpoch() !== epoch) return;
      const next =
        typeof patch === "function"
          ? patch(draft.current)
          : { ...draft.current, ...patch };
      const nextDraft = normalized(next);
      if (same(nextDraft, draft.current)) return;
      draft.current = nextDraft;
      dirty.current = true;
      setValue(draft.current);
      setSaving(true);
    },
    [epoch],
  );
  useEffect(() => {
    if (!dirty.current) return;
    const timer = setTimeout(() => void flush().catch(() => undefined), 500);
    return () => clearTimeout(timer);
  }, [value, flush]);
  useEffect(
    () =>
      registerProjectEditor(initial.project_id, async () => {
        await flush();
      }),
    [initial.project_id, flush],
  );
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("beforeunload", unload);
      if (dirty.current) void flush().catch(() => undefined);
    };
  }, [flush]);
  return { value, update, flush, refresh, removePlotVersion, saving, error };
}
