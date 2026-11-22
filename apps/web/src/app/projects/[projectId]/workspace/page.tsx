import { Message as UiMessage } from "@/components/message";
import { Suspense } from "react";
import { RealWorkspace } from "@/components/real-workspace";

export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return (
    <Suspense
      fallback={
        <p>
          <UiMessage id="ui.workspace.loadingWorkspace" />
        </p>
      }
    >
      <RealWorkspace projectId={projectId} />
    </Suspense>
  );
}
