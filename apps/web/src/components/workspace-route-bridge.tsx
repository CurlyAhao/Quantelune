"use client";

import { useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { useResultProjectId } from "./result-source";

type Section = "data" | "materials" | "statistics" | "machine-learning" | "history" | "runs" | "report" | "export";

/** Keep old project links usable inside the current workspace, including desktop shells. */
export function WorkspaceRouteBridge({ projectId, section }: { projectId: string; section: Section }) {
  const router = useRouter();
  const project = useResultProjectId(projectId);
  const params = useSearchParams();
  const query = params.toString();
  const requested = useRef("");
  useEffect(() => {
    if (!project) return;
    const next = new URLSearchParams(query);
    next.set("projectId", project);
    if (section === "statistics" || section === "machine-learning") {
      next.set("analysis", section === "statistics" ? "statistics" : "ml");
    }
    if (["history", "runs", "report", "export"].includes(section)) next.set("panel", "history");
    // datasetId/materialId, task, runId, kind/sourceId and precise material
    // locations pass through unchanged for the workspace to restore selection.
    const destination = `/projects/${encodeURIComponent(project)}/workspace?${next.toString()}${window.location.hash}`;
    if (requested.current === destination) return;
    requested.current = destination;
    navigateToAppRoute(router, destination, "replace");
  }, [project, query, router, section]);
  return <p role="status">{project ? "正在打开…" : "无法确定项目，请从首页重新打开。"}</p>;
}
