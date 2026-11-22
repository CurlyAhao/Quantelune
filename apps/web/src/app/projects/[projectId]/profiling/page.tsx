import ProfilingClient from "./profiling-client";

interface PageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectProfilingPage({ params }: PageProps) {
  const { projectId } = await params;
  return <ProfilingClient projectId={projectId} />;
}
