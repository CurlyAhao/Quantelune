"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getActorEpoch } from "./auth-session";
import { registerProjectEditor } from "./project-save";
import { sameReportSource } from "./report-source-frame";
import {
  workspaceApi,
  type ReportDocument,
  type WorkspaceRecord,
  type WorkspaceSummary,
} from "./workspace-api";

type Record = WorkspaceRecord<ReportDocument>;
const blank = (): ReportDocument => ({
  kind: "report",
  schema_version: 1,
  title: "研究成果",
  subtitle: "",
  blocks: [],
});
export function useReportDocument(
  projectId: string,
  initialId?: string | null,
) {
  const client = useQueryClient();
  const actor = useRef(getActorEpoch()).current;
  const [value, setValue] = useState(blank);
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
    (next: ReportDocument) => {
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
          throw new Error("Report is not available");
        while (changed.current) {
          const snapshot = current.current;
          const record = (await workspaceApi.save(
            projectId,
            snapshot,
            prior.current,
          )) as Record;
          if (getActorEpoch() !== actor)
            throw new Error("Report identity changed");
          prior.current = record;
          if (snapshot === current.current) {
            current.current = record.payload;
            changed.current = false;
          } else {
            const savedBlocks = new Map(
              record.payload.blocks.map((block) => [block.id, block]),
            );
            current.current = {
              ...current.current,
              blocks: current.current.blocks.map((block) => {
                const accepted = savedBlocks.get(block.id);
                return accepted &&
                  sameReportSource(block.source, accepted.source)
                  ? { ...block, source_snapshot: accepted.source_snapshot }
                  : block;
              }),
            };
          }
          if (alive.current) {
            setValue(current.current);
            setSaved(record);
            setChoices((old) => [
              {
                id: record.id,
                revision: record.revision,
                kind: "report",
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
        "report",
        controller.signal,
      );
      const id = initialId || list.find((item) => item.available)?.id;
      const record = id ? await workspaceApi.read(id, controller.signal) : null;
      if (controller.signal.aborted || getActorEpoch() !== actor) return;
      if (
        record &&
        (record.payload.kind !== "report" || record.project_id !== projectId)
      )
        throw new Error("Report is not available");
      setChoices(list);
      if (record) {
        prior.current = record as Record;
        current.current = record.payload as ReportDocument;
        setValue(current.current);
        setSaved(record as Record);
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
    window.addEventListener("beforeunload", unload);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("beforeunload", unload);
    };
  }, [dirty, value, flush]);
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
        (record.payload.kind !== "report" || record.project_id !== projectId))
    )
      throw new Error("Report is not available");
    prior.current = record;
    current.current = record?.payload ?? blank();
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
