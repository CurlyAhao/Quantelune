import { apiFetch } from "./api-client";

export type LocalEnvironment = {
  python_version: string;
  python_executable: string;
  packages: { name: string; version: string | null }[];
  editors: { id: string; name: string }[];
  default_editor: string;
  native_launch: boolean;
};

export function localEnvironment(signal?: AbortSignal) {
  return apiFetch<LocalEnvironment>("/api/v1/local-environment", { signal });
}
