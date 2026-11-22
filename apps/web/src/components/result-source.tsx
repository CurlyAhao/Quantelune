"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

/** Static desktop shells carry their actual project in the source URL. */
export function useResultProjectId(projectId: string): string | null {
  const params = useSearchParams();
  return projectId === "__desktop_project__"
    ? params.get("projectId") || null
    : projectId;
}

/** A source URL is a new selection, including when the pathname is unchanged.
 * Stable source parameters leave manual pickers and drafts alone.
 */
export function ResultSourceBoundary({
  projectId,
  children,
}: {
  projectId: string;
  children: React.ReactNode;
}) {
  const params = useSearchParams();
  const identity = JSON.stringify([
    projectId,
    params.get("datasetId"),
    params.get("runId"),
  ]);
  return <Fragment key={identity}>{children}</Fragment>;
}

/** Each mount and manual selection gets a fresh query identity. A-to-B-to-A
 * cannot reuse A's pending GET, nor can a retired mutation alter the new page.
 * The ticket only guards UI delivery; it does not cancel committed server work.
 */
export function useResultSelection(
  initialDatasetId: string | null,
  initialRunId: string | null,
) {
  const [instance] = useState(() => crypto.randomUUID());
  const [selection, setSelection] = useState({
    datasetId: initialDatasetId,
    runId: initialRunId,
    revision: 0,
  });
  const revision = useRef(0);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  return {
    ...selection,
    queryIdentity: `${instance}:${selection.revision}`,
    select: (datasetId: string | null, runId: string | null) => {
      revision.current += 1;
      setSelection({ datasetId, runId, revision: revision.current });
    },
    ticket: () => {
      const started = revision.current;
      return () => live.current && revision.current === started;
    },
  };
}
