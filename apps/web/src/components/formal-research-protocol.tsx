"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useState } from "react";
import { researchApi } from "@/lib/research-api";
import {
  blankProtocol,
  currentProtocol,
  protocolContentKey,
  protocolDiagnostics,
  protocolMethods,
  protocolPlan,
} from "@/lib/research-protocol";
import type {
  ProtocolAssessment,
  ProtocolDocument,
  ProtocolHistory,
  ProtocolRole,
} from "@/lib/research-protocol-types";
import type { ResearchContext, ResearchPlanV2 } from "@/lib/research-types";
import type { NoParameterMessageKey } from "@/lib/i18n-messages";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import {
  ResearchActionNotice,
  ResearchFailure,
} from "./formal-research-common";
import { useResearchAction } from "./use-research-action";

const lines = (text: string) =>
  text.split("\n").filter((item) => item.trim().length > 0);
const enums = {
  mode: ["unknown", "description", "inference", "prediction"],
  study_design: [
    "unknown",
    "descriptive",
    "association",
    "independent_groups",
    "paired",
    "one_way_groups",
    "cross_sectional",
    "prediction",
  ],
  estimand: [
    "unknown",
    "summary",
    "association",
    "mean_difference",
    "distribution_difference",
    "paired_mean_difference",
    "paired_distribution_shift",
    "group_difference",
    "conditional_association",
    "prediction",
    "causal_effect",
  ],
  independence: ["unknown", "independent", "paired", "clustered", "repeated"],
  missing_value_policy: [
    "unknown",
    "listwise_delete",
    "pairwise_delete",
    "error_on_missing",
  ],
  cluster_structure: ["unknown", "none", "present"],
  time_structure: ["unknown", "none", "present"],
  analysis_intent: ["unknown", "exploratory", "confirmatory"],
} as const;
const roles: ProtocolRole[] = [
  "unknown",
  "analysis",
  "outcome",
  "group",
  "predictor",
  "target",
  "exposure",
  "pairing_id",
  "time",
  "cluster",
];
const issueFields: Record<string, NoParameterMessageKey> = {
  research_question: "research.question",
  variables: "protocol.variables",
  unit_of_analysis: "protocol.field.unit_of_analysis",
  population_and_sample: "protocol.field.population_and_sample",
  study_design: "protocol.field.study_design",
  mode: "protocol.field.mode",
  estimand: "protocol.field.estimand",
  independence: "protocol.field.independence",
  group_definitions: "protocol.field.group_definitions",
  missingness_context: "protocol.field.missingness_context",
  missing_value_policy: "protocol.field.missing_value_policy",
  cluster_structure: "protocol.field.cluster_structure",
  time_structure: "protocol.field.time_structure",
  analysis_intent: "protocol.field.analysis_intent",
  assumptions: "protocol.field.assumptions",
  multiplicity: "protocol.field.multiplicity",
  reporting: "protocol.details",
  diagnostics: "protocol.diagnostics",
  candidate_methods: "protocol.methods",
  pairing: "protocol.pairing",
  prediction: "protocol.field.predictionTask",
};

export function FormalResearchProtocol({
  context,
  history,
  question,
  onQuestion,
  onProposal,
  onBusy,
  onReady,
  changed,
  readOnly,
  newWorkDisabled,
}: {
  context: ResearchContext;
  history: ProtocolHistory;
  question: string;
  onQuestion: (value: string) => void;
  onProposal: (value: ResearchPlanV2) => void;
  onBusy: (busy: boolean) => void;
  changed: () => Promise<unknown>;
  onReady: (ready: boolean) => void;
  readOnly: boolean;
  newWorkDisabled: boolean;
}) {
  const { t } = useLanguage();
  const latest = history.versions.at(-1);
  const initial =
    latest?.protocol.source_version === context.source_version
      ? latest.protocol
      : blankProtocol(context, question);
  const [draft, setDraft] = useState(initial);
  const [alpha, setAlpha] = useState(
    initial.reporting ? String(initial.reporting.alpha) : "",
  );
  const [confidence, setConfidence] = useState(
    initial.reporting ? String(initial.reporting.confidence_level) : "",
  );
  const [alternative, setAlternative] = useState<string>(
    initial.reporting?.alternative ?? "unknown",
  );
  const [assessment, setAssessment] = useState<{
    signature: string;
    value: ProtocolAssessment;
  } | null>(null);
  const [methodAssessment, setMethodAssessment] = useState<{
    signature: string;
    method: string;
    value: ProtocolAssessment;
  } | null>(null);
  const [method, setMethod] = useState("");
  const [references, setReferences] = useState<Record<string, string>>({});
  const [confirmIntent, setConfirmIntent] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [readError, setReadError] = useState<unknown>(null);
  const active = currentProtocol(history, context);
  const reporting =
    alpha !== "" &&
    confidence !== "" &&
    alternative !== "unknown" &&
    Number.isFinite(Number(alpha)) &&
    Number.isFinite(Number(confidence))
      ? {
          alpha: Number(alpha),
          confidence_level: Number(confidence),
          alternative: alternative as "two_sided" | "less" | "greater",
          comparison_order: "recorded_engine_order" as const,
        }
      : null;
  const document = { ...draft, research_question: question, reporting };
  const signature = protocolContentKey(document);
  const action = useResearchAction(
    `${context.session_id}:${context.source_version}:${latest?.revision ?? 0}:${history.activations.at(-1)?.generation ?? 0}:${signature}`,
  );
  useEffect(() => {
    onBusy(
      action.pending || action.error != null || reloading || readError != null,
    );
    return () => onBusy(false);
  }, [action.pending, action.error, reloading, readError, onBusy]);
  const blocked =
    readOnly ||
    action.pending ||
    action.error != null ||
    reloading ||
    readError != null;
  const editingDisabled = blocked || newWorkDisabled;
  const savedExact =
    latest?.protocol.source_version === context.source_version &&
    protocolContentKey(latest.protocol) === signature;
  const activeExact =
    savedExact && active?.version.version_id === latest?.version_id;
  useEffect(() => {
    onReady(Boolean(activeExact));
    return () => onReady(false);
  }, [activeExact, onReady]);
  const ready =
    assessment?.signature === signature && assessment.value.status === "ready";
  const shownAssessment =
    methodAssessment?.signature === signature &&
    methodAssessment.method === method
      ? methodAssessment.value
      : assessment?.signature === signature
        ? assessment.value
        : null;
  function update<K extends keyof ProtocolDocument>(
    key: K,
    value: ProtocolDocument[K],
  ) {
    setDraft((old) => ({ ...old, [key]: value }));
    setConfirmIntent(false);
  }
  const option = (value: string) =>
    t(`protocol.value.${value}` as NoParameterMessageKey);
  const select = (
    label: NoParameterMessageKey,
    value: string,
    values: readonly string[],
    change: (value: string) => void,
  ) => (
    <label>
      {t(label)}
      <SearchSelect value={value} onChange={(event) => change(event.target.value)}>
        {values.map((item) => (
          <option key={item} value={item}>
            {option(item)}
          </option>
        ))}
      </SearchSelect>
    </label>
  );
  const pairing = draft.pairing ?? {
    layout: "unknown" as const,
    description: null,
    conditions: [],
    pair_id: null,
  };
  const multiplicity = draft.multiplicity ?? {
    policy: "unknown" as const,
    family_id: null,
    planned_comparisons: null,
    adjustment: "unknown" as const,
  };
  const prediction = draft.prediction ?? {
    task: "unknown" as const,
    split_strategy: null,
    primary_metric: null,
  };
  function refreshAfterWrite() {
    setReloading(true);
    setReadError(null);
    void changed()
      .catch(setReadError)
      .finally(() => setReloading(false));
  }
  function activate(actionName: "confirm" | "revoke") {
    const selected = actionName === "revoke" ? active?.version : latest;
    if (!selected) return;
    action.start(
      async (signal, key) => {
        const value = await researchApi.protocols.activate(
          context.session_id,
          {
            version_id: selected.version_id,
            protocol_hash: selected.protocol_hash,
            expected_generation: history.activations.at(-1)?.generation ?? 0,
            action: actionName,
            idempotency_key: key,
          },
          signal,
        );
        return value;
      },
      () => {
        setConfirmIntent(false);
        refreshAfterWrite();
      },
      { retryable: true },
    );
  }
  return (
    <details className="formal-plan-editor formal-protocol" open={!active}>
      <summary>
        {t("protocol.title")} ·{" "}
        {t(active ? "protocol.active" : "protocol.draft")}
      </summary>
      <p className="entry-help">{t("protocol.help")}</p>
      {latest && latest.protocol.research_question !== question && (
        <Button
          variant="ghost"
          disabled={editingDisabled}
          onClick={() => onQuestion(latest.protocol.research_question)}
        >
          {t("protocol.savedQuestion")}
        </Button>
      )}
      {active && (
        <p className="entry-help" data-no-translate>
          {active.version.protocol.research_question}
        </p>
      )}
      <fieldset disabled={editingDisabled}>
        <div className="formal-inline-form">
          {(["study_design", "mode", "estimand", "independence"] as const).map(
            (field) => (
              <span className="formal-protocol-choice" key={field}>
                {select(
                  `protocol.field.${field}`,
                  draft[field],
                  enums[field],
                  (value) => update(field, value as never),
                )}
              </span>
            ),
          )}
          <label>
            {t("protocol.field.unit_of_analysis")}
            <input
              value={draft.unit_of_analysis ?? ""}
              maxLength={255}
              onChange={(event) =>
                update("unit_of_analysis", event.target.value || null)
              }
            />
          </label>
        </div>
        <label>
          {t("protocol.field.population_and_sample")}
          <textarea
            value={draft.population_and_sample ?? ""}
            maxLength={1000}
            onChange={(event) =>
              update("population_and_sample", event.target.value || null)
            }
          />
        </label>
        {context.datasets.map((source) => (
          <fieldset className="formal-fields" key={source.dataset_id}>
            <legend>
              {t("research.dataset")} ·{" "}
              <span data-no-translate>{source.dataset_id.slice(0, 8)}</span>
            </legend>
            {source.fields.map((field) => {
              const selected = draft.variables.find(
                (item) =>
                  item.reference.dataset_id === source.dataset_id &&
                  item.reference.name === field.reference.name,
              );
              return (
                <label key={field.reference.name}>
                  <span>
                    <span data-no-translate>{field.reference.name}</span> ·{" "}
                    {t("protocol.role")}
                  </span>
                  <SearchSelect
                    value={selected?.role ?? ""}
                    onChange={(event) => {
                      const matches = (
                        item: (typeof draft.variables)[number],
                      ) =>
                        item.reference.dataset_id === source.dataset_id &&
                        item.reference.name === field.reference.name;
                      update(
                        "variables",
                        !event.target.value
                          ? draft.variables.filter((item) => !matches(item))
                          : selected
                            ? draft.variables.map((item) =>
                                matches(item)
                                  ? {
                                      ...item,
                                      role: event.target.value as ProtocolRole,
                                    }
                                  : item,
                              )
                            : [
                                ...draft.variables,
                                {
                                  reference: field.reference,
                                  role: event.target.value as ProtocolRole,
                                },
                              ],
                      );
                    }}
                  >
                    <option value="">{t("protocol.chooseRole")}</option>
                    {roles.map((role) => (
                      <option key={role} value={role}>
                        {option(role)}
                      </option>
                    ))}
                  </SearchSelect>
                </label>
              );
            })}
          </fieldset>
        ))}
        {draft.variables.length > 0 && (
          <details>
            <summary>{t("protocol.variables")}</summary>
            <ol>
              {draft.variables.map((item, index) => (
                <li key={`${item.reference.dataset_id}:${item.reference.name}`}>
                  <span data-no-translate>{item.reference.name}</span> ·{" "}
                  {option(item.role)}
                  <div className="formal-actions">
                    {([-1, 1] as const).map((direction) => (
                      <Button
                        key={direction}
                        variant="ghost"
                        disabled={
                          index + direction < 0 ||
                          index + direction >= draft.variables.length
                        }
                        onClick={() => {
                          const reordered = [...draft.variables];
                          [reordered[index], reordered[index + direction]] = [
                            reordered[index + direction],
                            reordered[index],
                          ];
                          update("variables", reordered);
                        }}
                      >
                        {t(
                          direction < 0
                            ? "protocol.moveUp"
                            : "protocol.moveDown",
                        )}
                      </Button>
                    ))}
                  </div>
                </li>
              ))}
            </ol>
          </details>
        )}
        <details>
          <summary>{t("protocol.details")}</summary>
          <div className="formal-inline-form">
            {(
              [
                "missing_value_policy",
                "cluster_structure",
                "time_structure",
                "analysis_intent",
              ] as const
            ).map((field) => (
              <span className="formal-protocol-choice" key={field}>
                {select(
                  `protocol.field.${field}`,
                  draft[field],
                  enums[field],
                  (value) => update(field, value as never),
                )}
              </span>
            ))}
          </div>
          {(
            [
              "missingness_context",
              "group_definitions",
              "assumptions",
              "known_limitations",
              "unresolved_questions",
            ] as const
          ).map((field) => (
            <label key={field}>
              {t(`protocol.field.${field}`)}
              <textarea
                maxLength={1000}
                value={
                  Array.isArray(draft[field])
                    ? draft[field].join("\n")
                    : (draft[field] ?? "")
                }
                onChange={(event) =>
                  field === "missingness_context"
                    ? update(field, event.target.value || null)
                    : update(field, lines(event.target.value))
                }
              />
            </label>
          ))}
          {draft.study_design === "paired" && (
            <fieldset>
              <legend>{t("protocol.pairing")}</legend>
              {select(
                "protocol.pairing",
                pairing.layout,
                ["unknown", "wide_same_row", "long_pair_id"],
                (value) =>
                  update("pairing", {
                    ...pairing,
                    layout: value as typeof pairing.layout,
                  }),
              )}
              <label>
                {t("protocol.field.pairDescription")}
                <textarea
                  value={pairing.description ?? ""}
                  maxLength={1000}
                  onChange={(event) =>
                    update("pairing", {
                      ...pairing,
                      description: event.target.value || null,
                    })
                  }
                />
              </label>
              <label>
                {t("protocol.conditions")}
                <textarea
                  value={pairing.conditions.join("\n")}
                  onChange={(event) =>
                    update("pairing", {
                      ...pairing,
                      conditions: lines(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                {t("protocol.field.pairId")}
                <SearchSelect
                  value={
                    pairing.pair_id
                      ? `${pairing.pair_id.dataset_id}:${pairing.pair_id.name}`
                      : ""
                  }
                  onChange={(event) =>
                    update("pairing", {
                      ...pairing,
                      pair_id:
                        context.datasets
                          .flatMap((item) => item.fields)
                          .find(
                            (item) =>
                              `${item.reference.dataset_id}:${item.reference.name}` ===
                              event.target.value,
                          )?.reference ?? null,
                    })
                  }
                >
                  <option value="">{t("protocol.chooseRole")}</option>
                  {context.datasets
                    .flatMap((item) => item.fields)
                    .map((field) => (
                      <option
                        key={`${field.reference.dataset_id}:${field.reference.name}`}
                        value={`${field.reference.dataset_id}:${field.reference.name}`}
                      >
                        {field.reference.name}
                      </option>
                    ))}
                </SearchSelect>
              </label>
            </fieldset>
          )}
          <fieldset>
            <legend>{t("protocol.field.multiplicity")}</legend>
            <div className="formal-inline-form">
              {select(
                "protocol.field.multiplicity",
                multiplicity.policy,
                [
                  "unknown",
                  "single_prespecified",
                  "exploratory_unadjusted",
                  "family_adjusted",
                ],
                (value) =>
                  update("multiplicity", {
                    ...multiplicity,
                    policy: value as typeof multiplicity.policy,
                  }),
              )}
              <label>
                {t("protocol.field.comparisons")}
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={multiplicity.planned_comparisons ?? ""}
                  onChange={(event) =>
                    update("multiplicity", {
                      ...multiplicity,
                      planned_comparisons:
                        event.target.value === ""
                          ? null
                          : Number(event.target.value),
                    })
                  }
                />
              </label>
              {select(
                "protocol.field.adjustment",
                multiplicity.adjustment,
                ["unknown", "none", "holm", "bonferroni"],
                (value) =>
                  update("multiplicity", {
                    ...multiplicity,
                    adjustment: value as typeof multiplicity.adjustment,
                  }),
              )}
              <label>
                {t("protocol.field.family")}
                <input
                  value={multiplicity.family_id ?? ""}
                  maxLength={255}
                  onChange={(event) =>
                    update("multiplicity", {
                      ...multiplicity,
                      family_id: event.target.value || null,
                    })
                  }
                />
              </label>
            </div>
          </fieldset>
          <div className="formal-inline-form">
            <label>
              {t("protocol.field.alpha")}
              <input
                type="number"
                min={0.001}
                max={0.999}
                step="any"
                value={alpha}
                onChange={(event) => setAlpha(event.target.value)}
              />
            </label>
            <label>
              {t("protocol.field.confidence")}
              <input
                type="number"
                min={0.5}
                max={0.999}
                step="any"
                value={confidence}
                onChange={(event) => setConfidence(event.target.value)}
              />
            </label>
            {select(
              "protocol.field.alternative",
              alternative,
              ["unknown", "two_sided", "less", "greater"],
              setAlternative,
            )}
          </div>
          {draft.study_design === "prediction" && (
            <fieldset>
              {select(
                "protocol.field.predictionTask",
                prediction.task,
                ["unknown", "regression", "classification"],
                (value) =>
                  update("prediction", {
                    ...prediction,
                    task: value as typeof prediction.task,
                  }),
              )}
              {(["split_strategy", "primary_metric"] as const).map((field) => (
                <label key={field}>
                  {t(
                    field === "split_strategy"
                      ? "protocol.field.split"
                      : "protocol.field.metric",
                  )}
                  <input
                    value={prediction[field] ?? ""}
                    maxLength={255}
                    onChange={(event) =>
                      update("prediction", {
                        ...prediction,
                        [field]: event.target.value || null,
                      })
                    }
                  />
                </label>
              ))}
            </fieldset>
          )}
        </details>
        <details>
          <summary>{t("protocol.methods")}</summary>
          <fieldset>
            <legend>{t("protocol.methods")}</legend>
            {protocolMethods.map((value) => (
              <label className="formal-checkbox" key={value}>
                <input
                  type="checkbox"
                  checked={draft.candidate_methods.includes(value)}
                  onChange={(event) =>
                    update(
                      "candidate_methods",
                      event.target.checked
                        ? [...draft.candidate_methods, value]
                        : draft.candidate_methods.filter(
                            (item) => item !== value,
                          ),
                    )
                  }
                />
                <span>{t(`protocol.method.${value}`)}</span>
              </label>
            ))}
          </fieldset>
        </details>
        <details>
          <summary>{t("protocol.diagnostics")}</summary>
          <fieldset>
            <legend>{t("protocol.diagnostics")}</legend>
            {protocolDiagnostics.map((value) => (
              <label className="formal-checkbox" key={value}>
                <input
                  type="checkbox"
                  checked={draft.diagnostics.includes(value)}
                  onChange={(event) =>
                    update(
                      "diagnostics",
                      event.target.checked
                        ? [...draft.diagnostics, value]
                        : draft.diagnostics.filter((item) => item !== value),
                    )
                  }
                />
                <span>{t(`protocol.diagnostic.${value}`)}</span>
              </label>
            ))}
          </fieldset>
        </details>
        <div className="formal-actions">
          <Button
            variant="outline"
            disabled={!question.trim() || question.length > 1000}
            onClick={() =>
              action.start(
                (signal) =>
                  researchApi.protocols.assess(
                    context.session_id,
                    document,
                    undefined,
                    signal,
                  ),
                (value) => {
                  setAssessment({ signature, value });
                  setMethodAssessment(null);
                },
              )
            }
          >
            {t("protocol.assess")}
          </Button>
          <Button
            variant="outline"
            disabled={!question.trim() || question.length > 1000 || savedExact}
            onClick={() =>
              action.start(
                async (signal, key) => {
                  const saved = await researchApi.protocols.save(
                    context.session_id,
                    {
                      protocol: document,
                      expected_revision: latest?.revision ?? 0,
                      idempotency_key: key,
                    },
                    signal,
                  );
                  return saved;
                },
                refreshAfterWrite,
                { retryable: true },
              )
            }
          >
            {t("protocol.save")}
          </Button>
        </div>
      </fieldset>
      {shownAssessment && (
        <div role="status">
          <p>
            {t(
              shownAssessment.status === "ready"
                ? "protocol.ready"
                : "protocol.needsReview",
            )}
          </p>
          <ul>
            {shownAssessment.issues.map((item, index) => (
              <li key={`${item.field}:${item.code}:${index}`}>
                {t(issueFields[item.field.split(".")[0]] ?? "protocol.issue")}:{" "}
                {t(
                  ([
                    "required_metadata_missing",
                    "method_alternative_not_supported",
                    "method_missing_policy_not_supported",
                    "family_adjusted_execution_not_supported",
                    "diagnostic_plan_missing",
                  ].includes(item.code)
                    ? `protocol.issue.${item.code}`
                    : "protocol.issue.generic") as NoParameterMessageKey,
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <label className="formal-checkbox">
        <input
          type="checkbox"
          checked={confirmIntent}
          disabled={editingDisabled || !savedExact || activeExact}
          onChange={(event) => setConfirmIntent(event.target.checked)}
        />
        {t("protocol.confirmHint")}
      </label>
      <div className="formal-actions">
        <Button
          disabled={
            editingDisabled ||
            !savedExact ||
            activeExact ||
            !ready ||
            !confirmIntent
          }
          onClick={() => activate("confirm")}
        >
          {t("protocol.confirm")}
        </Button>
        <Button
          variant="outline"
          disabled={blocked || !active}
          onClick={() => activate("revoke")}
        >
          {t("protocol.revoke")}
        </Button>
      </div>
      {!activeExact && (
        <p className="entry-help">
          {t(active ? "protocol.changed" : "protocol.noActive")}
        </p>
      )}
      <fieldset disabled={editingDisabled || !activeExact}>
        <label>
          {t("protocol.method")}
          <SearchSelect
            value={method}
            onChange={(event) => setMethod(event.target.value)}
          >
            <option value="">{t("protocol.method")}</option>
            {draft.candidate_methods.map((value) => (
              <option key={value} value={value}>
                {t(`protocol.method.${value}` as NoParameterMessageKey)}
              </option>
            ))}
          </SearchSelect>
        </label>
        {draft.variables
          .filter(
            (item) =>
              item.role === "predictor" &&
              context.datasets
                .flatMap((source) => source.fields)
                .find(
                  (field) =>
                    field.reference.dataset_id === item.reference.dataset_id &&
                    field.reference.name === item.reference.name,
                )?.inferred_type !== "numeric",
          )
          .map((item) => (
            <label key={item.reference.name}>
              {t("protocol.reference")} ·{" "}
              <span data-no-translate>{item.reference.name}</span>
              <input
                maxLength={255}
                value={references[item.reference.name] ?? ""}
                onChange={(event) =>
                  setReferences((old) => ({
                    ...old,
                    [item.reference.name]: event.target.value,
                  }))
                }
              />
            </label>
          ))}
        <Button
          variant="outline"
          disabled={!method || !activeExact}
          onClick={() =>
            action.start(
              async (signal) => {
                if (!active) throw new Error("RESEARCH_PROTOCOL_UNAVAILABLE");
                const value = await researchApi.protocols.assess(
                  context.session_id,
                  document,
                  method,
                  signal,
                );
                return {
                  value,
                  plan:
                    value.status === "ready"
                      ? protocolPlan(active, context, method, references)
                      : null,
                };
              },
              ({ value, plan }) => {
                setMethodAssessment({ signature, method, value });
                if (plan) onProposal(plan);
              },
            )
          }
        >
          {t("protocol.newPlan")}
        </Button>
      </fieldset>
      <ResearchActionNotice action={action} reload={changed} />
      {readError != null && (
        <ResearchFailure error={readError} retry={refreshAfterWrite} />
      )}
    </details>
  );
}
