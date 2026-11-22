import { Suspense } from "react";
import { WorkspaceRouteBridge } from "@/components/workspace-route-bridge";

export default async function RunsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return (
    <Suspense fallback={<p>正在打开…</p>}>
      <WorkspaceRouteBridge projectId={projectId} section="runs" />
    </Suspense>
  );
}
