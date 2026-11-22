import DataWorkbench from "@/components/data-import-workbench";

export default async function PreparePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <DataWorkbench projectId={projectId} preparationMode />;
}
