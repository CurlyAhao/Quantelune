/** Public research documents only. These types do not authorize execution or egress. */
import type { ProtocolSnapshot } from "./research-protocol-types";

export type ResearchRunState =
  | "queued"
  | "running"
  | "waiting_approval"
  | "paused"
  | "cancel_requested"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type ResearchPlannerOutcome = {
  version: 1;
  outcome: "awaiting_input";
  run_id: string;
  attempt_id: string;
  receipt_sha256: string;
  advisory: string;
  execution_kinds: string[];
  business_completion: "NOT_VERIFIED";
  model_quality: "NOT_VERIFIED";
};

export type ResearchWorkspace = {
  id: string;
  project_id: string | null;
  title: string;
  revision: number;
  lifecycle_epoch: number;
  is_deleted: boolean;
  event_sequence: number;
  event_head_hash: string;
};

export type ResearchSession = {
  id: string;
  workspace_id: string;
  title: string;
  revision: number;
  lifecycle_epoch: number;
  is_archived: boolean;
  is_deleted: boolean;
};

export type ResearchAttachment = {
  id: string;
  target_kind: string;
  target_id: string;
  source_project_id: string;
  target_sha256: string | null;
  active: boolean;
};

export type ResearchRun = {
  id: string;
  session_id: string;
  kind: string;
  input_hash: string;
  state: ResearchRunState;
  revision: number;
  attempt_count: number;
  domain_kind: string | null;
  domain_run_id: string | null;
  completion_hash: string | null;
};

export type ResearchAttempt = {
  id: string;
  number: number;
  state: ResearchRunState;
  retry_key: string | null;
  base_revision: number | null;
  domain_kind: string | null;
  domain_run_id: string | null;
  completion_hash: string | null;
};

export type ResearchArtifact = {
  id: string;
  run_id: string;
  project_id: string;
  kind: string;
  media_type: string;
  byte_size: number;
  sha256: string;
  logical_hash: string;
  provenance_json: string;
};

/** Parse payload_json only with an event-specific validator in the UI. */
export type ResearchEvent = {
  sequence: number;
  entity_kind: string;
  entity_id: string;
  event_type: string;
  payload_json: string;
  logical_hash: string;
  previous_hash: string;
  chain_hash: string;
  hash_version: string;
  occurred_at: string;
};

export type ResearchMessage = {
  id: string;
  session_id: string;
  role: string;
  logical_hash: string;
  content_json: string;
};

export type ResearchFieldReference = {
  dataset_id: string;
  dataset_sha256: string;
  schema_sha256: string;
  name: string;
  variable_id?: string | null;
};

export type ResearchDatasetContext = {
  dataset_id: string;
  project_id: string;
  sha256: string;
  schema_sha256: string;
  reported_row_count: number | null;
  fields: {
    reference: ResearchFieldReference;
    dtype: string;
    inferred_type: string;
    role: string | null;
    semantic_type: string | null;
  }[];
};

export type ResearchContext = {
  version: "research-context-v1";
  workspace_id: string;
  session_id: string;
  workspace_revision: number;
  workspace_epoch: number;
  session_revision: number;
  session_epoch: number;
  attachment_revision: number;
  source_version: string;
  datasets: ResearchDatasetContext[];
  artifacts: {
    artifact_id: string;
    run_id: string;
    project_id: string;
    sha256: string;
    logical_hash: string;
    kind: string;
    byte_size: number;
  }[];
};

export type ResearchCapabilityId =
  "inspect" | "profile" | "quality" | "descriptive" | "correlation";

export type ResearchPlanStep = {
  id: string;
  capability: ResearchCapabilityId;
  dataset_id: string;
  dataset_sha256: string;
  fields?: ResearchFieldReference[];
  depends_on?: string[];
  configuration?: Record<string, unknown>;
};

export type ResearchPlan = {
  version?: "research-plan-v1";
  source_version: string;
  title: string;
  research_question?: string;
  steps: ResearchPlanStep[];
};

export type ResearchPlanStepV2 = Omit<ResearchPlanStep, "capability"> & {
  capability: "inspect" | "profile" | "quality" | "statistics";
};
export type ResearchPlanV2 = Omit<
  ResearchPlan,
  "version" | "steps" | "research_question"
> & {
  version: "research-plan-v2";
  research_question: string;
  protocol_version_id: string;
  protocol_hash: string;
  protocol_generation: number;
  steps: ResearchPlanStepV2[];
};
export type ResearchAnyPlan = ResearchPlan | ResearchPlanV2;

export type ResearchPlanVersion = {
  version: number;
  owner_id: string;
  session_id: string;
  draft_id: string;
  revision: number;
  idempotency_key: string;
  request_hash: string;
  version_id: string;
  context_id: string;
  plan: ResearchAnyPlan;
  plan_hash: string;
  context: ResearchContext;
  attachments: Record<string, unknown>[];
  protocol?: ProtocolSnapshot;
};

export type ResearchPlanAcceptance = {
  version: number;
  acceptance_id: string;
  request: {
    owner_id: string;
    session_id: string;
    version_id: string;
    plan_hash: string;
    source_version: string;
    key_hash: string;
  };
  plan_document_hash: string;
  context_id: string;
  context_document_hash: string;
  steps: {
    version: number;
    acceptance_id: string;
    step_id: string;
    capability_version: string;
    step: ResearchPlanStep | ResearchPlanStepV2;
  }[];
};

/** Completion and step proofs are returned by the service, never inferred here. */
export type ResearchPlanExecution = {
  acceptance_id: string;
  plan_version_id: string;
  source_version: string;
  revision: number;
  cancel_requested: boolean;
  state: "completed" | "cancel_pending" | "cancelled" | "incomplete";
  steps: {
    step_id: string;
    state: ResearchRunState | "proven" | "not_started" | "dependency_blocked";
    child_run_id: string | null;
    result: unknown;
  }[];
};

/** Server validation enforces source counts, unique columns and raw-data approval. */
export type ResearchDisclosure = {
  question: string;
  purpose: "planning" | "analysis" | "review";
  scope: "metadata_only" | "sanitized_sample" | "raw_data_approved";
  datasets?: { dataset_id: string; columns: string[] }[];
  artifact_ids?: string[];
  rows?: number;
  acknowledge_raw?: boolean;
};

export type ResearchProviderState = {
  revision: number;
  enabled: boolean;
  key_configured: boolean;
  configuration_id: string | null;
  configuration_hash: string | null;
  settings: {
    provider: string;
    model: string;
    pricing_mode?: "off" | "estimate";
    input_per_million: string | null;
    output_per_million: string | null;
    currency: string;
    output_length_mode?: "auto" | "manual";
    max_output_tokens: number | null;
    max_input_bytes: number;
    timeout_ms: number;
    price_ceiling_confirmed: boolean;
    billing_limit_acknowledged: boolean;
  } | null;
  provider_verified: boolean;
};

export type ResearchBudget = {
  currency: "USD";
  cost_unit: "0.00000001";
  accounts: {
    id: string;
    scope: string;
    scope_id: string;
    cost_limit_units: number;
    reserved_units: number | null;
    consumed_units: number | null;
    unpriced_calls?: number;
    attempts: number;
    attempt_limit: number;
  }[];
};

/** The safe summary has no original selection; recover it from admission events. */
export type ResearchApproval = {
  id: string;
  run_id: string;
  state: string;
  request_hash: string;
  expires_at_ms: number;
  scope: ResearchDisclosure["scope"];
  purpose: ResearchDisclosure["purpose"];
  payload_sha256: string;
  source_binding_sha256: string | null;
  configuration_hash: string;
  input_bytes: number;
  reserved_cost_units: number | null;
  currency: "USD";
  cost_unit: "0.00000001";
  provider: string;
  model: string;
  price_verified: boolean;
  pricing_mode?: "reviewed" | "estimated" | "unpriced";
};

export type ResearchCapability = {
  id: ResearchCapabilityId;
  version: string;
  effect: "metadata_read" | "saved_profile_read" | "native_run";
  methods?: string[];
};

export type ResearchRuleProposal =
  | {
      state: "clarification";
      code: string;
      plan: null;
    }
  | {
      state: "proposed";
      plan: ResearchPlan;
      plan_hash: string;
      executed: false;
    };

export type ResearchRevision = { expected_revision: number };
export type ResearchControl = ResearchRevision & {
  action: "pause" | "resume" | "cancel";
  idempotency_key: string;
};
export type ResearchInvocationCreate = {
  source_version: string;
  idempotency_key: string;
  selection: ResearchDisclosure;
};
export type ResearchPlannerCreateV2 = ResearchInvocationCreate & {
  version: "research-planner-v2";
  protocol_version_id: string;
  protocol_hash: string;
  protocol_generation: number;
};
export type ResearchDraftSave = ResearchRevision & {
  plan: ResearchAnyPlan;
  idempotency_key: string;
};
export type ResearchPlanAccept = {
  version_id: string;
  plan_hash: string;
  source_version: string;
  idempotency_key: string;
};
export type ResearchRuleProposalCreate = {
  source_version: string;
  selection: {
    capability: ResearchCapabilityId;
    dataset_id?: string | null;
    field_names?: string[];
    configuration?: Record<string, unknown>;
  };
};
export type ResearchApprovalDecision = {
  request_hash: string;
  decision: "approve" | "deny" | "revoke";
  selection?: ResearchDisclosure | null;
};
