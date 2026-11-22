import QualityClient from "./quality-client";

interface PageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectQualityPage({ params }: PageProps) {
  const { projectId } = await params;
  return <QualityClient projectId={projectId} />;
}
