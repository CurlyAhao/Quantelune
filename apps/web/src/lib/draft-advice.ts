import { apiFetch } from "./api-client";
import type { Location } from "./material-types";
import type { ResearchApproval } from "./research-types";
import type { PlotDraft } from "./workspace-api";
import type { ThemeColor } from "./text-coding";

export type AdviceTarget =
  | { kind: "plot"; draft_id: string; revision: number }
  | {
      kind: "coding";
      revision: number;
      selections: {
        material_id: string;
        material_sha256: string;
        material_version: number;
        locator: Extract<Location, { kind: "text" }>;
      }[];
    };
export type AdviceContext =
  | { kind: "plot"; draft: PlotDraft }
  | {
      kind: "coding";
      themes: { key: string; name: string }[];
      sources: { key: string; text: string; page: number }[];
    };
export type AdviceProposal =
  | { kind: "plot"; source_version: string; title: string; code: string }
  | {
      kind: "coding";
      source_version: string;
      themes: { key: string; name: string; color: ThemeColor; memo: string }[];
      segments: {
        source_key: string;
        start: number;
        end: number;
        quote: string;
        theme_keys: string[];
        memo: string;
      }[];
    };
export type AdviceRequest = {
  id: string;
  kind: "plot" | "coding";
  state:
    | "waiting_approval"
    | "running"
    | "cancel_requested"
    | "cancelled"
    | "failed"
    | "interrupted"
    | "proposed"
    | "applied";
  source_version: string;
  question: string;
  context: AdviceContext;
  approval: ResearchApproval;
  proposal: AdviceProposal | null;
  applied: { record_id: string; revision: number } | null;
  error: string | null;
};
const path = (project: string) => `/api/v1/projects/${project}/advice`;
const post = <T>(url: string, body?: unknown) =>
  apiFetch<T>(url, {
    method: "POST",
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
export const adviceApi = {
  context: (project: string, target: AdviceTarget) => {
    const { kind, ...body } = target;
    return kind === "plot"
      ? post<{ source_version: string; context: AdviceContext }>(
          `/api/v1/projects/${project}/plots/${(target as Extract<AdviceTarget, { kind: "plot" }>).draft_id}/advice-context`,
          { revision: target.revision },
        )
      : post<{ source_version: string; context: AdviceContext }>(
          `/api/v1/projects/${project}/coding/advice-context`,
          body,
        );
  },
  recent: (
    project: string,
    kind: "plot" | "coding",
    draftId?: string,
    signal?: AbortSignal,
  ) =>
    apiFetch<{ id: string; question: string }[]>(
      `${path(project)}?kind=${kind}${draftId ? `&draft_id=${draftId}` : ""}`,
      { signal },
    ),
  read: (project: string, id: string, signal?: AbortSignal) =>
    apiFetch<AdviceRequest>(`${path(project)}/${id}`, { signal }),
  create: (
    project: string,
    requestId: string,
    question: string,
    target: AdviceTarget,
    sourceVersion: string,
  ) =>
    post<AdviceRequest>(path(project), {
      request_id: requestId,
      question,
      target,
      source_version: sourceVersion,
    }),
  send: (project: string, request: AdviceRequest) =>
    post<AdviceRequest>(`${path(project)}/${request.id}/send`, {
      approval_id: request.approval.id,
      request_hash: request.approval.request_hash,
    }),
  cancel: (project: string, id: string) =>
    post<AdviceRequest>(`${path(project)}/${id}/cancel`),
  accept: (project: string, request: AdviceRequest, proposal: AdviceProposal) =>
    post<AdviceRequest>(`${path(project)}/${request.id}/accept`, {
      source_version: request.source_version,
      proposal,
    }),
};
