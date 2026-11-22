"use client";

import { SearchSelect } from "@/components/ui/search-select";

import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { researchApi } from "@/lib/research-api";
import { researchId, researchReferences } from "@/lib/research-view";
import { currentProtocol } from "@/lib/research-protocol";
import type { ProtocolHistory } from "@/lib/research-protocol-types";
import type {
  ResearchCapabilityId,
  ResearchContext,
  ResearchAnyPlan,
  ResearchSession,
  ResearchWorkspace,
} from "@/lib/research-types";
import type { StatisticalRunRequest } from "@/lib/types";
import { datasetsApi, projectsApi } from "@/lib/api-endpoints";
import { apiFetch } from "@/lib/api-client";
import {
  useResearchInput,
  type ResearchInputController,
} from "@/lib/use-research-input";
import type { ResearchInputState, WorkspaceRecord } from "@/lib/workspace-api";
import { ModelSettingsLink } from "./model-settings-link";
import { useAuthStore } from "@/stores/auth-store";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import { DesktopLink } from "./desktop-link";
import {
  FormalResearchSources,
  type ResearchColumns,
} from "./formal-research-sources";
import { FormalResearchProposal } from "./formal-research-plan";
import { FormalResearchActivity } from "./formal-research-activity";
import { FormalResearchProtocol } from "./formal-research-protocol";
import {
  ResearchActionNotice,
  ResearchFailure,
  ResearchMoney,
} from "./formal-research-common";
import { ResearchProviderPanel } from "./formal-model-status";
import { useResearchAction } from "./use-research-action";
import { useResearchEvents } from "./use-research-events";

type Location = { workspaceId: string; sessionId: string; runId: string };
const emptyLocation: Location = { workspaceId: "", sessionId: "", runId: "" };
const InputContext = createContext<ResearchInputController | null>(null);
type AutoSubmit = { id: string; started: () => void };

export function FormalResearch(props: {
  contextKey?: string;
  initialQuestion?: string;
  projectId?: string;
  initialDatasetId?: string;
  restoreFromUrl?: boolean;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
}) {
  const epoch = useAuthStore((state) => state.actorEpoch);
  if (props.projectId)
    return (
      <ProjectResearch
        key={`${epoch}:${props.projectId}:${props.contextKey ?? "project"}`}
        {...props}
        projectId={props.projectId}
      />
    );
  return (
    <ResearchWorkspaceEntry
      key={`${epoch}:${props.projectId ?? "global"}`}
      {...props}
    />
  );
}

function ProjectResearch({
  contextKey = "project",
  initialQuestion,
  projectId,
  initialDatasetId,
  onApplyStatistics,
  manualSource,
}: {
  contextKey?: string;
  initialQuestion?: string;
  projectId: string;
  initialDatasetId?: string;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const client = useQueryClient();
  const input = useResearchInput(projectId, initialDatasetId, contextKey);
  const seeded = useRef(false);
  useEffect(() => {
    if (!input.ready || seeded.current) return;
    seeded.current = true;
    if (initialQuestion && !input.value.question && !input.value.session_id)
      input.update({ question: initialQuestion.slice(0, 4000) });
  }, [input.ready, initialQuestion, input]);
  const { ready: inputReady, value: inputValue, update: updateInput } = input;
  const [autoSubmit, setAutoSubmit] = useState<string | null>(null);
  const action = useResearchAction(
    `${projectId}:begin:${input.value.session_id ?? "new"}`,
  );
  const workspaces = useQuery({
    queryKey: ["research", "workspaces"],
    queryFn: ({ signal }) => researchApi.workspaces.list(signal),
    retry: false,
  });
  const available =
    workspaces.data?.filter(
      (item) => item.project_id === projectId && !item.is_deleted,
    ) ?? [];
  const workspace = available.find(
    (item) => item.id === input.value.workspace_id,
  );
  const pickerWorkspace = workspace ?? available[0];
  const sessions = useQuery({
    queryKey: ["research", pickerWorkspace?.id, "sessions", true],
    queryFn: ({ signal }) =>
      researchApi.sessions.list(pickerWorkspace!.id, true, signal),
    enabled: Boolean(pickerWorkspace),
    retry: false,
  });
  const session = sessions.data?.find(
    (item) => item.id === input.value.session_id && !item.is_deleted,
  );
  const datasets = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });
  const dataset = datasets.data?.find(
    (item) =>
      item.id === (input.value.dataset_id ?? initialDatasetId) &&
      item.upload_status === "ready",
  );
  const provider = useQuery({
    queryKey: ["research", "provider"],
    queryFn: ({ signal }) => researchApi.security.provider(signal),
    retry: false,
  });
  useEffect(() => {
    if (
      inputReady &&
      initialDatasetId &&
      !inputValue.session_id &&
      initialDatasetId !== inputValue.dataset_id
    )
      updateInput({ dataset_id: initialDatasetId, columns: {} });
  }, [
    inputReady,
    inputValue.session_id,
    inputValue.dataset_id,
    initialDatasetId,
    updateInput,
  ]);
  const refresh = async () => {
    await workspaces.refetch({ throwOnError: true });
    if (pickerWorkspace) await sessions.refetch({ throwOnError: true });
  };
  function begin() {
    if (!input.ready || !input.value.question.trim() || action.pending) return;
    action.start(
      async (signal) => {
        const saved = await input.flush();
        if (!saved) throw new Error("Research input was not saved");
        return apiFetch<{
          input: WorkspaceRecord<ResearchInputState>;
          workspace: ResearchWorkspace;
          session: ResearchSession;
        }>(`/api/v1/research/inputs/${saved.id}/begin`, {
          method: "POST",
          signal,
          body: JSON.stringify({ expected_revision: saved.revision }),
        });
      },
      (value) => {
        client.setQueryData<ResearchWorkspace[]>(
          ["research", "workspaces"],
          (old) => [
            ...(old ?? []).filter((item) => item.id !== value.workspace.id),
            value.workspace,
          ],
        );
        client.setQueryData<ResearchSession[]>(
          ["research", value.workspace.id, "sessions", true],
          (old) => [
            ...(old ?? []).filter((item) => item.id !== value.session.id),
            value.session,
          ],
        );
        input.accept(value.input);
        if (
          value.input.payload.dataset_id &&
          provider.data?.enabled &&
          provider.data.key_configured
        )
          setAutoSubmit(crypto.randomUUID());
      },
    );
  }
  return (
    <InputContext.Provider value={input}>
      <div className="formal-research project-research">
        <div className="research-discussion-bar">
          <label>
            <span className="sr-only">{copy("讨论记录", "Discussions")}</span>
            <SearchSelect
              aria-label={copy("讨论记录", "Discussions")}
              value={
                session?.id ??
                (!input.value.session_id && input.recordId
                  ? `draft:${input.recordId}`
                  : "")
              }
              disabled={!input.ready || action.pending}
              onChange={(event) => {
                setAutoSubmit(null);
                void (
                  event.target.value.startsWith("draft:")
                    ? input.chooseDraft(event.target.value.slice(6))
                    : input.choose(
                        pickerWorkspace?.id ?? null,
                        event.target.value || null,
                      )
                ).catch(() => undefined);
              }}
            >
              <option value="">{copy("新的讨论", "New discussion")}</option>
              {input.drafts.map((draft) => (
                <option key={draft.id} value={`draft:${draft.id}`}>
                  {copy("草稿", "Draft")} ·{" "}
                  {draft.payload.question.trim().slice(0, 80) ||
                    copy("新的讨论", "New discussion")}
                </option>
              ))}
              {sessions.data
                ?.filter((item) => !item.is_deleted && (input.sessionIds.includes(item.id) || item.id === input.value.session_id))
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                    {item.is_archived ? copy(" · 已归档", " · Archived") : ""}
                  </option>
                ))}
            </SearchSelect>
          </label>
          <Button
            variant="ghost"
            size="sm"
            disabled={!input.ready || action.pending}
            onClick={() => {
              if (!input.value.session_id && !input.value.question.trim())
                return;
              setAutoSubmit(null);
              void input
                .choose(pickerWorkspace?.id ?? null, null)
                .catch(() => undefined);
            }}
          >
            {copy("新讨论", "New discussion")}
          </Button>
          <span className="research-draft-state" role="status">
            {input.error
              ? copy("未保存", "Not saved")
              : input.dirty
                ? copy("保存中…", "Saving…")
                : input.ready && input.value.question
                  ? copy("草稿已保存", "Draft saved")
                  : ""}
          </span>
        </div>
        {!input.ready && !input.error && (
          <p role="status">{copy("读取讨论…", "Loading discussion…")}</p>
        )}
        {input.error != null && (
          <ResearchFailure
            error={input.error}
            retry={() => {
              if (input.ready) void input.flush().catch(() => undefined);
              else window.location.reload();
            }}
          />
        )}
        {workspaces.isError && (
          <ResearchFailure
            error={workspaces.error}
            retry={() => {
              void workspaces.refetch();
            }}
          />
        )}
        {input.ready &&
          input.value.session_id &&
          !session &&
          (sessions.isPending || workspaces.isPending ? (
            <p role="status">{copy("读取讨论…", "Loading discussion…")}</p>
          ) : (
            <div role="alert">
              <p>
                {copy(
                  "这段讨论暂时无法打开，草稿仍已保留。",
                  "This discussion is unavailable. Your draft is retained.",
                )}
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  const question = input.value.question;
                  void input
                    .choose(null, null)
                    .then(() => input.update({ question }))
                    .catch(() => undefined);
                }}
              >
                {copy("以草稿新建讨论", "Start a discussion from this draft")}
              </Button>
            </div>
          ))}
        {sessions.isError && (
          <ResearchFailure
            error={sessions.error}
            retry={() => {
              void sessions.refetch();
            }}
          />
        )}
        {input.ready && !input.value.session_id && (
          <>
            <form
              className="entry-question research-start"
              onSubmit={(event) => {
                event.preventDefault();
                begin();
              }}
            >
              <label
                className="entry-question-label"
                htmlFor={`project-question-${projectId}`}
              >
                {copy("你想研究什么？", "What would you like to explore?")}
              </label>
              <textarea
                id={`project-question-${projectId}`}
                aria-label={copy("你的研究问题", "Your research question")}
                placeholder={copy(
                  "描述问题，或说说想从数据中了解什么…",
                  "Describe your question or what you want to learn…",
                )}
                value={input.value.question}
                maxLength={4000}
                disabled={action.pending}
                onChange={(event) =>
                  input.update({ question: event.target.value })
                }
              />
              <div className="entry-question-actions">
                {dataset && (
                  <span className="research-data-label">
                    {dataset.original_filename}
                  </span>
                )}
                {provider.data?.enabled && provider.data.key_configured ? (
                  <Button
                    type="submit"
                    disabled={
                      action.pending ||
                      !input.value.question.trim() ||
                      workspaces.isError
                    }
                  >
                    {copy(
                      dataset ? "生成方案" : "开始讨论",
                      dataset ? "Generate plan" : "Start discussion",
                    )}
                  </Button>
                ) : (
                  <ModelSettingsLink
                    projectId={projectId}
                    className="research-model-action"
                  >
                    {copy("选择模型", "Choose a model")}
                  </ModelSettingsLink>
                )}
              </div>
            </form>
            {(provider.data?.key_configured || provider.isError) && (
              <ResearchProviderPanel
                projectId={projectId}
                state={provider.data}
                loading={provider.isPending}
                error={provider.error}
                changed={() => provider.refetch({ throwOnError: true })}
              />
            )}
          </>
        )}
        {workspace && session && (
          <>
            {initialDatasetId &&
              input.value.dataset_id !== initialDatasetId &&
              !session.is_archived && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={action.pending}
                  onClick={() =>
                    action.start(
                      async (signal) => {
                        await researchApi.sessions.attach(
                          session.id,
                          {
                            target_kind: "dataset_version",
                            target_id: initialDatasetId,
                          },
                          signal,
                        );
                        return researchApi.sessions.context(session.id, signal);
                      },
                      (context) => {
                        input.update({
                          dataset_id: initialDatasetId,
                          columns: Object.fromEntries(
                            context.datasets.map((item) => [
                              item.dataset_id,
                              input.value.columns[item.dataset_id] ??
                                (item.fields.length <= 10
                                  ? item.fields.map(
                                      (field) => field.reference.name,
                                    )
                                  : []),
                            ]),
                          ),
                        });
                        void client.invalidateQueries({
                          queryKey: ["research", workspace.id, session.id],
                        });
                      },
                    )
                  }
                >
                  {copy("使用当前数据", "Use current data")}
                </Button>
              )}
            {session.is_archived ? (
              <div className="research-archived">
                <span>
                  {copy("这段讨论已归档", "This discussion is archived")}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    action.start(
                      (signal) =>
                        researchApi.sessions.lifecycle(
                          session.id,
                          {
                            action: "restore",
                            expected_revision: session.revision,
                          },
                          signal,
                        ),
                      () => {
                        void refresh().catch(() => undefined);
                      },
                    )
                  }
                >
                  {copy("继续讨论", "Resume discussion")}
                </Button>
              </div>
            ) : (
              <ResearchConversation
                workspace={workspace}
                session={session}
                initialDatasetId={initialDatasetId}
                onApplyStatistics={onApplyStatistics}
                manualSource={manualSource}
                autoSubmit={
                  autoSubmit
                    ? { id: autoSubmit, started: () => setAutoSubmit(null) }
                    : undefined
                }
              />
            )}
            <details className="formal-lifecycle">
              <summary>{copy("讨论管理", "Manage discussions")}</summary>
              <ResearchConversations
                workspace={workspace}
                initialSessionId={session.id}
                initialRunId=""
                initialDatasetId={initialDatasetId}
                onSelect={(id) => {
                  void input
                    .choose(workspace.id, id || null)
                    .catch(() => undefined);
                }}
                onWorkspaceRemoved={() => {
                  void input
                    .choose(null, null)
                    .then(refresh)
                    .catch(() => undefined);
                }}
                changed={refresh}
                showConversation={false}
              />
            </details>
          </>
        )}
        <ResearchActionNotice action={action} reload={refresh} />
      </div>
    </InputContext.Provider>
  );
}

function ResearchWorkspaceEntry({
  projectId,
  initialDatasetId,
  restoreFromUrl = false,
  onApplyStatistics,
  manualSource,
}: {
  projectId?: string;
  initialDatasetId?: string;
  restoreFromUrl?: boolean;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
}) {
  const { t } = useLanguage();
  const client = useQueryClient();
  const [navigation, setNavigation] = useState({
    location: emptyLocation,
    revision: 0,
  });
  const navigationScope = useRef(navigation);
  const { location, revision: locationRevision } = navigation;
  const [title, setTitle] = useState("");
  const action = useResearchAction(
    `${projectId ?? "global"}:workspace:${locationRevision}`,
  );
  const updateLocation = useCallback((next: Location) => {
    const current = navigationScope.current;
    if (
      current.location.workspaceId === next.workspaceId &&
      current.location.sessionId === next.sessionId &&
      current.location.runId === next.runId
    )
      return;
    // Retire callbacks at the event boundary, including batched A→B→A, before render.
    const changed = { location: next, revision: current.revision + 1 };
    navigationScope.current = changed;
    setNavigation(changed);
  }, []);
  const workspaces = useQuery({
    queryKey: ["research", "workspaces"],
    queryFn: ({ signal }) => researchApi.workspaces.list(signal),
    retry: false,
  });
  const available =
    workspaces.data?.filter(
      (item) =>
        !item.is_deleted && (!projectId || item.project_id === projectId),
    ) ?? [];
  const workspace = available.find((item) => item.id === location.workspaceId);
  useEffect(() => {
    if (!restoreFromUrl) return;
    const read = () => {
      const params = new URL(window.location.href).searchParams;
      updateLocation({
        workspaceId: researchId(params.get("workspaceId")) ?? "",
        sessionId: researchId(params.get("sessionId")) ?? "",
        runId: researchId(params.get("runId")) ?? "",
      });
    };
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [restoreFromUrl, updateLocation]);
  function select(next: Location, expectedRevision = locationRevision) {
    if (navigationScope.current.revision !== expectedRevision) return;
    updateLocation(next);
    if (restoreFromUrl) {
      const url = new URL(window.location.href);
      for (const key of ["workspaceId", "sessionId", "runId"] as const) {
        if (next[key]) url.searchParams.set(key, next[key]);
        else url.searchParams.delete(key);
      }
      window.history.replaceState(window.history.state, "", url);
    }
  }
  const refresh = async () => {
    const result = await workspaces.refetch({ throwOnError: true });
    return result.data;
  };
  return (
    <div className="formal-research">
      <details
        className="formal-setup"
        key={`workspace-setup:${workspace?.id ?? "none"}`}
        open={!workspace}
      >
        <summary>
          {t("research.workspace")}
          {workspace && <> · {workspace.title}</>}
        </summary>
        <div className="formal-inline-form formal-workspace-selector">
          <label>
            {t("research.workspace")}
            <SearchSelect
              value={workspace?.id ?? ""}
              disabled={workspaces.isPending || action.pending}
              onChange={(event) =>
                select({
                  workspaceId: event.target.value,
                  sessionId: "",
                  runId: "",
                })
              }
            >
              <option value="">{t("research.chooseWorkspace")}</option>
              {available.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title} ·{" "}
                  {t(item.project_id ? "research.saved" : "research.temporary")}
                </option>
              ))}
            </SearchSelect>
          </label>
          <details className="formal-create">
            <summary>{t("research.new")}</summary>
            <label>
              {t("research.title")}
              <input
                value={title}
                maxLength={200}
                onChange={(event) => setTitle(event.target.value)}
                disabled={action.pending}
              />
            </label>
            <Button
              disabled={
                action.pending || action.error != null || workspaces.isError
              }
              onClick={() => {
                if (navigationScope.current.revision !== locationRevision)
                  return;
                action.start(
                  (signal) =>
                    researchApi.workspaces.create(
                      {
                        title: title.trim() || t("research.defaultTitle"),
                        project_id: projectId ?? null,
                      },
                      signal,
                    ),
                  (value) => {
                    if (navigationScope.current.revision !== locationRevision)
                      return;
                    client.setQueryData<ResearchWorkspace[]>(
                      ["research", "workspaces"],
                      (old) => [...(old ?? []), value],
                    );
                    select({ workspaceId: value.id, sessionId: "", runId: "" });
                    setTitle("");
                  },
                );
              }}
            >
              {t("research.new")}
            </Button>
          </details>
        </div>
      </details>
      {workspaces.isPending && <p role="status">{t("research.loading")}</p>}
      {workspaces.isError && (
        <ResearchFailure
          error={workspaces.error}
          retry={() => {
            void workspaces.refetch();
          }}
        />
      )}
      {!workspace && workspaces.isSuccess && (
        <p
          className="entry-help"
          role={location.workspaceId ? "alert" : undefined}
        >
          {t(
            location.workspaceId
              ? "research.missingIdentity"
              : "research.noWorkspaces",
          )}
        </p>
      )}
      <ResearchActionNotice action={action} reload={refresh} />
      {workspace && (
        <ResearchConversations
          key={workspace.id}
          workspace={workspace}
          initialSessionId={location.sessionId}
          initialRunId={location.runId}
          initialDatasetId={initialDatasetId}
          onSelect={(sessionId) =>
            select({ workspaceId: workspace.id, sessionId, runId: "" })
          }
          onWorkspaceRemoved={() => {
            if (navigationScope.current.revision !== locationRevision) return;
            select(emptyLocation);
            void refresh().catch(() => undefined);
          }}
          changed={refresh}
          onApplyStatistics={onApplyStatistics}
          manualSource={manualSource}
        />
      )}
    </div>
  );
}

function ResearchConversations({
  workspace,
  initialSessionId,
  initialRunId,
  initialDatasetId,
  onSelect,
  onWorkspaceRemoved,
  changed,
  onApplyStatistics,
  manualSource,
  showConversation = true,
}: {
  workspace: ResearchWorkspace;
  initialSessionId: string;
  initialRunId: string;
  initialDatasetId?: string;
  onSelect: (id: string) => void;
  changed: () => Promise<unknown>;
  onWorkspaceRemoved: () => void;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
  showConversation?: boolean;
}) {
  const { t } = useLanguage();
  const [archived, setArchived] = useState(Boolean(initialSessionId));
  const [title, setTitle] = useState("");
  const [projectId, setProjectId] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [confirmWorkspace, setConfirmWorkspace] = useState(false);
  const action = useResearchAction(
    `${workspace.id}:${initialSessionId}:${initialRunId}:lifecycle`,
  );
  const sessions = useQuery({
    queryKey: ["research", workspace.id, "sessions", archived],
    queryFn: ({ signal }) =>
      researchApi.sessions.list(workspace.id, archived, signal),
    retry: false,
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => projectsApi.list(signal),
  });
  const session = sessions.data?.find(
    (item) =>
      item.id === initialSessionId &&
      !item.is_deleted &&
      item.workspace_id === workspace.id,
  );
  const refresh = async () => {
    await sessions.refetch({ throwOnError: true });
    return changed();
  };
  const lifecycle = (next: "archive" | "restore" | "delete") => {
    if (!session) return;
    action.start(
      (signal) =>
        researchApi.sessions.lifecycle(
          session.id,
          { action: next, expected_revision: session.revision },
          signal,
        ),
      () => {
        if (next === "delete") onSelect("");
        setConfirm(false);
        void refresh().catch(() => undefined);
      },
    );
  };
  return (
    <>
      <details
        className="formal-setup"
        key={session?.id ?? "no-session"}
        open={!session}
      >
        <summary>
          {t("research.session")}
          {session && <> · {session.title}</>}
        </summary>
        <div className="formal-inline-form">
          <label>
            {t("research.session")}
            <SearchSelect
              value={session?.id ?? ""}
              disabled={action.pending || sessions.isPending}
              onChange={(event) => {
                onSelect(event.target.value);
                setConfirm(false);
              }}
            >
              <option value="">{t("research.chooseSession")}</option>
              {sessions.data
                ?.filter(
                  (item) =>
                    !item.is_deleted && item.workspace_id === workspace.id,
                )
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                    {item.is_archived ? ` · ${t("research.archive")}` : ""}
                  </option>
                ))}
            </SearchSelect>
          </label>
          <details className="formal-create">
            <summary>{t("research.newSession")}</summary>
            <label>
              {t("research.title")}
              <input
                value={title}
                maxLength={200}
                disabled={action.pending}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            <Button
              disabled={
                action.pending || action.error != null || sessions.isError
              }
              onClick={() =>
                action.start(
                  (signal) =>
                    researchApi.sessions.create(
                      workspace.id,
                      { title: title.trim() || t("research.defaultSession") },
                      signal,
                    ),
                  (value) => {
                    onSelect(value.id);
                    setTitle("");
                    void refresh().catch(() => undefined);
                  },
                )
              }
            >
              {t("research.newSession")}
            </Button>
          </details>
        </div>
        <label className="formal-checkbox">
          <input
            type="checkbox"
            checked={archived}
            onChange={(event) => {
              setArchived(event.target.checked);
              if (!event.target.checked && session?.is_archived) onSelect("");
            }}
          />
          {t("research.showArchived")}
        </label>
      </details>
      {sessions.isPending && <p role="status">{t("research.loading")}</p>}
      {sessions.isError && (
        <ResearchFailure
          error={sessions.error}
          retry={() => {
            void sessions.refetch();
          }}
        />
      )}
      {!session && sessions.isSuccess && (
        <p className="entry-help" role={initialSessionId ? "alert" : undefined}>
          {t(
            initialSessionId
              ? "research.missingIdentity"
              : "research.noSessions",
          )}
        </p>
      )}
      <details className="formal-lifecycle">
        <summary>{t("research.workspaceActions")}</summary>
        {!workspace.project_id && (
          <div className="formal-inline-form">
            <label>
              {t("research.saveProject")}
              <SearchSelect
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
                disabled={action.pending || projects.isPending}
              >
                <option value="">{t("research.chooseProject")}</option>
                {projects.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </SearchSelect>
            </label>
            <Button
              variant="outline"
              disabled={
                action.pending ||
                action.error != null ||
                !projects.data?.some((item) => item.id === projectId)
              }
              onClick={() =>
                action.start(
                  (signal) =>
                    researchApi.workspaces.save(
                      workspace.id,
                      {
                        project_id: projectId,
                        expected_revision: workspace.revision,
                      },
                      signal,
                    ),
                  () => {
                    void refresh().catch(() => undefined);
                  },
                )
              }
            >
              {t("research.saveProject")}
            </Button>
          </div>
        )}
        {session && (
          <div className="formal-actions">
            <Button
              variant="outline"
              disabled={action.pending || action.error != null}
              onClick={() =>
                lifecycle(session.is_archived ? "restore" : "archive")
              }
            >
              {t(session.is_archived ? "research.restore" : "research.archive")}
            </Button>
            <Button
              variant="ghost"
              onClick={() => setConfirm(true)}
              disabled={action.pending}
            >
              {t("research.remove")}
            </Button>
          </div>
        )}
        {confirm && session && (
          <div role="alert">
            <p>{t("research.confirmRemove")}</p>
            <Button
              variant="outline"
              disabled={action.pending || action.error != null}
              onClick={() => lifecycle("delete")}
            >
              {t("research.remove")}
            </Button>
          </div>
        )}
        <label className="formal-checkbox">
          <input
            type="checkbox"
            checked={confirmWorkspace}
            disabled={action.pending}
            onChange={(event) => setConfirmWorkspace(event.target.checked)}
          />
          {t("research.confirmRemove")}
        </label>
        <Button
          variant="ghost"
          disabled={!confirmWorkspace || action.pending || action.error != null}
          onClick={() =>
            action.start(
              (signal) =>
                researchApi.workspaces.remove(
                  workspace.id,
                  workspace.revision,
                  signal,
                ),
              onWorkspaceRemoved,
            )
          }
        >
          {t("research.removeWorkspace")}
        </Button>
      </details>
      <ResearchActionNotice action={action} reload={refresh} />
      {session && showConversation && (
        <ResearchConversation
          key={`${session.id}:${session.lifecycle_epoch}`}
          workspace={workspace}
          session={session}
          initialRunId={initialRunId}
          initialDatasetId={initialDatasetId}
          onApplyStatistics={onApplyStatistics}
          manualSource={manualSource}
        />
      )}
    </>
  );
}

function ResearchConversation({
  workspace,
  session,
  initialRunId,
  initialDatasetId,
  onApplyStatistics,
  manualSource,
  autoSubmit,
}: {
  workspace: ResearchWorkspace;
  session: ResearchSession;
  initialRunId?: string;
  initialDatasetId?: string;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
  autoSubmit?: AutoSubmit;
}) {
  const { t } = useLanguage();
  const client = useQueryClient();
  const context = useQuery({
    queryKey: ["research", workspace.id, session.id, "context"],
    queryFn: ({ signal }) => researchApi.sessions.context(session.id, signal),
    refetchInterval: 10000,
    retry: false,
  });
  const attachments = useQuery({
    queryKey: ["research", workspace.id, session.id, "attachments"],
    queryFn: ({ signal }) =>
      researchApi.sessions.attachments(session.id, signal),
    retry: false,
  });
  const feed = useResearchEvents(workspace.id);
  const protocols = useQuery({
    queryKey: ["research", workspace.id, session.id, "protocols"],
    queryFn: ({ signal }) => researchApi.protocols.history(session.id, signal),
    refetchInterval: 5000,
    retry: false,
  });
  const references = researchReferences(feed.data?.rows ?? [], session.id);
  const runQueries = useQueries({
    queries: references.runIds.map((id) => ({
      queryKey: ["research", workspace.id, session.id, "run", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        researchApi.runs.get(id, signal),
      refetchInterval: 3000,
      retry: false,
    })),
  });
  const runs = runQueries.flatMap((query) =>
    query.data?.session_id === session.id ? [query.data] : [],
  );
  const unsettled =
    feed.isPending ||
    feed.isError ||
    Boolean(feed.data?.more) ||
    runQueries.some(
      (query) =>
        query.isPending ||
        query.isError ||
        query.data?.session_id !== session.id,
    );
  const active =
    unsettled ||
    runs.some((run) =>
      [
        "queued",
        "running",
        "waiting_approval",
        "paused",
        "cancel_requested",
      ].includes(run.state),
    );
  const cursor = feed.data?.cursor ?? 0;
  const verification = useQuery({
    queryKey: ["research", workspace.id, "business", cursor],
    queryFn: ({ signal }) =>
      researchApi.events.verifyBusiness(workspace.id, signal),
    enabled: !unsettled && runs.some((run) => run.state === "completed"),
    retry: false,
  });
  const refresh = async () => {
    await Promise.all([
      context.refetch({ throwOnError: true }),
      attachments.refetch({ throwOnError: true }),
      feed.refetch({ throwOnError: true }),
      protocols.refetch({ throwOnError: true }),
      client.invalidateQueries({
        queryKey: ["research", workspace.id, session.id, "run"],
      }),
    ]);
  };
  const coherent =
    context.data?.workspace_id === workspace.id &&
    context.data.session_id === session.id &&
    attachments.isSuccess;
  return (
    <div className="formal-conversation">
      {session.is_archived && <p role="status">{t("research.archived")}</p>}
      {context.isPending && <p role="status">{t("research.loading")}</p>}
      {context.isError && (
        <ResearchFailure
          error={context.error}
          retry={() => {
            void context.refetch();
          }}
        />
      )}
      {attachments.isError && (
        <ResearchFailure
          error={attachments.error}
          retry={() => {
            void attachments.refetch();
          }}
        />
      )}
      {feed.isError && (
        <ResearchFailure
          error={feed.error}
          retry={() => {
            void feed.refetch();
          }}
        />
      )}
      {protocols.isError && (
        <ResearchFailure
          error={protocols.error}
          retry={() => {
            void protocols.refetch();
          }}
        />
      )}
      {runQueries
        .filter((query) => query.isError)
        .map((query, index) => (
          <ResearchFailure
            key={index}
            error={query.error}
            retry={() => {
              void query.refetch();
            }}
          />
        ))}
      {coherent && context.data && (
        <>
          {protocols.isSuccess && protocols.data && (
            <ResearchComposer
              key={context.data.source_version}
              workspace={workspace}
              session={session}
              context={context.data}
              attachments={attachments.data ?? []}
              events={feed.data?.rows ?? []}
              protocolHistory={protocols.data}
              protocolReadOnly={
                session.is_archived ||
                unsettled ||
                context.isError ||
                attachments.isError
              }
              initialDatasetId={initialDatasetId}
              runs={runs}
              disabled={
                active ||
                context.isError ||
                attachments.isError ||
                session.is_archived
              }
              changed={refresh}
              onApplyStatistics={onApplyStatistics}
              manualSource={manualSource}
              autoSubmit={autoSubmit}
            />
          )}
          {runs.some((run) => run.state === "completed") && (
            <details className="formal-verification">
              <summary>{t("research.sourceDetails")}</summary>
              <p className="entry-help">
                {t(
                  verification.data?.verified &&
                    verification.data.event_count === cursor &&
                    !unsettled
                    ? "research.verified"
                    : "research.notVerified",
                )}
              </p>
              <Button
                variant="ghost"
                disabled={verification.isFetching || unsettled}
                onClick={() => {
                  void verification.refetch();
                }}
              >
                {t("research.verify")}
              </Button>
            </details>
          )}
          {verification.isError && (
            <ResearchFailure
              error={verification.error}
              retry={() => {
                void verification.refetch();
              }}
            />
          )}
          {(runs.length > 0 ||
            references.draftIds.length > 0 ||
            references.acceptanceIds.length > 0) && (
            <FormalResearchActivity
              context={context.data}
              runs={runs}
              events={feed.data?.rows ?? []}
              verified={
                verification.data?.verified === true &&
                verification.data.event_count === cursor &&
                !unsettled
              }
              changed={refresh}
              initialRunId={initialRunId}
              onApplyStatistics={onApplyStatistics}
              manualSource={manualSource}
              readOnly={session.is_archived || unsettled}
            />
          )}
          {workspace.project_id && !onApplyStatistics && (
            <DesktopLink
              className="entry-manual"
              href={`/projects/${workspace.project_id}/workspace`}
            >
              {t("research.manual")}
            </DesktopLink>
          )}
        </>
      )}
    </div>
  );
}

function ResearchComposer({
  workspace,
  session,
  context,
  attachments,
  events,
  runs,
  initialDatasetId,
  disabled,
  changed,
  onApplyStatistics,
  manualSource,
  protocolHistory,
  protocolReadOnly,
  autoSubmit,
}: {
  workspace: ResearchWorkspace;
  session: ResearchSession;
  context: ResearchContext;
  attachments: Awaited<ReturnType<typeof researchApi.sessions.attachments>>;
  events: import("@/lib/research-types").ResearchEvent[];
  runs: Awaited<ReturnType<typeof researchApi.runs.get>>[];
  initialDatasetId?: string;
  disabled: boolean;
  changed: () => Promise<unknown>;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
  protocolHistory: ProtocolHistory;
  protocolReadOnly: boolean;
  autoSubmit?: AutoSubmit;
}) {
  const { t, locale } = useLanguage();
  const input = useContext(InputContext);
  const questionId = useId();
  const [localQuestion, setLocalQuestion] = useState("");
  const [localColumns, setLocalColumns] = useState<ResearchColumns>({});
  const question = input?.value.question ?? localQuestion;
  const selectedColumns = input?.value.columns ?? localColumns;
  const columns = Object.fromEntries(
    context.datasets.map((item) => [
      item.dataset_id,
      selectedColumns[item.dataset_id] ?? [],
    ]),
  );
  const setQuestion = (value: string) =>
    input ? input.update({ question: value }) : setLocalQuestion(value);
  const setColumns = (value: ResearchColumns) =>
    input ? input.update({ columns: value }) : setLocalColumns(value);
  const [proposal, setProposal] = useState<ResearchAnyPlan | null>(null);
  const [clarification, setClarification] = useState(false);
  const [capability, setCapability] =
    useState<ResearchCapabilityId>("descriptive");
  const [datasetId, setDatasetId] = useState(
    context.datasets[0]?.dataset_id ?? "",
  );
  const [method, setMethod] = useState("");
  const [protocolBusy, setProtocolBusy] = useState(false);
  const [protocolReady, setProtocolReady] = useState(false);
  const protocol = currentProtocol(protocolHistory, context);
  const protocolEnabled =
    protocolHistory.versions.length > 0 ||
    protocolHistory.activations.length > 0;
  const action = useResearchAction(
    `${session.id}:${context.source_version}:question:${protocolHistory.activations.at(-1)?.generation ?? 0}`,
  );
  const provider = useQuery({
    queryKey: ["research", "provider"],
    queryFn: ({ signal }) => researchApi.security.provider(signal),
    retry: false,
  });
  const budget = useQuery({
    queryKey: ["research", "budget"],
    queryFn: ({ signal }) => researchApi.security.budget(signal),
    refetchInterval: 10000,
    retry: false,
  });
  const capabilities = useQuery({
    queryKey: ["research", "capabilities"],
    queryFn: ({ signal }) => researchApi.plans.capabilities(signal),
    retry: false,
  });
  const blocked =
    disabled ||
    action.pending ||
    action.error != null ||
    protocolBusy ||
    (input !== null && !input.ready);
  const reader = context.datasets.length === 0 && context.artifacts.length > 0;
  const selections = context.datasets.map((item) => ({
    dataset_id: item.dataset_id,
    columns: columns[item.dataset_id] ?? [],
  }));
  const fieldsReady = selections.every(
    (item) => item.columns.length > 0 && item.columns.length <= 10,
  );
  const requiredFields = protocol
    ? [
        ...protocol.version.protocol.variables.map((item) => item.reference),
        ...(protocol.version.protocol.pairing?.pair_id
          ? [protocol.version.protocol.pairing.pair_id]
          : []),
      ]
    : [];
  const protocolFieldsReady = requiredFields.every((field) =>
    columns[field.dataset_id]?.includes(field.name),
  );
  const protocolBlocked =
    !reader &&
    protocolEnabled &&
    (!protocol ||
      !protocolReady ||
      question !== protocol.version.protocol.research_question ||
      !protocolFieldsReady);
  function invoke() {
    if (
      blocked ||
      protocolBlocked ||
      !provider.data?.enabled ||
      !provider.data.key_configured ||
      !question.trim() ||
      (!reader && (context.datasets.length === 0 || !fieldsReady))
    )
      return false;
    action.start(
      (signal, key) =>
        (reader ? researchApi.runs.reader : researchApi.runs.planner)(
          session.id,
          {
            ...(!reader && protocolEnabled && protocol
              ? {
                  version: "research-planner-v2" as const,
                  protocol_version_id: protocol.version.version_id,
                  protocol_hash: protocol.version.protocol_hash,
                  protocol_generation: protocol.activation.generation,
                }
              : {}),
            source_version: context.source_version,
            idempotency_key: key,
            selection: {
              question:
                !reader && protocolEnabled && protocol
                  ? protocol.version.protocol.research_question
                  : question.trim(),
              purpose: reader ? "review" : "planning",
              scope: "metadata_only",
              datasets: selections,
              artifact_ids: context.artifacts.map((item) => item.artifact_id),
              rows: 0,
              acknowledge_raw: false,
            },
          },
          signal,
        ),
      () => {
        if (reader || !protocolEnabled) setQuestion("");
        void changed().catch(() => undefined);
      },
      { retryable: true },
    );
    return true;
  }
  const autoRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      !autoSubmit ||
      autoRef.current === autoSubmit.id ||
      blocked ||
      protocolBlocked ||
      !fieldsReady ||
      !provider.data?.enabled ||
      !provider.data.key_configured
    )
      return;
    autoRef.current = autoSubmit.id;
    autoSubmit.started();
    invoke();
  });
  return (
    <>
      <details className="research-source-summary" open={!fieldsReady}>
        <summary>
          {locale === "zh-CN" ? "数据与变量" : "Data and variables"} ·{" "}
          {context.datasets.length + context.artifacts.length}
        </summary>
        <FormalResearchSources
          workspace={workspace}
          session={session}
          context={context}
          attachments={attachments}
          events={events}
          columns={columns}
          onColumns={setColumns}
          disabled={blocked}
          changed={changed}
          knownRuns={runs}
          initialDatasetId={initialDatasetId}
        />
      </details>
      {!session.is_archived && (
        <>
          <form
            className="entry-question"
            onSubmit={(event) => {
              event.preventDefault();
              invoke();
            }}
          >
            <label className="entry-question-label" htmlFor={questionId}>
              {t("research.question")}
            </label>
            <textarea
              id={questionId}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              maxLength={protocolEnabled ? 1000 : 4000}
              placeholder={t("research.questionHint")}
              disabled={blocked}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                  event.preventDefault();
                  if (provider.data?.enabled && provider.data.key_configured)
                    invoke();
                }
              }}
            />
            <div className="entry-question-actions">
              <Button
                type="submit"
                disabled={
                  blocked ||
                  protocolBlocked ||
                  !question.trim() ||
                  (!reader &&
                    (context.datasets.length === 0 || !fieldsReady)) ||
                  !provider.data?.enabled ||
                  !provider.data.key_configured ||
                  provider.isError
                }
              >
                {t(
                  reader ? "research.explainQuestion" : "research.planQuestion",
                )}
              </Button>
            </div>
          </form>
          {context.datasets.length > 0 && context.artifacts.length > 0 && (
            <p className="entry-help">{t("research.readerOnly")}</p>
          )}
          {!reader && (
            <details className="research-design-options">
              <summary>
                {locale === "zh-CN"
                  ? "研究设计与方法"
                  : "Study design and methods"}
              </summary>
              <FormalResearchProtocol
                key={`${context.source_version}:${protocolHistory.versions.at(-1)?.revision ?? 0}:${protocolHistory.activations.at(-1)?.generation ?? 0}`}
                context={context}
                history={protocolHistory}
                question={question}
                onQuestion={setQuestion}
                onProposal={setProposal}
                onBusy={setProtocolBusy}
                onReady={setProtocolReady}
                changed={changed}
                readOnly={protocolReadOnly}
                newWorkDisabled={
                  disabled || action.pending || action.error != null
                }
              />
            </details>
          )}
          <details className="formal-structured">
            <summary>
              {locale === "zh-CN" ? "选择分析操作" : "Choose an analysis"}
            </summary>
            <div className="formal-inline-form">
              <label>
                {t("research.capability")}
                <SearchSelect
                  value={capability}
                  disabled={
                    blocked || capabilities.isPending || capabilities.isError
                  }
                  onChange={(event) => {
                    setCapability(event.target.value as ResearchCapabilityId);
                    setProposal(null);
                  }}
                >
                  {capabilities.data?.map((item) => (
                    <option key={item.id} value={item.id}>
                      {t(
                        item.id === "inspect"
                          ? "research.inspect"
                          : item.id === "profile"
                            ? "research.profile"
                            : item.id === "quality"
                              ? "research.quality"
                              : item.id === "descriptive"
                                ? "research.descriptive"
                                : "research.correlation",
                      )}
                    </option>
                  ))}
                </SearchSelect>
              </label>
              <label>
                {t("research.dataset")}
                <SearchSelect
                  value={datasetId}
                  disabled={blocked}
                  onChange={(event) => {
                    setDatasetId(event.target.value);
                    setProposal(null);
                  }}
                >
                  <option value="">{t("research.chooseDataset")}</option>
                  {context.datasets.map((item) => (
                    <option key={item.dataset_id} value={item.dataset_id}>
                      {item.dataset_id}
                    </option>
                  ))}
                </SearchSelect>
              </label>
              {capability === "correlation" && (
                <label>
                  {t("research.method")}
                  <SearchSelect
                    value={method}
                    disabled={blocked}
                    onChange={(event) => {
                      setMethod(event.target.value);
                      setProposal(null);
                    }}
                  >
                    <option value="">{t("research.method")}</option>
                    {capabilities.data
                      ?.find((item) => item.id === "correlation")
                      ?.methods?.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                  </SearchSelect>
                </label>
              )}
              <Button
                variant="outline"
                disabled={
                  blocked ||
                  !capabilities.data?.some((item) => item.id === capability)
                }
                onClick={() =>
                  action.start(
                    (signal) =>
                      researchApi.plans.ruleProposal(
                        session.id,
                        {
                          source_version: context.source_version,
                          selection: {
                            capability,
                            dataset_id: datasetId || null,
                            field_names: columns[datasetId] ?? [],
                            configuration:
                              capability === "correlation" && method
                                ? { method_id: method }
                                : {},
                          },
                        },
                        signal,
                      ),
                    (value) => {
                      setClarification(value.state === "clarification");
                      setProposal(
                        value.state === "proposed" ? value.plan : null,
                      );
                    },
                  )
                }
              >
                {locale === "zh-CN" ? "查看方案" : "Preview plan"}
              </Button>
            </div>
            {clarification && (
              <p role="status">
                {t("research.clarification")} ·{" "}
                {t("research.review_parameters")}
              </p>
            )}
            {capabilities.isError && (
              <ResearchFailure
                error={capabilities.error}
                retry={() => {
                  void capabilities.refetch();
                }}
              />
            )}
          </details>
          {proposal && (
            <FormalResearchProposal
              key={JSON.stringify(proposal)}
              plan={proposal}
              context={context}
              sessionId={session.id}
              changed={changed}
              saved={() => setProposal(null)}
              onApplyStatistics={onApplyStatistics}
              manualSource={manualSource}
            />
          )}
        </>
      )}
      <ResearchActionNotice action={action} reload={changed} />
      <ResearchProviderPanel
        projectId={workspace.project_id ?? undefined}
        state={provider.data}
        loading={provider.isPending}
        error={provider.error}
        changed={async () => {
          await provider.refetch({ throwOnError: true });
          await budget.refetch({ throwOnError: true });
        }}
      />
      {budget.isError && (
        <ResearchFailure
          error={budget.error}
          retry={() => {
            void budget.refetch();
          }}
        />
      )}
      {budget.data && (
        <details className="formal-budget">
          <summary>{t("research.budget")}</summary>
          {budget.data.accounts.map((account) => (
            <dl className="formal-metadata" key={account.id}>
              <dt data-no-translate>{account.scope}</dt>
              <dd>
                {t("research.reserved")}:{" "}
                <ResearchMoney units={account.reserved_units} /> ·{" "}
                {t("research.used")}:{" "}
                <ResearchMoney units={account.consumed_units} /> ·{" "}
                {t("research.limit")}:{" "}
                <ResearchMoney units={account.cost_limit_units} /> ·{" "}
                {t("research.requests")}: {account.attempts}/
                {account.attempt_limit}
              </dd>
            </dl>
          ))}
        </details>
      )}
    </>
  );
}
