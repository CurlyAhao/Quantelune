"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { projectsApi, datasetsApi } from "@/lib/api-endpoints";
import { researchApi } from "@/lib/research-api";
import { researchId, researchReferences } from "@/lib/research-view";
import type {
  ResearchAttachment,
  ResearchContext,
  ResearchEvent,
  ResearchRun,
  ResearchSession,
  ResearchWorkspace,
} from "@/lib/research-types";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import { UploadDialog } from "./upload-dialog";
import { DesktopLink } from "./desktop-link";
import {
  ResearchActionNotice,
  ResearchFailure,
} from "./formal-research-common";
import { useResearchAction } from "./use-research-action";
import { ResearchResultPicker } from "./formal-research-result-picker";

export type ResearchColumns = Record<string, string[]>;

export function FormalResearchSources({
  workspace,
  session,
  context,
  attachments,
  events,
  columns,
  onColumns,
  disabled,
  changed,
  knownRuns = [],
  initialDatasetId,
}: {
  workspace: ResearchWorkspace;
  session: ResearchSession;
  context: ResearchContext;
  attachments: ResearchAttachment[];
  events: ResearchEvent[];
  columns: ResearchColumns;
  onColumns: (next: ResearchColumns) => void;
  disabled: boolean;
  changed: () => Promise<unknown>;
  knownRuns?: ResearchRun[];
  initialDatasetId?: string;
}) {
  const { t, formatBytes, locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const [projectId, setProjectId] = useState(workspace.project_id ?? "");
  const [datasetId, setDatasetId] = useState(initialDatasetId ?? "");
  const [crossProject, setCrossProject] = useState(false);
  const [runId, setRunId] = useState("");
  const [requestedRun, setRequestedRun] = useState("");
  const [domainKind, setDomainKind] = useState<
    "quality" | "statistics" | "ml" | "workflow"
  >("statistics");
  const [domainRunId, setDomainRunId] = useState("");
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
  const [sourceSession, setSourceSession] = useState(session.id);
  const [runPage, setRunPage] = useState(1);
  const action = useResearchAction(`${session.id}:${context.source_version}`);
  const blocked =
    disabled ||
    action.pending ||
    action.error != null ||
    session.is_archived ||
    session.is_deleted;
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => projectsApi.list(signal),
  });
  const datasets = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
    enabled: Boolean(projectId),
  });
  const currentDataset = datasets.data?.find(
    (item) =>
      item.id === datasetId &&
      item.project_id === projectId &&
      item.upload_status === "ready",
  );
  const artifacts = useQuery({
    queryKey: ["research", workspace.id, "source-artifacts", requestedRun],
    queryFn: ({ signal }) => researchApi.runs.artifacts(requestedRun, signal),
    enabled: Boolean(researchId(requestedRun)),
    retry: false,
  });
  const sourceSessions = useQuery({
    queryKey: ["research", workspace.id, "sessions", true],
    queryFn: ({ signal }) =>
      researchApi.sessions.list(workspace.id, true, signal),
    enabled: sourcePickerOpen,
    retry: false,
  });
  const ownedSession = sourceSessions.data?.some(
    (item) =>
      item.id === sourceSession &&
      item.workspace_id === workspace.id &&
      !item.is_deleted,
  );
  const sourceRunIds = researchReferences(events, sourceSession).runIds;
  const sourceRuns = useQueries({
    queries: sourceRunIds.slice((runPage - 1) * 20, runPage * 20).map((id) => ({
      queryKey: ["research", workspace.id, sourceSession, "run", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        researchApi.runs.get(id, signal),
      enabled: sourcePickerOpen && ownedSession,
      retry: false,
    })),
  });
  const runChoices = new Map<string, ResearchRun>();
  for (const run of [
    ...knownRuns,
    ...sourceRuns.flatMap((item) => (item.data ? [item.data] : [])),
  ])
    if (run.session_id === sourceSession && run.state === "completed")
      runChoices.set(run.id, run);
  const otherProject = (id: string) =>
    workspace.project_id !== null
      ? workspace.project_id !== id
      : [
          ...context.datasets.map((item) => item.project_id),
          ...context.artifacts.map((item) => item.project_id),
        ].some((current) => current !== id);
  function attach(
    kind: "dataset_version" | "artifact",
    id: string,
    project: string,
  ) {
    action.start(
      (signal) =>
        researchApi.sessions.attach(
          session.id,
          {
            target_kind: kind,
            target_id: id,
            allow_cross_project: otherProject(project) && crossProject,
          },
          signal,
        ),
      () => {
        setCrossProject(false);
        void changed().catch(() => undefined);
      },
    );
  }
  return (
    <section className="formal-sources" aria-label={t("research.sources")}>
      <div className="formal-section-heading">
        <h3>{t("research.sources")}</h3>
        <span className="entry-help">
          {context.datasets.length} {copy("份数据", "datasets")}
          {context.artifacts.length > 0
            ? ` · ${context.artifacts.length} ${copy("个结果", "results")}`
            : ""}
        </span>
      </div>
      {context.datasets.length === 0 && context.artifacts.length === 0 && (
        <p>{t("research.noSources")}</p>
      )}
      {context.datasets.map((dataset) => (
        <fieldset
          className="formal-dataset"
          key={`${dataset.dataset_id}:${dataset.sha256}`}
          disabled={blocked}
        >
          <legend>
            <DatasetName id={dataset.dataset_id} />
          </legend>
          <details>
            <summary>{t("research.sourceDetails")}</summary>
            <dl className="formal-metadata">
              <dt>{t("research.project")}</dt>
              <dd>
                <code>{dataset.project_id}</code>
              </dd>
              <dt>SHA256</dt>
              <dd>
                <code>{dataset.sha256}</code>
              </dd>
              <dt>Schema SHA256</dt>
              <dd>
                <code>{dataset.schema_sha256}</code>
              </dd>
            </dl>
          </details>
          <details className="formal-fields">
            <summary>
              {copy("选择变量", "Select variables")} (
              {(columns[dataset.dataset_id] ?? []).length}/
              {dataset.fields.length})
            </summary>
            {dataset.fields.map((field) => {
              const selected = columns[dataset.dataset_id] ?? [];
              const name = field.reference.name;
              return (
                <label key={name}>
                  <input
                    type="checkbox"
                    checked={selected.includes(name)}
                    disabled={
                      blocked ||
                      (!selected.includes(name) && selected.length >= 10)
                    }
                    onChange={(event) =>
                      onColumns({
                        ...columns,
                        [dataset.dataset_id]: event.target.checked
                          ? [...selected, name]
                          : selected.filter((value) => value !== name),
                      })
                    }
                  />
                  <span data-no-translate>{name}</span>
                  <small data-no-translate>{field.inferred_type}</small>
                </label>
              );
            })}
          </details>
          {attachments
            .filter(
              (item) =>
                item.active &&
                item.target_kind === "dataset_version" &&
                item.target_id === dataset.dataset_id,
            )
            .map((item) => (
              <Button
                key={item.id}
                size="sm"
                variant="ghost"
                onClick={() =>
                  action.start(
                    (signal) => researchApi.sessions.detach(item.id, signal),
                    () => {
                      void changed().catch(() => undefined);
                    },
                  )
                }
              >
                {t("research.detach")}
              </Button>
            ))}
        </fieldset>
      ))}
      {context.artifacts.map((artifact) => (
        <div className="formal-source-row" key={artifact.artifact_id}>
          <span>
            {t("research.artifact")} <code>{artifact.artifact_id}</code> ·{" "}
            {formatBytes(artifact.byte_size)}
          </span>
          <details>
            <summary>{t("research.sourceDetails")}</summary>
            <code>{artifact.sha256}</code>
            <p>
              <code>{artifact.run_id}</code>
            </p>
          </details>
          {attachments
            .filter(
              (item) =>
                item.active &&
                item.target_kind === "artifact" &&
                item.target_id === artifact.artifact_id,
            )
            .map((item) => (
              <Button
                disabled={blocked}
                key={item.id}
                size="sm"
                variant="ghost"
                onClick={() =>
                  action.start(
                    (signal) => researchApi.sessions.detach(item.id, signal),
                    () => {
                      void changed().catch(() => undefined);
                    },
                  )
                }
              >
                {t("research.detach")}
              </Button>
            ))}
        </div>
      ))}
      <details
        className="formal-source-picker"
        onToggle={(event) => setSourcePickerOpen(event.currentTarget.open)}
      >
        <summary>{copy("添加数据或结果", "Add data or results")}</summary>
        <div className="formal-inline-form">
          <label>
            {t("research.project")}
            <SearchSelect
              value={projectId}
              disabled={blocked || projects.isPending}
              onChange={(event) => {
                setProjectId(event.target.value);
                setDatasetId("");
                setCrossProject(false);
              }}
            >
              <option value="">{t("research.chooseProject")}</option>
              {projects.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </SearchSelect>
          </label>
          <label>
            {t("research.dataset")}
            <SearchSelect
              value={currentDataset?.id ?? ""}
              disabled={blocked || !projectId || datasets.isPending}
              onChange={(event) => setDatasetId(event.target.value)}
            >
              <option value="">{t("research.chooseDataset")}</option>
              {datasets.data
                ?.filter((item) => item.project_id === projectId)
                .map((item) => (
                  <option
                    key={item.id}
                    value={item.id}
                    disabled={item.upload_status !== "ready"}
                  >
                    {item.original_filename}
                  </option>
                ))}
            </SearchSelect>
          </label>
          {projectId && !blocked && <UploadDialog projectId={projectId} />}
          <Button
            disabled={
              blocked ||
              !currentDataset ||
              context.datasets.length >= 5 ||
              (currentDataset &&
                otherProject(currentDataset.project_id) &&
                !crossProject) ||
              context.datasets.some((item) => item.dataset_id === datasetId)
            }
            onClick={() => {
              if (currentDataset)
                attach(
                  "dataset_version",
                  currentDataset.id,
                  currentDataset.project_id,
                );
            }}
          >
            {t("research.addDataset")}
          </Button>
        </div>
        {projects.isError && (
          <ResearchFailure
            error={projects.error}
            retry={() => {
              void projects.refetch();
            }}
          />
        )}
        {datasets.isError && projectId && (
          <ResearchFailure
            error={datasets.error}
            retry={() => {
              void datasets.refetch();
            }}
          />
        )}
        <label className="formal-checkbox">
          <input
            type="checkbox"
            checked={crossProject}
            disabled={blocked}
            onChange={(event) => setCrossProject(event.target.checked)}
          />
          {t("research.crossProject")}
        </label>
        <div className="formal-inline-form">
          <label>
            {t("research.sourceConversation")}
            <SearchSelect
              value={ownedSession ? sourceSession : ""}
              disabled={blocked || sourceSessions.isPending}
              onChange={(event) => {
                setSourceSession(event.target.value);
                setRunPage(1);
                setRequestedRun("");
                setRunId("");
              }}
            >
              <option value="">{t("research.chooseSession")}</option>
              {sourceSessions.data
                ?.filter(
                  (item) =>
                    !item.is_deleted && item.workspace_id === workspace.id,
                )
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
            </SearchSelect>
          </label>
          <label>
            {t("research.savedResult")}
            <SearchSelect
              value={runChoices.has(requestedRun) ? requestedRun : ""}
              disabled={blocked || !ownedSession}
              onChange={(event) => {
                setRequestedRun(event.target.value);
                setRunId(event.target.value);
              }}
            >
              {<option value="">{t("research.savedResult")}</option>}
              {[...runChoices.values()].map((run) => (
                <option key={run.id} value={run.id}>
                  {run.domain_kind ?? run.kind} · {run.id}
                </option>
              ))}
            </SearchSelect>
          </label>
          <label>
            {t("research.runId")}
            <input
              value={runId}
              disabled={blocked}
              onChange={(event) => setRunId(event.target.value)}
            />
          </label>
          <Button
            variant="outline"
            disabled={blocked || !researchId(runId)}
            onClick={() => setRequestedRun(runId)}
          >
            {t("research.findArtifacts")}
          </Button>
        </div>
        {sourceRunIds.length > 20 && (
          <div className="formal-actions">
            <Button
              variant="ghost"
              disabled={blocked || runPage === 1}
              onClick={() => setRunPage((old) => old - 1)}
            >
              {t("research.previousPage")}
            </Button>
            <span>{t("research.resultPage", { page: runPage })}</span>
            <Button
              variant="ghost"
              disabled={blocked || runPage * 20 >= sourceRunIds.length}
              onClick={() => setRunPage((old) => old + 1)}
            >
              {t("research.nextPage")}
            </Button>
          </div>
        )}
        {sourceSessions.isError && (
          <ResearchFailure
            error={sourceSessions.error}
            retry={() => {
              void sourceSessions.refetch();
            }}
          />
        )}
        {sourceRuns
          .filter((item) => item.isError)
          .map((item, index) => (
            <ResearchFailure
              key={index}
              error={item.error}
              retry={() => {
                void item.refetch();
              }}
            />
          ))}
        {artifacts.isPending && requestedRun && (
          <p role="status">{t("research.loading")}</p>
        )}
        {artifacts.isError && (
          <ResearchFailure
            error={artifacts.error}
            retry={() => {
              void artifacts.refetch();
            }}
          />
        )}
        <ul className="formal-artifact-list">
          {artifacts.data?.map((artifact) => (
            <li key={artifact.id}>
              <span data-no-translate>
                {artifact.kind} · <code>{artifact.id}</code>
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  blocked ||
                  context.artifacts.length >= 5 ||
                  context.artifacts.some(
                    (item) => item.artifact_id === artifact.id,
                  ) ||
                  (otherProject(artifact.project_id) && !crossProject)
                }
                onClick={() =>
                  attach("artifact", artifact.id, artifact.project_id)
                }
              >
                {t("research.addArtifact")}
              </Button>
            </li>
          ))}
        </ul>
        <details>
          <summary>{t("research.adopt")}</summary>
          <p className="entry-help">{t("research.adoptHint")}</p>
          <div className="formal-inline-form">
            <label>
              {t("research.domainKind")}
              <SearchSelect
                disabled={blocked}
                value={domainKind}
                onChange={(event) => {
                  setDomainKind(event.target.value as typeof domainKind);
                  setDomainRunId("");
                }}
              >
                {(["quality", "statistics", "ml", "workflow"] as const).map(
                  (kind) => (
                    <option key={kind} value={kind}>
                      {t(
                        kind === "statistics"
                          ? "research.descriptive"
                          : kind === "quality"
                            ? "research.quality"
                            : kind === "ml"
                              ? "research.ml"
                              : "research.workflow",
                      )}
                    </option>
                  ),
                )}
              </SearchSelect>
            </label>
            <ResearchResultPicker
              key={domainKind}
              kind={domainKind}
              context={context}
              disabled={blocked}
              value={domainRunId}
              onSelect={setDomainRunId}
            />
            <label>
              {t("research.domainRunId")}
              <input
                value={domainRunId}
                disabled={blocked}
                onChange={(event) => setDomainRunId(event.target.value)}
              />
            </label>
            <Button
              disabled={blocked || !researchId(domainRunId)}
              variant="outline"
              onClick={() =>
                action.start(
                  (signal, key) =>
                    researchApi.runs.adoptResult(
                      session.id,
                      {
                        domain_kind: domainKind,
                        domain_run_id: domainRunId,
                        idempotency_key: key,
                      },
                      signal,
                    ),
                  (run) => {
                    setRequestedRun(run.id);
                    setRunId(run.id);
                    void changed().catch(() => undefined);
                  },
                  { retryable: true },
                )
              }
            >
              {t("research.adopt")}
            </Button>
          </div>
        </details>
        <p className="entry-help">{t("research.readerKinds")}</p>
      </details>
      <ResearchActionNotice action={action} reload={changed} />
    </section>
  );
}

function DatasetName({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["dataset", id],
    queryFn: ({ signal }) => datasetsApi.get(id, signal),
  });
  return (
    <>
      {query.data ? (
        <DesktopLink
          href={`/projects/${query.data.project_id}/data/${query.data.id}`}
          data-no-translate
        >
          {query.data.original_filename}
        </DesktopLink>
      ) : (
        <code>{id}</code>
      )}
    </>
  );
}
