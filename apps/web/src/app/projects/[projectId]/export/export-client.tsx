"use client";

import { FriendlyErrorMessage as UiError } from "@/components/friendly-error";
import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  FolderOutputIcon,
  GitBranchIcon,
  LockIcon,
  PackageIcon,
} from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { ErrorState } from "@/components/error-state";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import { friendlyApiError, versionStatusLabel } from "@/lib/format";

interface ExportClientProps {
  projectId: string;
}

export default function ExportClient({ projectId }: ExportClientProps) {
  return (
    <UiAttributes messages={{ label: { id: "common.loading" } }}>
      <AuthGuard label={"Loading…"}>
        <ProjectShell projectId={projectId}>
          <ExportBody projectId={projectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

function ExportBody({ projectId }: ExportClientProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const [forkTitle, setForkTitle] = useState("");
  const [msg, setMsg] = useState<{
    text: React.ReactNode;
    kind: "ok" | "err";
  } | null>(null);

  const snapshotQuery = useQuery({
    queryKey: ["snapshot", projectId],
    queryFn: ({ signal }) =>
      apiFetch<{ status: string; title: string; frozen_at: string | null }>(
        `/api/v1/projects/${projectId}/snapshot`,
        { signal },
      ),
  });

  const freezeM = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/projects/${projectId}/freeze`, { method: "POST" }),
    onSuccess: () => {
      snapshotQuery.refetch();
      setMsg({
        text: <UiMessage id="ui.projects.projectFrozen" />,
        kind: "ok",
      });
    },
    onError: (e) =>
      setMsg({ text: <UiError error={e} part="message" />, kind: "err" }),
  });

  const forkM = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/projects/${projectId}/fork`, {
        method: "POST",
        body: JSON.stringify({
          title: forkTitle || uiCopyT("project.defaultForkTitle"),
        }),
      }),
    onSuccess: (data: unknown) => {
      const d = data as { project_id: string };
      setMsg({
        text: (
          <UiMessage
            id="ui.projects.forkedProjectsValue"
            values={{ value0: String(d.project_id) }}
          />
        ),
        kind: "ok",
      });
    },
    onError: (e) =>
      setMsg({ text: <UiError error={e} part="message" />, kind: "err" }),
  });

  if (snapshotQuery.isLoading)
    return (
      <UiAttributes
        messages={{ label: { id: "ui.projects.loadingProjectStatus" } }}
      >
        <PageLoader label={"Loading project status…"} />
      </UiAttributes>
    );
  if (snapshotQuery.isError)
    return (
      <UiAttributes messages={{ title: { id: "ui.projects.failed" } }}>
        <ErrorState
          title={"Failed"}

          message={friendlyApiError(snapshotQuery.error, uiCopyT).message}
          onRetry={() => snapshotQuery.refetch()}
        />
      </UiAttributes>
    );

  const project = snapshotQuery.data as {
    status: string;
    title: string;
    frozen_at: string | null;
  };
  const isFrozen = project?.status === "frozen";
  const busy = freezeM.isPending || forkM.isPending;

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 py-3 text-xs">
          <span className="flex items-center gap-2 font-medium">
            <FolderOutputIcon className="size-4 text-primary" />
            {project?.title}
          </span>
          <StatusBadge
            label={versionStatusLabel(project?.status, uiCopyT)}
            tone={isFrozen ? "teal" : "slate"}
          />
          {isFrozen && project?.frozen_at && (
            <span className="font-mono">
              {" "}
              <UiMessage id="ui.projects.frozen" />{" "}
              {project.frozen_at.slice(0, 10)}
            </span>
          )}
        </CardContent>
      </Card>

      {msg && (
        <div
          role="alert"
          className={
            msg.kind === "err"
              ? "rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
              : "rounded-md border border-teal-300/60 bg-teal-50 px-3 py-2 text-xs text-teal-900"
          }
        >
          {msg.text}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <LockIcon className="size-4" />{" "}
              <UiMessage id="ui.projects.freeze" />{" "}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.projects.lockThisProjectSnapshotAllDataFingerprintsAndMakeItReproducible" />{" "}
            </p>
            <Button
              onClick={() => freezeM.mutate()}
              disabled={busy || isFrozen}
              size="sm"
              variant="outline"
            >
              {busy ? "…" : <UiMessage id="ui.common.freezeProject" />}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <PackageIcon className="size-4" />{" "}
              <UiMessage id="ui.projects.exportZip" />{" "}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.projects.exportIsUnavailableAReproduciblePackageIsNotYetImplemented" />{" "}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <GitBranchIcon className="size-4" />{" "}
              <UiMessage id="ui.projects.fork" />{" "}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.projects.createAnEditableCopyOfThisFrozenProject" />{" "}
            </p>
            <UiAttributes
              messages={{ placeholder: { id: "ui.projects.forkTitle" } }}
            >
              <Input
                placeholder={"Fork title"}
                value={forkTitle}
                onChange={(e) => setForkTitle(e.target.value)}

                disabled={!isFrozen}
                className="h-8 text-xs"
              />
            </UiAttributes>
            <Button
              onClick={() => forkM.mutate()}
              disabled={busy || !isFrozen}
              size="sm"
              variant="outline"
            >
              {" "}
              <UiMessage id="ui.projects.fork" />{" "}
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
