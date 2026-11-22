import { Suspense } from "react";
import { WorkspaceRouteBridge } from "@/components/workspace-route-bridge";

interface ProjectDataPageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectDataPage({
  params,
}: ProjectDataPageProps) {
  const { projectId } = await params;
  return <Suspense fallback={<p>正在打开…</p>}><WorkspaceRouteBridge projectId={projectId} section="data" /></Suspense>;
}
