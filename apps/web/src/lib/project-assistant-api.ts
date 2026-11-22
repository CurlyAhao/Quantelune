import { apiFetch } from "./api-client";

export type AssistantResult = {
  kind: "statistics" | "ml" | "profile" | "dataset" | "figure" | "plot" | "material";
  id: string;
  title: string;
  href: string;
  workspace_record_id?: string;
  task_id?: string;
};

export type AssistantMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  status?: "running" | "completed" | "failed" | "cancelled";
  results?: AssistantResult[];
};

export type AssistantConversation = {
  id: string;
  title: string;
  material_brief?: string;
  context_key: string;
  dataset_id: string | null;
  updated_at: string;
  messages: AssistantMessage[];
  active_turn: {
    id: string;
    status: "running" | "stopping";
    progress: string;
  } | null;
};

export type AssistantTurnInput = {
  request_id: string;
  conversation_id?: string | null;
  material_action?: boolean;
  context_key: string;
  question: string;
  dataset_id?: string | null;
  figure_target?: { id: string; revision: number };
};

export type AssistantFigureTarget = { id: string; revision: number; name: string };

const base = (projectId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/assistant`;

export const projectAssistantApi = {
  materialResult(projectId: string, datasetId: string, contextKey: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ dataset_id: datasetId, context_key: contextKey });
    return apiFetch<{ item: AssistantConversation | null }>(`${base(projectId)}/material-result?${params}`, { signal });
  },
  list(projectId: string, contextKey: string, signal?: AbortSignal) {
    return apiFetch<{ items: AssistantConversation[] }>(
      `${base(projectId)}/conversations?context_key=${encodeURIComponent(contextKey)}`,
      { signal },
    );
  },
  read(projectId: string, conversationId: string, signal?: AbortSignal) {
    return apiFetch<AssistantConversation>(
      `${base(projectId)}/conversations/${encodeURIComponent(conversationId)}`,
      { signal },
    );
  },
  send(projectId: string, input: AssistantTurnInput, signal?: AbortSignal) {
    return apiFetch<AssistantConversation>(`${base(projectId)}/turns`, {
      method: "POST",
      body: JSON.stringify(input),
      signal,
    });
  },
  stop(projectId: string, conversationId: string, signal?: AbortSignal) {
    return apiFetch<AssistantConversation>(
      `${base(projectId)}/conversations/${encodeURIComponent(conversationId)}/stop`,
      { method: "POST", signal },
    );
  },
};
