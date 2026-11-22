import DatasetDetailClient from "./dataset-client";

interface DatasetDetailPageProps {
  params: Promise<{ projectId: string; datasetId: string }>;
}

export default async function DatasetDetailPage({
  params,
}: DatasetDetailPageProps) {
  const { projectId, datasetId } = await params;
  return <DatasetDetailClient projectId={projectId} datasetId={datasetId} />;
}
