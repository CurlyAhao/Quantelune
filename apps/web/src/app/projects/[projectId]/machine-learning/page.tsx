import { Suspense } from "react";
import { WorkspaceRouteBridge } from "@/components/workspace-route-bridge";

interface PageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectMachineLearningPage({
  params,
}: PageProps) {
  const { projectId } = await params;
  return (
    <Suspense
      fallback={
        <p>
          正在打开…
        </p>
      }
    >
      <WorkspaceRouteBridge projectId={projectId} section="machine-learning" />
    </Suspense>
  );
}
