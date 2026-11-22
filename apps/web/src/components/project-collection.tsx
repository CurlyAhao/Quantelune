"use client";
import { useSearchParams } from "next/navigation";
import { AuthGuard } from "./auth-guard";
import { useResultProjectId } from "./result-source";
import { ProjectResults } from "./project-results";
import { ProjectHistory } from "./project-history";
import { useAuthStore } from "@/stores/auth-store";
export function ProjectCollection({
  projectId,
  mode,
}: {
  projectId: string;
  mode: "results" | "history";
}) {
  const resolved = useResultProjectId(projectId);
  const params = useSearchParams();
  const actor = useAuthStore((state) => state.actorEpoch);
  return resolved ? (
    <AuthGuard>
      {mode === "results" ? (
        <ProjectResults
          key={`${actor}:${resolved}:${params.get("reportId") ?? ""}`}
          projectId={resolved}
          initialId={params.get("reportId")}
        />
      ) : (
        <ProjectHistory key={`${actor}:${resolved}`} projectId={resolved} />
      )}
    </AuthGuard>
  ) : null;
}
