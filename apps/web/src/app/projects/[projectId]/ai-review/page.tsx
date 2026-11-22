import AIReviewClient from "./ai-review-client";

interface PageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectAIReviewPage({ params }: PageProps) {
  const { projectId } = await params;
  return <AIReviewClient projectId={projectId} />;
}
