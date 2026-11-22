/** Synthetic metadata only; no server, scientific result or Provider evidence. */
import { blankProtocol } from "./research-protocol";
import type { ResearchContext } from "./research-types";
import type { ProtocolSnapshot } from "./research-protocol-types";

export const protocolTestId = (digit: string) =>
  `${digit.repeat(8)}-${digit.repeat(4)}-${digit.repeat(4)}-${digit.repeat(4)}-${digit.repeat(12)}`;
export function protocolTestFixture(): {
  context: ResearchContext;
  snapshot: ProtocolSnapshot;
} {
  const context: ResearchContext = {
    version: "research-context-v1",
    workspace_id: protocolTestId("1"),
    session_id: protocolTestId("2"),
    workspace_revision: 0,
    workspace_epoch: 0,
    session_revision: 0,
    session_epoch: 0,
    attachment_revision: 1,
    source_version: "a".repeat(64),
    artifacts: [],
    datasets: [
      {
        dataset_id: protocolTestId("3"),
        project_id: protocolTestId("4"),
        sha256: "b".repeat(64),
        schema_sha256: "c".repeat(64),
        reported_row_count: null,
        fields: ["x", "group"].map((name) => ({
          reference: {
            dataset_id: protocolTestId("3"),
            dataset_sha256: "b".repeat(64),
            schema_sha256: "c".repeat(64),
            name,
          },
          dtype: name === "x" ? "Float64" : "String",
          inferred_type: name === "x" ? "numeric" : "categorical",
          role: null,
          semantic_type: null,
        })),
      },
    ],
  };
  const protocol = {
    ...blankProtocol(context, "  描述原始样本  "),
    mode: "description" as const,
    study_design: "descriptive" as const,
    estimand: "summary" as const,
    unit_of_analysis: "one synthetic row",
    variables: [
      {
        reference: context.datasets[0].fields[0].reference,
        role: "analysis" as const,
      },
    ],
    candidate_methods: ["descriptive"],
    reporting: {
      alpha: 0.05,
      confidence_level: 0.95,
      alternative: "two_sided" as const,
      comparison_order: "recorded_engine_order" as const,
    },
  };
  const version = {
    version: 1 as const,
    owner_id: protocolTestId("5"),
    session_id: context.session_id,
    revision: 1,
    version_id: protocolTestId("6"),
    context_id: protocolTestId("7"),
    key_hash: "d".repeat(64),
    request_hash: "e".repeat(64),
    protocol_hash: "f".repeat(64),
    protocol,
    context,
    attachments: [],
  };
  const activation = {
    version: 1 as const,
    owner_id: version.owner_id,
    session_id: context.session_id,
    activation_id: protocolTestId("8"),
    version_id: version.version_id,
    protocol_hash: version.protocol_hash,
    generation: 1,
    action: "confirm" as const,
    key_hash: "d".repeat(64),
    request_hash: "e".repeat(64),
  };
  return { context, snapshot: { version, activation } };
}
