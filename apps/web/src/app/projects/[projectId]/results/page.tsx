import { Suspense } from "react";
import { ProjectCollection } from "@/components/project-collection";

export default async function ResultsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return (
    <Suspense>
      <ProjectCollection projectId={projectId} mode="results" />
    </Suspense>
  );
}
