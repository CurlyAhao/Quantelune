import AgentClient from "./agent-client";

interface PageProps { params: Promise<{ projectId: string }>; }

export default async function ProjectAgentPage({ params }: PageProps) {
  const { projectId } = await params;
  return <AgentClient projectId={projectId} />;
}
