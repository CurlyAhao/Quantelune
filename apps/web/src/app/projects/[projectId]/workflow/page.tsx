import WorkflowClient from "./workflow-client";

interface PageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectWorkflowPage({ params }: PageProps) {
  const { projectId } = await params;
  return <WorkflowClient projectId={projectId} />;
}
