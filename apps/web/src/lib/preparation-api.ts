import { apiFetch } from "./api-client";
import type { Dataset } from "./types";

export type PreparationOperation =
  | "missing"
  | "units"
  | "dates"
  | "join"
  | "rename"
  | "cast"
  | "trim"
  | "deduplicate";
export interface PreparationRequest {
  source_sha256: string;
  operation: PreparationOperation;
  column: string;
  rule?: "keep" | "median" | "mean" | "value" | "drop";
  value?: string;
  target?: string;
  right_dataset_id?: string;
  right_sha256?: string;
  right_key?: string;
  duplicate?: "block" | "first" | "last";
  result_sha256?: string;
}
export interface PreparationPreview {
  blocked: boolean;
  label?: string;
  before_rows?: number;
  after_rows?: number;
  after_columns?: number;
  affected_rows?: number;
  removed_rows?: number;
  changes: { row: number; column: string; before: unknown; after: unknown }[];
  schema_changes?: { column: string; before: string; after: string }[];
  result_sha256?: string;
  notes: string[];
  join?: {
    matched: number;
    unmatched: number;
    duplicate_keys: number;
    unmatched_examples?: (string|number|null)[];
    duplicate_examples: Record<string, unknown>[];
  } | null;
}
export interface PreparationHistory {
  parent_id: string | null;
  root_id: string | null;
  steps: {
    label: string;
    recipe: PreparationRequest;
    affected_rows: number;
    removed_rows: number;
    output_sha256: string;
    created_at: string;
  }[];
}
export const preparationApi = {
  draft(id: string, signal?: AbortSignal) {
    return apiFetch<{revision: number; request: PreparationRequest | null}>(`/api/v1/datasets/${id}/preparation/draft`, {signal});
  },
  saveDraft(id: string, revision: number, request: PreparationRequest | null) {
    return apiFetch<{revision: number; request: PreparationRequest | null}>(`/api/v1/datasets/${id}/preparation/draft`, {method: "PUT", body: JSON.stringify({revision, request})});
  },
  metadata(id: string, signal?: AbortSignal) {
    return apiFetch<PreparationHistory | null>(
      `/api/v1/datasets/${id}/preparation`,
      { signal },
    );
  },
  preview(id: string, body: PreparationRequest) {
    return apiFetch<PreparationPreview>(
      `/api/v1/datasets/${id}/preparation/preview`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },
  apply(id: string, body: PreparationRequest) {
    return apiFetch<Dataset>(`/api/v1/datasets/${id}/preparation/apply`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};
