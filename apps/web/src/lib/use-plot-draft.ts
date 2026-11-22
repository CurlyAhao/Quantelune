"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getActorEpoch } from "./auth-session";
import { registerProjectEditor } from "./project-save";
import { readWorkspacePreferences } from "./workspace-preferences";
import { useRouter } from "next/navigation";
import { navigateToAppRoute } from "./desktop-navigation";
import {
  workspaceApi,
  type PlotDraft,
  type WorkspaceRecord,
  type WorkspaceSummary,
} from "./workspace-api";

type Record = WorkspaceRecord<PlotDraft>;
const blank = (projectId: string): PlotDraft => ({
  kind: "plot",
  schema_version: 1,
  title: "新图形",
  scope: { project_id: projectId, columns: [] },
  code: "",
  dpi: readWorkspacePreferences().plotDpi,
});
export function usePlotDraft(projectId: string, initialId?: string | null) {
  const client = useQueryClient();
  const router = useRouter();
  const actor = useRef(getActorEpoch()).current;
  const [value, setValue] = useState(() => blank(projectId));
  const [saved, setSaved] = useState<Record | null>(null);
  const [choices, setChoices] = useState<WorkspaceSummary[]>([]);
  const [ready, setReady] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const current = useRef(value);
  const prior = useRef<Record | undefined>(undefined);
  const loaded = useRef(false);
  const changed = useRef(false);
  const alive = useRef(true);
  const writes = useRef<Promise<Record | undefined>>(
    Promise.resolve(undefined),
  );
  const update = useCallback(
    (next: PlotDraft) => {
      if (!loaded.current || getActorEpoch() !== actor) return;
      current.current = next;
      changed.current = true;
      setValue(next);
      setDirty(true);
    },
    [actor],
  );
  const flush = useCallback(() => {
    const pending = writes.current
      .catch(() => undefined)
      .then(async () => {
        if (!loaded.current || getActorEpoch() !== actor)
          throw new Error("Plot is not available");
        while (changed.current) {
          const snapshot = current.current;
          const record = (await workspaceApi.save(
            projectId,
            snapshot,
            prior.current,
          )) as Record;
          prior.current = record;
          if (snapshot === current.current) changed.current = false;
          if (getActorEpoch() !== actor)
            throw new Error("Plot identity changed");
          if (alive.current) {
            setSaved(record);
            setChoices((old) => [
              {
                id: record.id,
                revision: record.revision,
                kind: "plot",
                available: true,
                title: record.payload.title,
              },
              ...old.filter((item) => item.id !== record.id),
            ]);
          }
        }
        if (alive.current) {
          setDirty(false);
          setError(null);
        }
        void client.invalidateQueries({ queryKey: ["workspace-objects"] });
        void client.invalidateQueries({
          queryKey: ["project-history", projectId],
        });
        return prior.current;
      });
    writes.current = pending;
    void pending.catch((failure) => {
      if (alive.current) setError(failure);
    });
    return pending;
  }, [projectId, actor, client]);
  useEffect(() => {
    const controller = new AbortController();
    alive.current = true;
    void (async () => {
      const list = await workspaceApi.list(
        projectId,
        "plot",
        controller.signal,
      );
      // An explicit empty ID opens a new plot, never another task's latest draft.
      const id = initialId ?? list.find((item) => item.available)?.id;
      const record = id ? await workspaceApi.read(id, controller.signal) : null;
      if (controller.signal.aborted || getActorEpoch() !== actor) return;
      if (
        record &&
        (record.payload.kind !== "plot" || record.project_id !== projectId)
      )
        throw new Error("Plot is not available");
      setChoices(list);
      if (record) {
        prior.current = record as Record;
        current.current = record.payload as PlotDraft;
        setValue(current.current);
        setSaved(record as Record);
      } else {
        current.current = blank(projectId);
        setValue(current.current);
      }
      loaded.current = true;
      setReady(true);
    })().catch((failure) => {
      if (!controller.signal.aborted) setError(failure);
    });
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [projectId, initialId, actor]);
  useEffect(
    () =>
      registerProjectEditor(projectId, async () => {
        await flush();
      }),
    [projectId, flush],
  );
  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      void flush().catch(() => undefined);
    }, 700);
    const unload = (event: BeforeUnloadEvent) => {
      if (changed.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const leave = (event: MouseEvent) => {
      const link =
        event.target instanceof Element
          ? event.target.closest("a[href]")
          : null;
      if (
        !changed.current ||
        !link ||
        !(link instanceof HTMLAnchorElement) ||
        link.download ||
        link.target ||
        event.button !== 0 ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const destination = new URL(link.href);
      if (
        destination.origin !== window.location.origin ||
        destination.href === window.location.href
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
      clearTimeout(timer);
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", leave, true);
    };
  }, [dirty, value, flush, router]);
  useEffect(
    () => () => {
      if (changed.current) void flush().catch(() => undefined);
    },
    [flush],
  );
  const choose = async (id: string) => {
    await flush();
    const record = id ? ((await workspaceApi.read(id)) as Record) : undefined;
    if (
      getActorEpoch() !== actor ||
      (record &&
        (record.payload.kind !== "plot" || record.project_id !== projectId))
    )
      throw new Error("Plot is not available");
    prior.current = record;
    current.current = record?.payload ?? blank(projectId);
    changed.current = false;
    setValue(current.current);
    setSaved(record ?? null);
    setDirty(false);
    setError(null);
  };
  return {
    value,
    saved,
    choices,
    ready,
    dirty,
    error,
    update,
    flush,
    choose,
    peek: () => current.current,
  };
}
