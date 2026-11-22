import { apiFetch } from "./api-client";
import type { ResearchProviderState } from "./research-types";

export type ModelSettings = NonNullable<ResearchProviderState["settings"]>;
export interface ModelConnection {
  id: string;
  configuration_id: string;
  is_current: boolean;
  enabled: boolean;
  key_state: string;
  name: string;
  endpoint: string | null;
  settings: ModelSettings;
  created_at: string;
}
export interface ModelConnections {
  revision: number;
  connections: ModelConnection[];
  total: number;
  next_offset: number | null;
  profiles: { id: string; name: string; endpoint: string }[];
}
export interface ConnectionTestResult {
  code: string;
  reachable: boolean;
  authentication_verified: boolean | null;
  model_found: boolean | null;
  balance_available: boolean | null;
  latency_ms: number;
  generation_checked: false;
  configuration_saved: false;
}
const base = "/api/v1/research/security/provider/connections";
export const modelConnectionsApi = {
  test(
    body: {
      provider: string;
      model: string;
      key?: string;
      configuration_id?: string;
      expected_revision: number;
      timeout_ms: number;
    },
    signal?: AbortSignal,
  ) {
    return apiFetch<ConnectionTestResult>(`${base}/test`, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    });
  },
  enable(revision: number, enabled: boolean, signal?: AbortSignal) {
    return apiFetch<ResearchProviderState>(
      "/api/v1/research/security/provider/enabled",
      {
        method: "PUT",
        body: JSON.stringify({ expected_revision: revision, enabled }),
        signal,
      },
    );
  },
  list(signal?: AbortSignal, offset = 0) {
    return apiFetch<ModelConnections>(`${base}?offset=${offset}`, { signal });
  },
  save(
    body: {
      expected_revision: number;
      settings: ModelSettings;
      key?: string;
      source_configuration_id?: string;
      new_connection: boolean;
      display_name: string;
    },
    signal?: AbortSignal,
  ) {
    return apiFetch<ResearchProviderState>(
      "/api/v1/research/security/provider",
      { method: "PUT", body: JSON.stringify(body), signal },
    );
  },
  activate(id: string, revision: number, signal?: AbortSignal) {
    return apiFetch<ResearchProviderState>(`${base}/${id}/activate`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: revision }),
      signal,
    });
  },
  inspect(id: string, revision: number, signal?: AbortSignal) {
    return apiFetch<{ configuration_valid: boolean; network_checked: boolean }>(
      `${base}/${id}/inspect`,
      {
        method: "POST",
        body: JSON.stringify({ expected_revision: revision }),
        signal,
      },
    );
  },
  remove(id: string, revision: number, signal?: AbortSignal) {
    return apiFetch<{ status: string }>(`${base}/${id}`, {
      method: "DELETE",
      body: JSON.stringify({ expected_revision: revision }),
      signal,
    });
  },
};
