/** Explicit public protocol versions. Metadata readiness is not execution proof. */
import type { ResearchContext, ResearchFieldReference } from "./research-types";

export type ProtocolRole =
  | "analysis"
  | "outcome"
  | "group"
  | "predictor"
  | "target"
  | "exposure"
  | "pairing_id"
  | "time"
  | "cluster"
  | "unknown";
export type ProtocolDesign =
  | "unknown"
  | "descriptive"
  | "association"
  | "independent_groups"
  | "paired"
  | "one_way_groups"
  | "cross_sectional"
  | "prediction";
export type ProtocolEstimand =
  | "unknown"
  | "summary"
  | "association"
  | "mean_difference"
  | "distribution_difference"
  | "paired_mean_difference"
  | "paired_distribution_shift"
  | "group_difference"
  | "conditional_association"
  | "prediction"
  | "causal_effect";
export type ProtocolDocument = {
  version: "research-protocol-v1";
  source_version: string;
  research_question: string;
  mode: "unknown" | "description" | "inference" | "prediction";
  study_design: ProtocolDesign;
  unit_of_analysis: string | null;
  population_and_sample: string | null;
  variables: { reference: ResearchFieldReference; role: ProtocolRole }[];
  estimand: ProtocolEstimand;
  independence: "unknown" | "independent" | "paired" | "clustered" | "repeated";
  group_definitions: string[];
  pairing: {
    layout: "unknown" | "wide_same_row" | "long_pair_id";
    description: string | null;
    conditions: string[];
    pair_id: ResearchFieldReference | null;
  } | null;
  cluster_structure: "unknown" | "none" | "present";
  time_structure: "unknown" | "none" | "present";
  missingness_context: string | null;
  missing_value_policy:
    "unknown" | "listwise_delete" | "pairwise_delete" | "error_on_missing";
  candidate_methods: string[];
  diagnostics: string[];
  assumptions: string[];
  multiplicity: {
    policy:
      | "unknown"
      | "single_prespecified"
      | "family_adjusted"
      | "exploratory_unadjusted";
    family_id: string | null;
    planned_comparisons: number | null;
    adjustment: "unknown" | "none" | "holm" | "bonferroni";
  } | null;
  reporting: {
    alpha: number;
    confidence_level: number;
    alternative: "two_sided" | "less" | "greater";
    comparison_order: "recorded_engine_order";
  } | null;
  prediction: {
    task: "unknown" | "regression" | "classification";
    split_strategy: string | null;
    primary_metric: string | null;
  } | null;
  analysis_intent: "unknown" | "exploratory" | "confirmatory";
  known_limitations: string[];
  unresolved_questions: string[];
};
export type ProtocolAssessment = {
  status: "ready" | "clarification" | "blocked";
  issues: {
    code: string;
    field: string;
    severity: "clarification" | "blocked";
  }[];
  required_diagnostics: string[];
  alternatives: string[];
  limitations: string[];
};
export type ProtocolVersion = {
  version: 1;
  owner_id: string;
  session_id: string;
  revision: number;
  version_id: string;
  context_id: string;
  key_hash: string;
  request_hash: string;
  protocol_hash: string;
  protocol: ProtocolDocument;
  context: ResearchContext;
  attachments: Record<string, unknown>[];
};
export type ProtocolActivation = {
  version: 1;
  owner_id: string;
  session_id: string;
  activation_id: string;
  version_id: string;
  protocol_hash: string;
  generation: number;
  action: "confirm" | "revoke";
  key_hash: string;
  request_hash: string;
};
export type ProtocolHistory = {
  versions: ProtocolVersion[];
  activations: ProtocolActivation[];
};
export type ProtocolSnapshot = {
  version: ProtocolVersion;
  activation: ProtocolActivation;
};
export type ProtocolBinding = {
  version_id: string;
  protocol_hash: string;
  generation: number;
};

export type P4ResultFacts = {
  version: "p4-result-facts-v1";
  computed: false;
  binding: {
    acceptance_id: string;
    plan_version_id: string;
    step_id: string;
    full_result_hash: string;
    protocol: Record<string, unknown>;
    citation: Record<string, unknown>;
    root: Record<string, unknown>;
  };
  method: { id: string; version: string };
  requested: {
    alternative: string;
    confidence_level: number;
    missing_value_policy: string;
    alpha: number | null;
  };
  execution: {
    alternative: string;
    missing_semantics: string;
    new_work_supported: boolean;
    capability_issues: string[];
    per_variable_exclusions_are_engine_records: true;
    comparison_order: string[] | null;
  };
  multiplicity: {
    declared: ProtocolDocument["multiplicity"];
    plan_statistical_steps: number;
    adjustment_executed: false;
    study_family_verified: false;
  };
  diagnostics: {
    planned: string[];
    status: "NOT_VERIFIED";
    recorded_count: number;
    recorded_pass_is_not_assumption_proof: true;
  };
  facts: {
    path: (string | number)[];
    value: string | number | boolean | null;
  }[];
  availability: {
    ci: "available" | "unavailable";
    effect: "available" | "unavailable";
    p_value: "available" | "unavailable";
  };
  wording: string[];
  projection_hash: string;
};
