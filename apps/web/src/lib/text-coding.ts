import { apiFetch } from "./api-client";
import { downloadApiFile, saveGeneratedFile } from "./api-download";
import type { Location } from "./material-types";

export type AttributeColumn = {name:string;kind:"number"|"text"};
export type AttributeBindingRequest = {id?:string;dataset_id:string;target:"case"|"file";key_column:string;key_kind:"sample_id"|"file_id"|"relative_path";group_id?:string;columns:AttributeColumn[];file_choices:Record<string,string>;case_choices?:Record<string,string>};
export type AttributeBinding = {id:string;source_id:string;source_sha256:string;source_name:string;request:AttributeBindingRequest;rows:{key:string;title:string;values:Record<string,string|number|null>}[]};
export type AttributeCondition = {binding_id:string;column:string;operator:"eq"|"gte"|"lte"|"contains"|"starts_with";value:string|number};
export type AttributeFilter = {mode:"all"|"any";conditions:AttributeCondition[]};
export type AttributeCatalog = {tables:{id:string;title:string}[];cases:CodingCaseScope[];files:{key:string;title:string;relative_path:string|null;version:number|null;sha256:string}[]};
export type AttributePreview = {binding:AttributeBinding;total_rows:number;matched_count:number;unmatched:{row:number;value:string;reason:string}[]};
export type CodingScope = {segment_ids:string[];current_segment_ids:string[];case_count:number;file_count:number;annotation_count:number;scope_label:string;description:string};

export type ThemeColor =
  "green" | "blue" | "amber" | "purple" | "rose" | "neutral";
export type CodingTheme = {
  id: string;
  name: string;
  color: ThemeColor;
  color_rgb?: string | null;
  is_codable?: boolean;
  merged_ids?: string[];
  parent_id: string | null;
  memo: string;
};
export type SignalLocation = {
  kind: "signal_time";
  start_seconds: number;
  end_seconds: number;
  channel_ids: string[];
  window_index?: number | null;
};
export type AnnotationLocation = Location | SignalLocation;
type SegmentBase = {
  id: string;
  coder_id?: string | null;
  coder_name?: string | null;
  origin?: "manual" | "ai_accepted" | "mixed" | "legacy_ai" | null;
  sample_id?: string | null;
  sample_title?: string | null;
  sample_collection_id?: string | null;
  theme_ids: string[];
  memo: string;
  filename: string;
  task_id?: string | null;
  source_fingerprint?: string | null;
};
export type MaterialCodedSegment = SegmentBase & {
  citation_id: string;
  citation_revision: number;
  citation_sha256: string;
  material_id: string;
  material_sha256: string;
  material_version: number;
  locator: Location;
  dataset_id?: null;
  dataset_sha256?: null;
};
export type SignalCodedSegment = SegmentBase & {
  locator: SignalLocation;
  dataset_id: string;
  dataset_sha256: string;
  material_id?: null;
  material_sha256?: null;
  material_version?: null;
  citation_id?: null;
  citation_revision?: null;
  citation_sha256?: null;
};
export type CodedSegment = MaterialCodedSegment | SignalCodedSegment;
export type TextCodedSegment = MaterialCodedSegment & {
  locator: Extract<Location, { kind: "text" }>;
};
export function isTextCodedSegment(segment: CodedSegment): segment is TextCodedSegment {
  return segment.locator.kind === "text" && !!segment.material_id;
}
export type CodingDocument = {
  ai_question?: string;
  schema_version: 1;
  kind: "coding";
  title: string;
  themes: CodingTheme[];
  segments: CodedSegment[];
  undo_stack: number[];
  attribute_bindings?: AttributeBinding[];
  attribute_filter?: AttributeFilter;
};
export type CodingCaseScope = {
  key: string; group_id: string; collection_id: string; sample_id: string;
  title: string; group_title: string;
};
export type CodingState = {
  current_coder?: {id:string;name:string};
  local_coders?: {id:string;name:string}[];
  id: string | null;
  project_id: string;
  revision: number;
  payload: CodingDocument;
  case_scopes?: CodingCaseScope[];
  segment_cases?: Record<string, string>;
  transcription_status?: Record<string, "current" | "historical" | "unavailable">;
  transcription_info?: Record<string, {status: "current" | "historical" | "unavailable";run_id?:string;text_id?:string;version?:number}>;
};
export type CodingChange = {
  operation:
    | "add_coder" | "select_coder"
    | "bind_attributes" | "filter_attributes"
    | "question"
    | "add_theme"
    | "edit_theme"
    | "merge_theme"
    | "delete_theme"
    | "move_theme"
    | "import_codebook"
    | "add_segment"
    | "edit_segment"
    | "remove_segment"
    | "undo";
  theme_id?: string;
  coder_id?: string;
  attribute_binding?: AttributeBindingRequest;
  attribute_filter?: AttributeFilter;
  question?: string;
  target_id?: string;
  parent_id?: string | null;
  name?: string;
  color?: ThemeColor;
  color_rgb?: string | null;
  is_codable?: boolean;
  direction?: "up" | "down";
  codebook_xml?: string;
  memo?: string;
  segment_id?: string;
  theme_ids?: string[];
  material_id?: string;
  material_sha256?: string;
  material_version?: number;
  dataset_id?: string;
  dataset_sha256?: string;
  task_id?: string | null;
  source_fingerprint?: string | null;
  sample_id?: string;
  sample_collection_id?: string;
  proposal_id?: string;
  locator?: AnnotationLocation;
};
type SelectionRequest = { annotation_id?: string; revision: number; material_id: string; material_sha256: string; material_version: number; locator: AnnotationLocation; purpose: string; share_selection: true; share_previous_context?: boolean; task_id: string | null };
export type RelationKind = "all" | "equal" | "contains" | "overlap" | "touching" | "separate";
export type RelationsRequest = {revision:number;case_collection_id?:string;case_sample_id?:string;coder_id?:string;theme_ids:string[];pair?:string[];relation:RelationKind;page:number};
export type CodingRelation = {theme_a:string;theme_b:string;relation:Exclude<RelationKind,"all">;overlap:number;distance:number;left:{segment:TextCodedSegment;start:number;end:number};right:{segment:TextCodedSegment;start:number;end:number}};
export type CodingRelations = {revision:number;scope:CodingScope;coders:{id:string;name:string}[];themes:{id:string;name:string}[];matrix:number[][];cooccurrences:number;comparable_annotations:number;unrecorded_annotations:number;relations:CodingRelation[];total_relations:number;page:number;page_size:number;unit:string};
export type AnnotationTarget = Pick<CodedSegment,"material_id"|"material_sha256"|"material_version"|"dataset_id"|"dataset_sha256"|"source_fingerprint"|"task_id"|"filename"|"locator"> & {id?:string;comparison?:{side:"a"|"b";name:string;regions:Extract<AnnotationLocation,{kind:"region"}>[]}[]};
export type AgreementModality = "text"|"image"|"audio"|"video";
export type AgreementRequest = {modality?:AgreementModality;revision:number;coder_a:string;coder_b:string;theme_id:string;material_id?:string;case_key?:string;page:number};
export type AgreementChoices = {coders:{id:string;name:string}[];files:{id:string;name:string;version:number;kind:AgreementModality}[];cases:CodingCaseScope[];excluded:string[]};
export type AgreementNumbers = {both:number;only_a:number;only_b:number;neither:number|null;total:number|null;agreement:number|null;coded_overlap:number|null;kappa:number|null};
export type CodingAgreement = {modality:AgreementModality;revision:number;scope_label:string;unit:string;saved_at?:string;request:AgreementRequest;coder_a:{id:string;name:string};coder_b:{id:string;name:string};theme:{id:string;name:string};summary:AgreementNumbers|null;sources:(AgreementNumbers&{filename:string;material_id:string;material_version:number;page:number|null;start:number;end:number|null;unit?:string;width?:number;height?:number;duration_missing?:boolean})[];differences:{kind:"only_a"|"only_b";start?:number;end?:number;pixels?:number[];area?:number;target:AnnotationTarget}[];total_differences:number;page_size:number;excluded:string[];unrecorded:number};
export const codingApi = {
  agreementChoices: (project:string,signal?:AbortSignal) => apiFetch<AgreementChoices>(`/api/v1/projects/${project}/coding/agreement/choices`,{signal}),
  agreement: (project:string,body:AgreementRequest,signal?:AbortSignal) => apiFetch<CodingAgreement>(`/api/v1/projects/${project}/coding/agreement`,{method:"POST",body:JSON.stringify(body),signal}),
  exportAgreement: async (project:string,body:AgreementRequest,signal:AbortSignal,snapshot?:string) => {
    const data=await apiFetch<{csv:string;filename:string}>(`/api/v1/projects/${project}/coding/agreement/export${snapshot?`?snapshot_id=${snapshot}`:""}`,{method:"POST",body:JSON.stringify(body),signal});
    return saveGeneratedFile(new Blob([data.csv],{type:"text/csv;charset=utf-8"}),data.filename,signal,undefined,"zh-CN");
  },
  relations: (project:string,body:RelationsRequest,signal?:AbortSignal) => apiFetch<CodingRelations>(`/api/v1/projects/${project}/coding/relations`,{method:"POST",body:JSON.stringify(body),signal}),
  exportRelations: async (project:string,body:RelationsRequest,format:"matrix"|"relations",signal:AbortSignal) => {
    const data=await apiFetch<{csv:string;filename:string}>(`/api/v1/projects/${project}/coding/relations/export?format=${format}`,{method:"POST",body:JSON.stringify(body),signal});
    return saveGeneratedFile(new Blob([data.csv],{type:"text/csv;charset=utf-8"}),data.filename,signal,undefined,"zh-CN");
  },
  attributeCatalog: (project:string,signal?:AbortSignal) => apiFetch<AttributeCatalog>(`/api/v1/projects/${project}/coding/attributes/catalog`,{signal}),
  attributeColumns: (project:string,dataset:string,signal?:AbortSignal) => apiFetch<{columns:{name:string;polars_type:string}[]}>(`/api/v1/projects/${project}/coding/attributes/tables/${dataset}`,{signal}),
  attributePreview: (project:string,body:AttributeBindingRequest) => apiFetch<AttributePreview>(`/api/v1/projects/${project}/coding/attributes/preview`,{method:"POST",body:JSON.stringify(body)}),
  scope: (project:string,revision:number,scope?:CodingCaseScope,signal?:AbortSignal) => apiFetch<CodingScope>(`/api/v1/projects/${project}/coding/scope?revision=${revision}${scope?`&case_collection_id=${scope.collection_id}&case_sample_id=${encodeURIComponent(scope.sample_id)}`:""}`,{signal}),
  selectionSummary: (project: string, body: SelectionRequest, signal?: AbortSignal) =>
    apiFetch<{ summary: string; evidence: string; previous_context?:string }>(`/api/v1/projects/${project}/coding/selection-summary`, { method: "POST", body: JSON.stringify(body), signal }),
  suggestThemes: (project: string, body: SelectionRequest, signal?: AbortSignal) =>
    apiFetch<{ proposal_id: string; theme_ids: string[]; suggestions: { theme_id: string; reason: string; evidence: string }[]; previous_context?:string }>(`/api/v1/projects/${project}/coding/theme-suggestions`, { method: "POST", body: JSON.stringify(body), signal }),
  sampleSummary: (project: string, revision: number, taskId?: string, scope?: CodingCaseScope) =>
    apiFetch<{ id: string; sample_count: number; unassociated_segments: number; excluded_transcription_segments?: number; scope_label: string }>(
      `/api/v1/projects/${project}/coding/summary-dataset`,
      { method: "POST", body: JSON.stringify({ revision, task_id: taskId || null, case_collection_id: scope?.collection_id, case_sample_id: scope?.sample_id }) },
    ),
  report: (project: string, segment: string, revision: number) =>
    apiFetch<{ id: string }>(
      `/api/v1/projects/${project}/coding/segments/${segment}/report`,
      { method: "POST", body: JSON.stringify({ revision }) },
    ),
  read: (project: string, signal?: AbortSignal) =>
    apiFetch<CodingState>(`/api/v1/projects/${project}/coding`, { signal }),
  summarySnapshot: (project: string, dataset: string, signal?: AbortSignal) =>
    apiFetch<CodingState>(`/api/v1/projects/${project}/coding/summary/${dataset}/snapshot`, { signal }),
  change: (project: string, revision: number, change: CodingChange) =>
    apiFetch<CodingState>(`/api/v1/projects/${project}/coding`, {
      method: "POST",
      body: JSON.stringify({ revision, ...change }),
    }),
  export: (project: string, format: "csv" | "json" | "summary_csv" | "qdc", signal?: AbortSignal, scope?: CodingCaseScope) =>
    downloadApiFile(
      `/api/v1/projects/${project}/coding/export?format=${format}${format === "summary_csv" && scope ? `&case_collection_id=${scope.collection_id}&case_sample_id=${encodeURIComponent(scope.sample_id)}` : ""}`,
      format === "qdc" ? "代码本.qdc" : format === "summary_csv" ? `主题汇总 · ${scope ? `案例 · ${scope.title}` : "全项目"}.csv` : `标注.${format}`,
      signal,
      16 * 1024 * 1024,
    ),
};

export function themePath(theme: CodingTheme, themes: CodingTheme[]) {
  const parts = [theme.name], seen = new Set([theme.id]);
  let parent = theme.parent_id;
  while (parent && !seen.has(parent)) {
    seen.add(parent);
    const item = themes.find(value => value.id === parent);
    if (!item) break;
    parts.unshift(item.name); parent = item.parent_id;
  }
  const same = themes.filter(value => value.name === theme.name && value.parent_id === theme.parent_id);
  return parts.join(" / ") + (same.length > 1 ? ` · ${same.findIndex(value => value.id === theme.id) + 1}` : "");
}
