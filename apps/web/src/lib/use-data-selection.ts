"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  workspaceApi,
  type DataWorkspaceState,
  type WorkspaceRecord,
} from "./workspace-api";
import { registerProjectEditor } from "./project-save";

export function useDataSelection(projectId: string) {
  const [selected, setSelected] = useState("");
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const saved = useRef<WorkspaceRecord<DataWorkspaceState> | null>(null);
  const writes = useRef<Promise<void>>(Promise.resolve());
  const loaded = useRef(false);
  const writeFailed = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const records = await workspaceApi.list(
        projectId,
        "data_workspace",
        controller.signal,
      );
      const latest = records.find((item) => item.available);
      const record = latest
        ? await workspaceApi.read(latest.id, controller.signal)
        : null;
      if (controller.signal.aborted) return;
      if (record?.payload.kind === "data_workspace") {
        saved.current = record as WorkspaceRecord<DataWorkspaceState>;
        setSelected(record.payload.dataset_id);
      }
      loaded.current = true;
      setReady(true);
    })().catch(() => {
      if (!controller.signal.aborted) {
        writeFailed.current = true;
        setFailed(true);
      }
    });
    return () => controller.abort();
  }, [projectId]);
  const persist = useCallback(
    (datasetId: string) => {
      if (!loaded.current || !datasetId) return;
      writes.current = writes.current
        .catch(() => undefined)
        .then(async () => {
          if (saved.current?.payload.dataset_id === datasetId) return;
          saved.current = (await workspaceApi.save(
            projectId,
            {
              kind: "data_workspace",
              schema_version: 1,
              title: "数据工作台",
              dataset_id: datasetId,
            },
            saved.current ?? undefined,
          )) as WorkspaceRecord<DataWorkspaceState>;
          writeFailed.current = false;
          setFailed(false);
        })
        .catch(() => {
          writeFailed.current = true;
          setFailed(true);
        });
    },
    [projectId],
  );
  useEffect(
    () =>
      registerProjectEditor(projectId, async () => {
        await writes.current;
        if (!loaded.current || writeFailed.current)
          throw new Error("Data selection could not be saved");
      }),
    [projectId],
  );
  return { selected, ready, persist, failed };
}
