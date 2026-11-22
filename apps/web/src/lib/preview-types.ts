import type { MLExperimentCreateRequest, StatisticalRunRequest } from "./types";
import type { CitationReference } from "./material-types";
import type { MessageSpec } from "./i18n-messages";
import type { AdviceTarget } from "./draft-advice";

export type PreviewScope = {
  project_id: string;
  dataset_id?: string | null;
  columns?: string[];
  dataset_sha256?: string | null;
  schema_sha256?: string | null;
  statistical_run_id?: string | null;
  ml_run_id?: string | null;
  statistical_config?: StatisticalRunRequest | null;
  ml_config?: MLExperimentCreateRequest | null;
  material_references?: CitationReference[];
  session_id?: string | null;
  session_revision?: number | null;
};

export type PreviewContext = {
  scope: PreviewScope;
  categories: Record<string, unknown>;
  disclosure_hash: string;
};

export type PreviewTurn = {
  turn_id: string;
  request_id?: string;
  status: string;
  error_code: string | null;
  evidence: Record<string, unknown>[];
};

export function sessionSourceKey(scope: PreviewScope): string {
  return JSON.stringify([
    scope.project_id,
    scope.dataset_id ?? null,
    scope.dataset_sha256 ?? null,
    scope.schema_sha256 ?? null,
    [...(scope.columns ?? [])].sort(),
    scope.statistical_run_id ?? null,
    scope.ml_run_id ?? null,
    scope.material_references ?? [],
  ]);
}

export type ReadEvidenceBinding = {
  tool:
    | "project_context"
    | "field_explanation"
    | "quality_summary"
    | "result_facts";
  category: "project" | "schema" | "quality" | "results";
  disclosure_hash: string;
  evidence_digest: string;
  source: Pick<
    PreviewScope,
    "project_id" | "dataset_id" | "dataset_sha256" | "schema_sha256" | "columns"
  >;
};

export type ReadEvidence = {
  state: "observed";
  binding: ReadEvidenceBinding;
} & (
  | {
      kind: "project_context";
      summary: { title: string; description: string | null };
    }
  | {
      kind: "field_explanation";
      summary: {
        dataset_sha256: string;
        columns: { name: string; dtype: string; inferred_type: string }[];
      };
    }
  | {
      kind: "quality_summary";
      summary: { row_count: number; null_counts: Record<string, number> };
    }
);

export type PreviewPublication = {
  scope: PreviewScope;
  page: string;
  draft?: string;
  pageMessage?: MessageSpec;
  draftMessage?: MessageSpec;
  applyStatistics?: (config: StatisticalRunRequest) => void;
  applyML?: (config: MLExperimentCreateRequest) => void;
  adviceKind?: "plot" | "coding";
  adviceDraftId?: string;
  adviceQuestion?: string;
  saveAdviceQuestion?: (question: string) => Promise<void>;
  prepareAdvice?: () => Promise<AdviceTarget>;
  adviceApplied?: () => Promise<void>;
};

export function statisticalColumns(config: StatisticalRunRequest): string[] {
  const strings = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((v) => typeof v === "string") : [];
  return [
    ...new Set([
      ...strings(config.variables),
      ...strings(config.categorical_variables),
      ...Object.keys(config.reference_categories ?? {}),
      ...(config.group_variable ? [config.group_variable] : []),
      ...(config.target ? [config.target] : []),
      ...(typeof config.evaluation?.actual?.name === "string"
        ? [config.evaluation.actual.name]
        : []),
      ...(typeof config.evaluation?.predicted?.name === "string"
        ? [config.evaluation.predicted.name]
        : []),
    ]),
  ];
}

export function mlColumns(config: MLExperimentCreateRequest): string[] {
  return [
    ...new Set([
      ...config.features,
      ...(config.categorical_features ?? []),
      config.target,
      ...(config.group_variable ? [config.group_variable] : []),
      ...(config.time_variable ? [config.time_variable] : []),
    ]),
  ];
}
