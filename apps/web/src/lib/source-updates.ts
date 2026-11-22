import { apiFetch, apiUpload } from "./api-client";
import type { ColumnProfile, Dataset, PreviewRow } from "./types";

export type SourceVersions = {
  source: Dataset;
  selected_id: string;
  selected_is_prepared: boolean;
  stored_available: boolean;
  versions: Dataset[];
};
type SourcePreview = {
  row_count: number;
  column_count: number;
  rows: PreviewRow[];
  columns: ColumnProfile[];
};
export type SourceComparison = {
  source: Dataset;
  same_content: boolean;
  bytes_identical: boolean;
  comparison_sha256: string;
  stored_available: boolean;
  selected_is_prepared: boolean;
  before: SourcePreview;
  after: SourcePreview & {
    filename: string;
    sha256: string;
    sheet_name: string | null;
    csv_encoding: string | null;
  };
  column_changes: {
    column: string;
    before: string | null;
    after: string | null;
  }[];
};
export const sourceUpdatesApi = {
  versions: (id: string, signal?: AbortSignal) =>
    apiFetch<SourceVersions>(`/api/v1/datasets/${id}/source-versions`, {
      signal,
    }),
  compare(
    id: string,
    file: File,
    sha: string,
    sheet: string,
    encoding: string,
    signal?: AbortSignal,
  ) {
    const form = body(file, sha, sheet, encoding);
    return apiUpload<SourceComparison>(
      `/api/v1/datasets/${id}/source-check`,
      form,
      { signal },
    );
  },
  apply(
    id: string,
    file: File,
    sha: string,
    sheet: string,
    encoding: string,
    comparison: SourceComparison,
    mode: "version" | "separate" | "restore",
    signal?: AbortSignal,
  ) {
    const form = body(file, sha, sheet, encoding);
    form.set("comparison_sha256", comparison.comparison_sha256);
    form.set("mode", mode);
    return apiUpload<{
      dataset: Dataset;
      outcome: "created" | "existing" | "verified";
    }>(`/api/v1/datasets/${id}/source-update`, form, { signal });
  },
};
function body(file: File, sha: string, sheet: string, encoding: string) {
  const form = new FormData();
  form.set("file", file);
  form.set("source_sha256", sha);
  if (sheet) form.set("sheet_name", sheet);
  form.set("csv_encoding", encoding);
  return form;
}
