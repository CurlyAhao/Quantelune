import { apiFetch } from "./api-client";
import { isTauri } from "./tauri-adapter";
import type { PlotDraft, PlotRun, WorkspaceRecord } from "./workspace-api";
export const plotActive = (status?: string) =>
  ["queued", "running", "cancel_requested"].includes(status ?? "");
export type PlotStylePatch = {
  colors?: Record<string, string>;
  font_size?: number;
  width_inches?: number;
  height_inches?: number;
  line_width?: number;
  title?: string;
  grid?: boolean;
  dpi?: number;
};
export const plotsApi = {
  list: (project: string, draft?: string, signal?: AbortSignal) =>
    apiFetch<{ runs: WorkspaceRecord<PlotRun>[] }>(
      `/api/v1/projects/${project}/plots/runs${draft ? `?draft_id=${draft}` : ""}`,
      { signal },
    ),
  start: (
    project: string,
    draft: string,
    revision: number,
    requestId: string,
  ) =>
    apiFetch<WorkspaceRecord<PlotRun>>(
      `/api/v1/projects/${project}/plots/${draft}/runs`,
      {
        method: "POST",
        body: JSON.stringify({ revision, request_id: requestId }),
      },
    ),
  cancel: (project: string, id: string) =>
    apiFetch<WorkspaceRecord<PlotRun>>(
      `/api/v1/projects/${project}/plots/runs/${id}/cancel`,
      { method: "POST" },
    ),
  style: (project: string, id: string, style: PlotStylePatch, requestId: string) =>
    apiFetch<WorkspaceRecord<PlotRun>>(`/api/v1/projects/${project}/plots/runs/${id}/style`, {
      method: "POST",
      body: JSON.stringify({ style, request_id: requestId }),
    }),
  file: (project: string, id: string, format: "svg" | "png" | "pdf" | "py") =>
    `/api/v1/projects/${project}/plots/runs/${id}/files/${format}`,
  package: (project: string, id: string) =>
    `/api/v1/projects/${project}/plots/runs/${id}/package`,
};

export type PlotCodeFile =
  | { available: false }
  | {
      available: true;
      code: string;
      file_sha256: string;
      external_changed: boolean;
      application_changed: boolean;
      conflict: boolean;
      file_path: string;
      data_path: string;
      draft_revision: number;
      base_revision: number;
      dpi: number;
    };
export const plotCodeApi = {
  editors: (project: string, signal?: AbortSignal) =>
    apiFetch<{
      editors: { id: string; name: string }[];
      default_editor: string;
    }>(`/api/v1/projects/${project}/plots/editors`, { signal }),
  read: (project: string, draft: string, signal?: AbortSignal) =>
    apiFetch<PlotCodeFile>(
      `/api/v1/projects/${project}/plots/${draft}/code-file`,
      { signal },
    ),
  import: (
    project: string,
    draft: string,
    revision: number,
    sha: string,
    replace: boolean,
  ) =>
    apiFetch<{ record: WorkspaceRecord<PlotDraft>; file_sha256: string }>(
      `/api/v1/projects/${project}/plots/${draft}/code-file/import`,
      {
        method: "POST",
        body: JSON.stringify({ revision, file_sha256: sha, replace }),
      },
    ),
  open: async (
    project: string,
    draft: string,
    revision: number,
    editor: string,
    asNew = false,
  ) => {
    const value = await apiFetch<
      Extract<PlotCodeFile, { available: true }> & {
        native_launch_required?: boolean;
      }
    >(`/api/v1/projects/${project}/plots/${draft}/code-file/open`, {
      method: "POST",
      body: JSON.stringify({ revision, editor, as_new: asNew }),
    });
    if (value.native_launch_required) {
      if (!isTauri())
        throw new Error("Open the editor from the desktop window.");
      const native = (
        window as unknown as {
          __TAURI_INTERNALS__: {
            invoke: (command: string, args: unknown) => Promise<unknown>;
          };
        }
      ).__TAURI_INTERNALS__;
      await native.invoke("open_plot_editor", {
        projectId: project,
        draftId: draft,
        fileSha256: value.file_sha256,
        editor,
      });
    }
    return value;
  },
};
