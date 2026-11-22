"use client";

import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useQuery } from "@tanstack/react-query";
import { GitBranchIcon, HistoryIcon, LockIcon } from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import { useLanguage } from "@/providers/language-provider";
import { versionStatusLabel } from "@/lib/format";

interface Props {
  projectId: string;
}

export default function VersionsClient({ projectId }: Props) {
  return (
    <UiAttributes messages={{ label: { id: "common.loading" } }}>
      <AuthGuard label={"Loading…"}>
        <ProjectShell projectId={projectId}>
          <Body projectId={projectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

function Body({ projectId }: Props) {
  const { t, formatDate } = useLanguage();
  const { data, isLoading } = useQuery({
    queryKey: ["snapshot", projectId],
    queryFn: ({ signal }) =>
      apiFetch<{ status: string; title: string; frozen_at: string | null }>(
        `/api/v1/projects/${projectId}/snapshot`,
        { signal },
      ),
  });

  if (isLoading)
    return (
      <UiAttributes messages={{ label: { id: "ui.projects.loadingVersions" } }}>
        <PageLoader label={"Loading versions…"} />
      </UiAttributes>
    );
  const project = data as {
    status: string;
    title: string;
    frozen_at: string | null;
  };
  const isFrozen = project?.status === "frozen";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <GitBranchIcon className="size-4" />
            <UiMessage id="ui.projects.versionStatus" />
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-4 text-xs">
          <span className="font-medium">{project?.title}</span>
          <StatusBadge
            label={versionStatusLabel(project?.status, t)}
            tone={isFrozen ? "teal" : "slate"}
          />
          {isFrozen && project?.frozen_at && (
            <span className="flex items-center gap-1 text-muted-foreground">
              <LockIcon className="size-3" />
              <UiMessage id="ui.projects.frozen.versionsClient" />{" "}
              {formatDate(project.frozen_at)}
            </span>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <HistoryIcon className="size-4" />
            <UiMessage id="ui.projects.versioning" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1 text-xs text-muted-foreground">
            <li>
              <UiMessage id="ui.projects.draftEditableProjectRunsAreAppendOnly" />
            </li>
            <li>
              <UiMessage id="ui.projects.freezeSnapshotFingerprintsArtifactsExportPage" />
            </li>
            <li>
              <UiMessage id="ui.projects.forkEditableCopyOfAFrozenVersion" />
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
