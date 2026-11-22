import OverviewClient from "./overview-client";

interface ProjectOverviewPageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectOverviewPage({
  params,
}: ProjectOverviewPageProps) {
  const { projectId } = await params;
  return <OverviewClient projectId={projectId} />;
}
