/** UI derivations only. The service remains the authority for every new action. */
import type {
  ProtocolDocument,
  ProtocolHistory,
  ProtocolSnapshot,
} from "./research-protocol-types";
import type { ResearchContext, ResearchPlanV2 } from "./research-types";

export const protocolMethods = [
  "descriptive",
  "pearson",
  "spearman",
  "kendall",
  "welch_t",
  "student_t",
  "mann_whitney",
  "paired_t",
  "wilcoxon",
  "one_way_anova",
  "welch_anova",
  "kruskal_wallis",
  "linear_regression",
] as const;
export const protocolDiagnostics = [
  "linearity",
  "influence",
  "monotonicity",
  "group_normality",
  "equal_variance",
  "group_distribution",
  "difference_normality",
  "difference_symmetry",
  "homoscedasticity",
  "residual_normality",
] as const;

export function protocolContentKey(document: ProtocolDocument): string {
  const ordered = (value: unknown): unknown =>
    Array.isArray(value)
      ? value.map(ordered)
      : value !== null && typeof value === "object"
        ? Object.fromEntries(
            Object.entries(value)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([key, item]) => [key, ordered(item)]),
          )
        : value;
  return JSON.stringify(ordered(document));
}

export function blankProtocol(
  context: ResearchContext,
  question: string,
): ProtocolDocument {
  return {
    version: "research-protocol-v1",
    source_version: context.source_version,
    research_question: question,
    mode: "unknown",
    study_design: "unknown",
    unit_of_analysis: null,
    population_and_sample: null,
    variables: [],
    estimand: "unknown",
    independence: "unknown",
    group_definitions: [],
    pairing: null,
    cluster_structure: "unknown",
    time_structure: "unknown",
    missingness_context: null,
    missing_value_policy: "unknown",
    candidate_methods: [],
    diagnostics: [],
    assumptions: [],
    multiplicity: null,
    reporting: null,
    prediction: null,
    analysis_intent: "unknown",
    known_limitations: [],
    unresolved_questions: [],
  };
}

export function currentProtocol(
  history: ProtocolHistory | undefined,
  context: ResearchContext,
): ProtocolSnapshot | null {
  const activation = history?.activations.at(-1);
  if (
    !activation ||
    activation.action !== "confirm" ||
    activation.session_id !== context.session_id
  )
    return null;
  const version = history?.versions.find(
    (item) =>
      item.version_id === activation.version_id &&
      item.protocol_hash === activation.protocol_hash,
  );
  if (
    !version ||
    version.session_id !== context.session_id ||
    version.protocol.source_version !== context.source_version
  )
    return null;
  return { version, activation };
}

export function protocolPlan(
  snapshot: ProtocolSnapshot,
  context: ResearchContext,
  method: string,
  references: Record<string, string> = {},
): ResearchPlanV2 {
  const doc = snapshot.version.protocol;
  if (
    doc.source_version !== context.source_version ||
    !doc.candidate_methods.includes(method) ||
    !protocolMethods.includes(method as (typeof protocolMethods)[number])
  )
    throw new Error("RESEARCH_PROTOCOL_METHOD_UNAVAILABLE");
  const fields = doc.variables.map((item) => item.reference);
  if (
    doc.pairing?.pair_id &&
    !fields.some(
      (item) =>
        item.dataset_id === doc.pairing?.pair_id?.dataset_id &&
        item.name === doc.pairing.pair_id.name,
    )
  )
    fields.push(doc.pairing.pair_id);
  const source = context.datasets.find(
    (item) => item.dataset_id === fields[0]?.dataset_id,
  );
  if (!source || fields.some((item) => item.dataset_id !== source.dataset_id))
    throw new Error("RESEARCH_PROTOCOL_SOURCE_UNAVAILABLE");
  const names = (role: string) =>
    doc.variables
      .filter((item) => item.role === role)
      .map((item) => item.reference.name);
  const config: Record<string, unknown> = { method_id: method };
  if (["descriptive", "pearson", "spearman", "kendall"].includes(method))
    config.variables = names("analysis");
  else if (["paired_t", "wilcoxon"].includes(method)) {
    config.variables = names("outcome");
    config.paired = true;
  } else if (method === "linear_regression") {
    const categorical = names("predictor").filter(
      (name) =>
        source.fields.find((item) => item.reference.name === name)
          ?.inferred_type !== "numeric",
    );
    if (categorical.some((name) => !references[name]?.trim()))
      throw new Error("RESEARCH_PROTOCOL_REFERENCE_REQUIRED");
    config.variables = names("predictor").filter(
      (name) => !categorical.includes(name),
    );
    config.categorical_variables = categorical;
    config.reference_categories = Object.fromEntries(
      categorical.map((name) => [name, references[name]]),
    );
    config.target = names("outcome")[0];
  } else {
    config.variables = names("outcome");
    config.group_variable = names("group")[0];
  }
  if (doc.reporting) {
    config.alternative = doc.reporting.alternative;
    config.confidence_level = doc.reporting.confidence_level;
  }
  if (doc.missing_value_policy !== "unknown")
    config.missing_value_policy = doc.missing_value_policy;
  return {
    version: "research-plan-v2",
    source_version: context.source_version,
    title: Array.from(doc.research_question).slice(0, 160).join(""),
    research_question: doc.research_question,
    protocol_version_id: snapshot.version.version_id,
    protocol_hash: snapshot.version.protocol_hash,
    protocol_generation: snapshot.activation.generation,
    steps: [
      {
        id: "analysis",
        capability: "statistics",
        dataset_id: source.dataset_id,
        dataset_sha256: source.sha256,
        fields,
        depends_on: [],
        configuration: config,
      },
    ],
  };
}
