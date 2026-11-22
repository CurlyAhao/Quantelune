import { ApiError, apiBlob, apiFetch, apiUpload } from "./api-client";
import { downloadApiFile } from "./api-download";
import { getApiRuntime } from "./tauri-adapter";
import type {
  Citation,
  CitationRequest,
  Material,
  TextPage,
} from "./material-types";

export type AudioPreview = {duration:number;sample_rate:number;frames:number;channels:number;start:number;end:number;clock:string;points:{time:number;value:number}[];read_frames:number;display_sampled:boolean};

export const materialApi = {
  list: async (projectId: string, signal?: AbortSignal) => {
    const result: Material[] = [];
    const seen = new Set<string>();
    for (;;) {
      const page = await apiFetch<Material[]>(
        `/api/v1/projects/${projectId}/materials?offset=${result.length}`, { signal },
      );
      for (const material of page) {
        if (seen.has(material.id))
          throw new ApiError(409, "Materials changed. Reload the list.", "MATERIAL_LIST_CHANGED");
        seen.add(material.id);
        result.push(material);
      }
      if (page.length < 100) return result;
    }
  },
  citations: (projectId: string, signal?: AbortSignal) =>
    apiFetch<Citation[]>(`/api/v1/projects/${projectId}/citations`, { signal }),
  get: (id: string, signal?: AbortSignal) =>
    apiFetch<Material>(`/api/v1/materials/${id}`, { signal }),
  text: (id: string, signal?: AbortSignal) =>
    apiFetch<{
      status: string;
      code: string | null;
      extractor?: string;
      pages: TextPage[];
    }>(`/api/v1/materials/${id}/text`, { signal }),
  textRange: (id: string, page: { index: number } | { offset: number }, limit: number, signal?: AbortSignal) =>
    apiFetch<{ page: number; text: string; sha256: string; start: number; total: number; index: number }>(
      `/api/v1/materials/${id}/text-range?${"index" in page ? `page_index=${page.index}` : `locate=true&offset=${page.offset}`}&limit=${limit}`, { signal },
    ),
  blob: (id: string, signal?: AbortSignal) =>
    apiBlob(`/api/v1/materials/${id}/content`, signal),
  playback: async (id: string, signal?: AbortSignal) => {
    const result = await apiFetch<AudioPreview & {url:string}>(`/api/v1/materials/${id}/playback`, {method:"POST",signal});
    const runtime = await getApiRuntime();
    return {...result,url:new URL(result.url,runtime.apiUrl).toString()};
  },
  audioPreview: (id: string, start:number, end:number, signal?:AbortSignal) =>
    apiFetch<AudioPreview>(`/api/v1/materials/${id}/audio-preview`, {method:"POST",signal,body:JSON.stringify({start,end})}),
  pdfPageCount: (material: Material, signal?: AbortSignal) =>
    apiFetch<{ page_count: number }>(`/api/v1/materials/${encodeURIComponent(material.id)}/pdf-page?material_sha256=${encodeURIComponent(material.sha256)}&material_version=${material.version}`, { signal }),
  pdfPage: (material: Material, page: number, signal?: AbortSignal) =>
    apiBlob(`/api/v1/materials/${encodeURIComponent(material.id)}/pdf-page?material_sha256=${encodeURIComponent(material.sha256)}&material_version=${material.version}&page=${page}`, signal),
  download: (id: string, filename: string, signal?: AbortSignal) =>
    downloadApiFile(
      `/api/v1/materials/${encodeURIComponent(id)}/content`,
      filename,
      signal,
      10 * 1024 * 1024,
    ),
  citation: (materialId: string, id: string, signal?: AbortSignal) =>
    apiFetch<Citation>(`/api/v1/materials/${materialId}/citations/${id}`, {
      signal,
    }),
  save: (id: string, body: CitationRequest, citationId?: string) =>
    apiFetch<Citation>(
      `/api/v1/materials/${id}/citations${citationId ? `/${citationId}` : ""}`,
      { method: citationId ? "PATCH" : "POST", body: JSON.stringify(body) },
    ),
  upload: (projectId: string, file: File, sourcePath?: string) => {
    const form = new FormData();
    form.append("file", file);
    return apiUpload<Material>(`/api/v1/projects/${projectId}/materials${sourcePath ? `?source_path=${encodeURIComponent(sourcePath)}` : ""}`, form);
  },
};
