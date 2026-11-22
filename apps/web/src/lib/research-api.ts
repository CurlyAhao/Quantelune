import { apiBlob, apiFetch } from "./api-client";
import type {
  ResearchApproval,
  ResearchApprovalDecision,
  ResearchArtifact,
  ResearchAttachment,
  ResearchAttempt,
  ResearchBudget,
  ResearchCapability,
  ResearchContext,
  ResearchControl,
  ResearchDraftSave,
  ResearchEvent,
  ResearchInvocationCreate,
  ResearchMessage,
  ResearchPlanAccept,
  ResearchPlanAcceptance,
  ResearchPlanExecution,
  ResearchPlanVersion,
  ResearchPlannerCreateV2,
  ResearchPlannerOutcome,
  ResearchProviderState,
  ResearchRevision,
  ResearchRuleProposal,
  ResearchRuleProposalCreate,
  ResearchRun,
  ResearchSession,
  ResearchWorkspace,
} from "./research-types";
import type {
  P4ResultFacts,
  ProtocolActivation,
  ProtocolAssessment,
  ProtocolBinding,
  ProtocolDocument,
  ProtocolHistory,
  ProtocolVersion,
} from "./research-protocol-types";

const base = "/api/v1/research";
const segment = (id: string) => {
  // URL parsers normalize even percent-encoded dot segments before sending.
  if (!id || id === "." || id === "..")
    throw new TypeError("Invalid research identifier.");
  return encodeURIComponent(id);
};
const sessionPath = (id: string) => `${base}/sessions/${segment(id)}`;
const runPath = (id: string) => `${base}/runs/${segment(id)}`;
const workspacePath = (id: string) => `${base}/workspaces/${segment(id)}`;
const acceptancePath = (sessionId: string, id: string) =>
  `${sessionPath(sessionId)}/plans/acceptances/${segment(id)}`;

function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiFetch<T>(path, { method: "GET", signal });
}

function send<T>(
  path: string,
  method: "POST" | "PUT" | "DELETE",
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return apiFetch<T>(path, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal,
  });
}

/** Thin transport only: no cache, automatic retry, dispatch, or provider/key writes. */
export const researchApi = {
  workspaces: {
    list: (signal?: AbortSignal) =>
      get<ResearchWorkspace[]>(`${base}/workspaces`, signal),
    get: (id: string, signal?: AbortSignal) =>
      get<ResearchWorkspace>(workspacePath(id), signal),
    create: (
      body: { title: string; project_id?: string | null },
      signal?: AbortSignal,
    ) => send<ResearchWorkspace>(`${base}/workspaces`, "POST", body, signal),
    save: (
      id: string,
      body: ResearchRevision & { project_id: string },
      signal?: AbortSignal,
    ) =>
      send<ResearchWorkspace>(
        `${workspacePath(id)}/save`,
        "POST",
        body,
        signal,
      ),
    remove: (id: string, expectedRevision: number, signal?: AbortSignal) =>
      send<ResearchWorkspace>(
        `${workspacePath(id)}?expected_revision=${encodeURIComponent(expectedRevision)}`,
        "DELETE",
        undefined,
        signal,
      ),
  },
  sessions: {
    list: (
      workspaceId: string,
      includeArchived = false,
      signal?: AbortSignal,
    ) =>
      get<ResearchSession[]>(
        `${workspacePath(workspaceId)}/sessions?include_archived=${includeArchived}`,
        signal,
      ),
    get: (id: string, signal?: AbortSignal) =>
      get<ResearchSession>(sessionPath(id), signal),
    create: (
      workspaceId: string,
      body: { title: string },
      signal?: AbortSignal,
    ) =>
      send<ResearchSession>(
        `${workspacePath(workspaceId)}/sessions`,
        "POST",
        body,
        signal,
      ),
    lifecycle: (
      id: string,
      body: ResearchRevision & { action: "archive" | "restore" | "delete" },
      signal?: AbortSignal,
    ) =>
      send<ResearchSession>(
        `${sessionPath(id)}/lifecycle`,
        "POST",
        body,
        signal,
      ),
    attachments: (id: string, signal?: AbortSignal) =>
      get<ResearchAttachment[]>(`${sessionPath(id)}/attachments`, signal),
    attach: (
      id: string,
      body: {
        target_kind: "project" | "dataset_version" | "artifact";
        target_id: string;
        allow_cross_project?: boolean;
      },
      signal?: AbortSignal,
    ) =>
      send<ResearchAttachment>(
        `${sessionPath(id)}/attachments`,
        "POST",
        body,
        signal,
      ),
    detach: (attachmentId: string, signal?: AbortSignal) =>
      send<void>(
        `${base}/attachments/${segment(attachmentId)}`,
        "DELETE",
        undefined,
        signal,
      ),
    context: (id: string, signal?: AbortSignal) =>
      get<ResearchContext>(`${sessionPath(id)}/context`, signal),
    messages: (id: string, limit = 100, signal?: AbortSignal) =>
      get<ResearchMessage[]>(
        `${sessionPath(id)}/messages?limit=${encodeURIComponent(limit)}`,
        signal,
      ),
    addMessage: (id: string, body: { text: string }, signal?: AbortSignal) =>
      send<ResearchMessage>(
        `${sessionPath(id)}/messages`,
        "POST",
        body,
        signal,
      ),
  },
  runs: {
    planningOutcome: (id: string, signal?: AbortSignal) =>
      get<ResearchPlannerOutcome | null>(
        `${runPath(id)}/planning/outcome`,
        signal,
      ),
    get: (id: string, signal?: AbortSignal) =>
      get<ResearchRun>(runPath(id), signal),
    attempts: (id: string, signal?: AbortSignal) =>
      get<ResearchAttempt[]>(`${runPath(id)}/attempts`, signal),
    artifacts: (id: string, signal?: AbortSignal) =>
      get<ResearchArtifact[]>(`${runPath(id)}/artifacts`, signal),
    planner: (
      sessionId: string,
      body: ResearchInvocationCreate | ResearchPlannerCreateV2,
      signal?: AbortSignal,
    ) =>
      send<ResearchRun>(
        `${sessionPath(sessionId)}/planner-runs`,
        "POST",
        body,
        signal,
      ),
    reader: (
      sessionId: string,
      body: ResearchInvocationCreate,
      signal?: AbortSignal,
    ) =>
      send<ResearchRun>(
        `${sessionPath(sessionId)}/reader-runs`,
        "POST",
        body,
        signal,
      ),
    planningControl: (
      id: string,
      body: ResearchControl,
      signal?: AbortSignal,
    ) =>
      send<Record<string, unknown>>(
        `${runPath(id)}/planning/control`,
        "POST",
        body,
        signal,
      ),
    readerControl: (id: string, body: ResearchControl, signal?: AbortSignal) =>
      send<Record<string, unknown>>(
        `${runPath(id)}/reader/control`,
        "POST",
        body,
        signal,
      ),
    cancel: (id: string, body: ResearchRevision, signal?: AbortSignal) =>
      send<ResearchRun>(`${runPath(id)}/cancel`, "POST", body, signal),
    pause: (id: string, body: ResearchRevision, signal?: AbortSignal) =>
      send<ResearchRun>(`${runPath(id)}/pause`, "POST", body, signal),
    resume: (id: string, body: ResearchRevision, signal?: AbortSignal) =>
      send<ResearchRun>(`${runPath(id)}/resume`, "POST", body, signal),
    retry: (
      id: string,
      body: ResearchRevision & { retry_key: string },
      signal?: AbortSignal,
    ) => send<ResearchRun>(`${runPath(id)}/retry`, "POST", body, signal),
    adoptResult: (
      sessionId: string,
      body: {
        domain_kind: "quality" | "statistics" | "ml" | "workflow";
        domain_run_id: string;
        idempotency_key: string;
      },
      signal?: AbortSignal,
    ) =>
      send<ResearchRun>(
        `${sessionPath(sessionId)}/domain-results`,
        "POST",
        body,
        signal,
      ),
  },
  protocols: {
    history: (sessionId: string, signal?: AbortSignal) =>
      get<ProtocolHistory>(`${sessionPath(sessionId)}/protocols`, signal),
    assess: (
      sessionId: string,
      body: ProtocolDocument,
      methodId?: string,
      signal?: AbortSignal,
    ) =>
      send<ProtocolAssessment>(
        `${sessionPath(sessionId)}/protocols/assessment${methodId ? `?method_id=${encodeURIComponent(methodId)}` : ""}`,
        "POST",
        body,
        signal,
      ),
    save: (
      sessionId: string,
      body: {
        protocol: ProtocolDocument;
        expected_revision: number;
        idempotency_key: string;
      },
      signal?: AbortSignal,
    ) =>
      send<ProtocolVersion>(
        `${sessionPath(sessionId)}/protocols/versions`,
        "POST",
        body,
        signal,
      ),
    activate: (
      sessionId: string,
      body: Omit<ProtocolBinding, "generation"> & {
        expected_generation: number;
        action: "confirm" | "revoke";
        idempotency_key: string;
      },
      signal?: AbortSignal,
    ) =>
      send<ProtocolActivation>(
        `${sessionPath(sessionId)}/protocols/activations`,
        "POST",
        body,
        signal,
      ),
  },
  plans: {
    capabilities: (signal?: AbortSignal) =>
      get<ResearchCapability[]>(`${base}/capabilities`, signal),
    ruleProposal: (
      sessionId: string,
      body: ResearchRuleProposalCreate,
      signal?: AbortSignal,
    ) =>
      send<ResearchRuleProposal>(
        `${sessionPath(sessionId)}/plans/rule-proposals`,
        "POST",
        body,
        signal,
      ),
    createDraft: (
      sessionId: string,
      body: ResearchDraftSave,
      signal?: AbortSignal,
    ) =>
      send<ResearchPlanVersion>(
        `${sessionPath(sessionId)}/plans/drafts`,
        "POST",
        body,
        signal,
      ),
    saveDraft: (
      sessionId: string,
      draftId: string,
      body: ResearchDraftSave,
      signal?: AbortSignal,
    ) =>
      send<ResearchPlanVersion>(
        `${sessionPath(sessionId)}/plans/drafts/${segment(draftId)}`,
        "PUT",
        body,
        signal,
      ),
    versions: (sessionId: string, draftId: string, signal?: AbortSignal) =>
      get<ResearchPlanVersion[]>(
        `${sessionPath(sessionId)}/plans/drafts/${segment(draftId)}/versions`,
        signal,
      ),
    accept: (
      sessionId: string,
      body: ResearchPlanAccept,
      signal?: AbortSignal,
    ) =>
      send<ResearchPlanAcceptance>(
        `${sessionPath(sessionId)}/plans/acceptances`,
        "POST",
        body,
        signal,
      ),
    acceptance: (sessionId: string, id: string, signal?: AbortSignal) =>
      get<ResearchPlanAcceptance>(acceptancePath(sessionId, id), signal),
    execution: (sessionId: string, id: string, signal?: AbortSignal) =>
      get<ResearchPlanExecution>(
        `${acceptancePath(sessionId, id)}/execution`,
        signal,
      ),
    resultFacts: (
      sessionId: string,
      id: string,
      stepId: string,
      signal?: AbortSignal,
    ) =>
      get<P4ResultFacts>(
        `${acceptancePath(sessionId, id)}/steps/${segment(stepId)}/result-facts`,
        signal,
      ),
    cancelExecution: (
      sessionId: string,
      id: string,
      body: ResearchRevision,
      signal?: AbortSignal,
    ) =>
      send<ResearchPlanExecution>(
        `${acceptancePath(sessionId, id)}/cancel`,
        "POST",
        body,
        signal,
      ),
  },
  events: {
    list: (
      workspaceId: string,
      afterSequence = 0,
      limit = 100,
      signal?: AbortSignal,
    ) =>
      get<ResearchEvent[]>(
        `${workspacePath(workspaceId)}/events?after_sequence=${encodeURIComponent(afterSequence)}&limit=${encodeURIComponent(limit)}`,
        signal,
      ),
    verify: (workspaceId: string, signal?: AbortSignal) =>
      get<{ verified: boolean; event_count: number }>(
        `${workspacePath(workspaceId)}/events/verify`,
        signal,
      ),
    verifyBusiness: (workspaceId: string, signal?: AbortSignal) =>
      get<{
        verified: boolean;
        event_count: number;
        runs: number;
        attempts: number;
        artifacts: number;
        sessions: number;
      }>(`${workspacePath(workspaceId)}/business/verify`, signal),
  },
  artifacts: {
    get: (id: string, signal?: AbortSignal) =>
      get<ResearchArtifact>(`${base}/artifacts/${segment(id)}`, signal),
    content: (id: string, signal?: AbortSignal) =>
      apiBlob(`${base}/artifacts/${segment(id)}/content`, signal),
    graph: (id: string, signal?: AbortSignal) =>
      get<Record<string, unknown>>(
        `${base}/artifacts/${segment(id)}/graph`,
        signal,
      ),
  },
  security: {
    provider: (signal?: AbortSignal) =>
      get<ResearchProviderState>(`${base}/security/provider`, signal),
    budget: (signal?: AbortSignal) =>
      get<ResearchBudget>(`${base}/security/budget`, signal),
    approval: (id: string, signal?: AbortSignal) =>
      get<ResearchApproval>(
        `${base}/security/approvals/${segment(id)}`,
        signal,
      ),
    decide: (
      id: string,
      body: ResearchApprovalDecision,
      signal?: AbortSignal,
    ) =>
      send<ResearchApproval>(
        `${base}/security/approvals/${segment(id)}/decision`,
        "POST",
        body,
        signal,
      ),
  },
};
