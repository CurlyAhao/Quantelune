import VersionsClient from "./versions-client";

interface PageProps { params: Promise<{ projectId: string }>; }

export default async function ProjectVersionsPage({ params }: PageProps) {
  const { projectId } = await params;
  return <VersionsClient projectId={projectId} />;
}
