"use client";

import { Message as UiMessage } from "@/components/message";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { Button } from "./ui/button";
import { PreviewEvidence } from "./preview-evidence";
import type { PreviewScope, PreviewTurn } from "@/lib/preview-types";

type Summary = { turn_id: string; status: string; created_at: string };
type SavedTurn = PreviewTurn & {
  scope: PreviewScope;
  authorization: Record<string, unknown>;
  recovery_notice: string | null;
  effects: { status: string }[];
};

export function PreviewHistory({
  projectId,
  identity,
}: {
  projectId: string;
  identity: string;
}) {
  const [rows, setRows] = useState<Summary[]>([]);
  const [saved, setSaved] = useState<SavedTurn | null>(null);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<React.ReactNode>("");
  const active = useRef(identity + projectId);
  active.current = identity + projectId;
  const selection = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    selection.current?.abort();
    setRows([]);
    setSaved(null);
    setError("");
    const start = identity + projectId;
    void apiFetch<{ turns: Summary[] }>(
      `/api/v1/preview/turns?project_id=${encodeURIComponent(projectId)}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!value || !Array.isArray(value.turns))
          throw new Error("Invalid saved list");
        if (!controller.signal.aborted && active.current === start)
          setRows(value.turns);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            <UiMessage id="ui.residual.workspace.savedListUnavailable" />,
          );
      });
    return () => {
      controller.abort();
      selection.current?.abort();
    };
  }, [projectId, identity, reload]);

  async function read(id: string) {
    selection.current?.abort();
    const controller = new AbortController();
    selection.current = controller;
    const start = identity + projectId;
    setSaved(null);
    setError("");
    try {
      const value = await apiFetch<SavedTurn>(`/api/v1/preview/turns/${id}`, {
        signal: controller.signal,
      });
      if (
        !controller.signal.aborted &&
        active.current === start &&
        value.scope.project_id === projectId
      )
        setSaved(value);
    } catch {
      if (!controller.signal.aborted && active.current === start)
        setError(
          <UiMessage id="ui.residual.workspace.savedSourceChangedNoRerun" />,
        );
    }
  }
  return (
    <details className="border-t pt-3">
      <summary>
        <UiMessage id="ui.preview.savedResearchSessionsReadOnly" />
      </summary>
      <p className="my-2 text-xs">
        {" "}
        <UiMessage id="ui.preview.restartingReadsActualSavedRecordsInterruptedOrReviewPendingOperationsAreNotRepeatedAutomaticallyANewRequestIdDoesNotImplySemanticDeduplicationAcrossRequests" />{" "}
      </p>
      <Button variant="outline" onClick={() => setReload((v) => v + 1)}>
        {" "}
        <UiMessage id="ui.preview.refreshSavedList" />{" "}
      </Button>
      {!rows.length && (
        <p>
          <UiMessage id="ui.preview.thereAreNoVisibleSavedSessionsForThisProject" />
        </p>
      )}
      {rows.map((row) => (
        <button
          className="block w-full truncate text-left underline"
          key={row.turn_id}
          onClick={() => void read(row.turn_id)}
        >
          {row.created_at} · {row.status} · {row.turn_id}
        </button>
      ))}
      {error && <p role="alert">{error}</p>}
      {saved && (
        <div className="space-y-2">
          <p>
            {" "}
            <UiMessage id="ui.preview.savedStatus" />
            {saved.status} {saved.error_code}
          </p>
          {saved.recovery_notice && (
            <p>
              {" "}
              <UiMessage id="ui.preview.interruptedNeedsReviewNoAutomaticRerunInspectTheActualRun" />{" "}
              {saved.effects.map((e) => e.status).join("、")}
            </p>
          )}
          <details>
            <summary>
              <UiMessage id="ui.preview.modelCostAndApprovalSnapshotForThisTurnRealProviderUnverified" />
            </summary>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs">
              {JSON.stringify(saved.authorization, null, 2)}
            </pre>
          </details>
          <PreviewEvidence evidence={saved.evidence} scope={saved.scope} />
        </div>
      )}
    </details>
  );
}
