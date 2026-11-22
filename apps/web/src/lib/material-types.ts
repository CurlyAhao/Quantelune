export type Material = {
  id: string;
  project_id: string;
  filename: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  version: number;
  metadata: {
    source_path?: string;
    source_dataset_id?: string;
    annotation_material_ids?: string[];
    width?: number;
    height?: number;
    duration_seconds?: number | null;
    duration_status?: string;
    extraction_status: string;
    extraction_code?: string | null;
    file_version?: { root_id: string; parent_id: string; parent_sha256: string };
    survey_answer?: {group_id:string;case_id:string;question:string;source_id:string;source_sha256:string;source_name:string;row:number;sheet_name?:string|null;version:number};
  };
};
export type TextPage = { page: number; text: string; sha256: string };
export type Location =
  | {
      kind: "text";
      page: number;
      start: number;
      end: number;
      text_sha256: string;
      quote: string;
    }
  | {
      kind: "region";
      x: number;
      y: number;
      width: number;
      height: number;
      note: string;
    }
  | { kind: "time"; start_seconds: number; end_seconds: number; point?: boolean; note: string; transcription?: { run_id: string; text_id: string; version: number; segment_ids: number[]; quote: string } | null };
export type CitationReference = {
  citation_id: string;
  revision: number;
  citation_sha256: string;
};
export type Citation = {
  id: string;
  project_id: string;
  material_id: string;
  filename: string;
  material_sha256: string;
  material_version: number;
  revision: number;
  reviewed: boolean;
  locator: Location;
  location_status: string;
  perception: "unavailable";
  citation_sha256: string;
};
export type CitationRequest = {
  material_sha256: string;
  material_version: number;
  reviewed: boolean;
  expected_revision?: number;
  locator: Location;
};
export function reference(citation: Citation): CitationReference {
  return {
    citation_id: citation.id,
    revision: citation.revision,
    citation_sha256: citation.citation_sha256,
  };
}
/** Returns null for a boundary inside a surrogate pair; server uses code points. */
export function codepointOffset(
  text: string,
  utf16Offset: number,
): number | null {
  if (utf16Offset < 0 || utf16Offset > text.length) return null;
  if (
    utf16Offset > 0 &&
    utf16Offset < text.length &&
    /[\uD800-\uDBFF]/.test(text[utf16Offset - 1]) &&
    /[\uDC00-\uDFFF]/.test(text[utf16Offset])
  )
    return null;
  return Array.from(text.slice(0, utf16Offset)).length;
}
