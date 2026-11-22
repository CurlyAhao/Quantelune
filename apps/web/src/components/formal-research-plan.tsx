"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { researchApi } from "@/lib/research-api";
import { researchObject } from "@/lib/research-view";
import { currentProtocol } from "@/lib/research-protocol";
import { downloadApiFile } from "@/lib/api-download";
import type {
  ResearchContext,
  ResearchAnyPlan,
  ResearchPlanVersion,
} from "@/lib/research-types";
import type { StatisticalRunRequest } from "@/lib/types";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import { DesktopLink } from "./desktop-link";
import { FormalResearchFacts } from "./formal-research-facts";
import { DownloadButton } from "./download-button";
import {
  ResearchActionNotice,
  ResearchFailure,
  ResearchStatus,
  ResearchValue,
} from "./formal-research-common";
import { useResearchAction } from "./use-research-action";

export type AcceptedResearchVersion = {
  versionId: string;
  planHash: string;
  sourceVersion: string;
};

export function FormalResearchDraft({
  sessionId,
  context,
  draftId,
  changed,
  onApplyStatistics,
  manualSource,
  readOnly = false,
  acceptedVersions = [],
}: {
  sessionId: string;
  context: ResearchContext;
  draftId: string;
  changed: () => Promise<unknown>;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
  readOnly?: boolean;
  acceptedVersions?: AcceptedResearchVersion[];
}) {
  const { t } = useLanguage();
  const [selected, setSelected] = useState("");
  const versions = useQuery({
    queryKey: ["research", context.workspace_id, sessionId, "draft", draftId],
    queryFn: ({ signal }) =>
      researchApi.plans.versions(sessionId, draftId, signal),
    refetchInterval: 5000,
    retry: false,
  });
  const version =
    versions.data?.find((item) => item.version_id === selected) ??
    versions.data?.at(-1);
  return (
    <section className="formal-plan">
      {versions.isPending && <p role="status">{t("research.loading")}</p>}
      {versions.isError && (
        <ResearchFailure
          error={versions.error}
          retry={() => {
            void versions.refetch();
          }}
        />
      )}
      {version && (
        <>
          <label>
            {t("research.plan")}
            <SearchSelect
              aria-label={t("research.plan")}
              value={version.version_id}
              onChange={(event) => setSelected(event.target.value)}
            >
              {versions.data?.map((item) => (
                <option key={item.version_id} value={item.version_id}>
                  {t("research.version", { revision: item.revision })} ·{" "}
                  {item.plan.title}
                </option>
              ))}
            </SearchSelect>
          </label>
          <FormalResearchProposal
            key={version.version_id}
            plan={version.plan}
            version={version}
            context={context}
            sessionId={sessionId}
            onApplyStatistics={onApplyStatistics}
            manualSource={manualSource}
            readOnly={readOnly}
            accepted={acceptedVersions.some(
              (item) =>
                item.versionId === version.version_id &&
                item.planHash === version.plan_hash &&
                item.sourceVersion === version.plan.source_version,
            )}
            changed={async () => {
              await versions.refetch({ throwOnError: true });
              return changed();
            }}
            saved={(value) => {
              setSelected(value.version_id);
            }}
          />
        </>
      )}
    </section>
  );
}

export function FormalResearchProposal({
  plan,
  version,
  context,
  sessionId,
  changed,
  saved,
  onApplyStatistics,
  manualSource,
  readOnly = false,
  accepted = false,
}: {
  plan: ResearchAnyPlan;
  version?: ResearchPlanVersion;
  context: ResearchContext;
  sessionId: string;
  changed: () => Promise<unknown>;
  saved?: (value: ResearchPlanVersion) => void;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
  readOnly?: boolean;
  accepted?: boolean;
}) {
  const { t, locale } = useLanguage();
  const original = JSON.stringify(plan, null, 2);
  const [editor, setEditor] = useState(original);
  const [invalid, setInvalid] = useState(false);
  const [acceptance, setAcceptance] = useState<string | null>(null);
  const [acceptIntent, setAcceptIntent] = useState(false);
  const protocols = useQuery({
    queryKey: ["research", context.workspace_id, sessionId, "protocols"],
    queryFn: ({ signal }) => researchApi.protocols.history(sessionId, signal),
    enabled: plan.version === "research-plan-v2",
    retry: false,
    refetchInterval: 5000,
  });
  const activeProtocol = currentProtocol(protocols.data, context);
  const protocolStale =
    plan.version === "research-plan-v2" &&
    (!protocols.isSuccess ||
      !activeProtocol ||
      activeProtocol.version.version_id !== plan.protocol_version_id ||
      activeProtocol.version.protocol_hash !== plan.protocol_hash ||
      activeProtocol.activation.generation !== plan.protocol_generation);
  const action = useResearchAction(
    `${sessionId}:${version?.version_id ?? context.source_version}:plan:${activeProtocol?.activation.generation ?? "none"}`,
  );
  const stale = plan.source_version !== context.source_version || protocolStale;
  const blocked = readOnly || action.pending || action.error != null;
  const dirty = editor !== original;
  const isAccepted = accepted || acceptance !== null;
  function save() {
    let document: unknown;
    try {
      document = JSON.parse(editor);
    } catch {
      setInvalid(true);
      return;
    }
    const object = researchObject(document);
    if (
      !object ||
      typeof object.title !== "string" ||
      !Array.isArray(object.steps) ||
      (plan.version === "research-plan-v2"
        ? object.version !== "research-plan-v2" ||
          new TextEncoder().encode(editor).byteLength > 512 * 1024
        : (object.version !== undefined &&
            object.version !== "research-plan-v1") ||
          editor.length > 128 * 1024)
    ) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    // Syntax only. The service validates every capability, reference and parameter.
    action.start(
      (signal, key) => {
        const request = {
          plan: document as ResearchAnyPlan,
          expected_revision: version?.revision ?? 0,
          idempotency_key: key,
        };
        return version
          ? researchApi.plans.saveDraft(
              sessionId,
              version.draft_id,
              request,
              signal,
            )
          : researchApi.plans.createDraft(sessionId, request, signal);
      },
      (value) => {
        saved?.(value);
        void changed().catch(() => undefined);
      },
      { retryable: true },
    );
  }
  const manual = manualSource
    ? manualStatistics(plan, context, manualSource)
    : null;
  return (
    <div className="formal-plan-document">
      <div className="formal-section-heading">
        <h3 data-no-translate>
          {plan.title === "Explicit structured analysis"
            ? locale === "zh-CN"
              ? "分析方案"
              : "Analysis plan"
            : plan.title}
        </h3>
        <span className="entry-help">
          {t(isAccepted ? "research.acceptedPlan" : "research.proposal")}
        </span>
      </div>
      {plan.research_question && (
        <p data-no-translate>{plan.research_question}</p>
      )}
      <ol className="formal-plan-steps">
        {plan.steps.map((step) => (
          <li key={step.id}>
            <span>
              {t(
                step.capability === "inspect"
                  ? "research.inspect"
                  : step.capability === "profile"
                    ? "research.profile"
                    : step.capability === "quality"
                      ? "research.quality"
                      : step.capability === "descriptive"
                        ? "research.descriptive"
                        : step.capability === "statistics"
                          ? "research.statistics"
                          : "research.correlation",
              )}
            </span>
            {step.capability === "statistics" && (
              <span data-no-translate>
                {" "}
                · {String(step.configuration?.method_id ?? "")}
              </span>
            )}
            <p className="entry-help" data-no-translate>
              {step.fields?.map((field) => field.name).join(", ")}
            </p>
            {Boolean(step.depends_on?.length) && (
              <p>
                {t("research.depends")}:{" "}
                <span data-no-translate>{step.depends_on?.join(", ")}</span>
              </p>
            )}
            <details>
              <summary>
                {t("research.parameters")} / {t("research.sourceDetails")}
              </summary>
              <ResearchValue value={step.configuration ?? {}} />
              <code>{step.dataset_id}</code>
              <br />
              <code>{step.dataset_sha256}</code>
            </details>
          </li>
        ))}
      </ol>
      <details className="formal-plan-editor">
        <summary>{t("research.editPlan")}</summary>
        <label>
          {t("research.advancedPlan")}
          <textarea
            value={editor}
            maxLength={
              plan.version === "research-plan-v2" ? 512 * 1024 : 128 * 1024
            }
            onChange={(event) => {
              setEditor(event.target.value);
              setInvalid(false);
            }}
            disabled={blocked}
            spellCheck={false}
          />
        </label>
      </details>
      {invalid && <p role="alert">{t("research.invalidPlan")}</p>}
      {stale && <p role="alert">{t("research.stalePlan")}</p>}
      <div className="formal-actions">
        <Button
          variant="outline"
          disabled={blocked || stale || (version != null && !dirty)}
          onClick={save}
        >
          {t("research.saveDraft")}
        </Button>
        {version && (
          <Button
            disabled={blocked || stale || dirty || acceptIntent || isAccepted}
            onClick={() => {
              setAcceptIntent(true);
              action.start(
                (signal, key) =>
                  researchApi.plans.accept(
                    sessionId,
                    {
                      version_id: version.version_id,
                      plan_hash: version.plan_hash,
                      source_version: version.plan.source_version,
                      idempotency_key: key,
                    },
                    signal,
                  ),
                (value) => {
                  setAcceptance(value.acceptance_id);
                  void changed().catch(() => undefined);
                },
                { retryable: true },
              );
            }}
          >
            {t("research.accept")}
          </Button>
        )}
        {manual && onApplyStatistics && (
          <Button
            variant="outline"
            disabled={blocked || stale || dirty}
            onClick={() => onApplyStatistics(manual)}
          >
            {t("research.applyManual")}
          </Button>
        )}
      </div>
      {isAccepted && <p role="status">{t("research.planAccepted")}</p>}
      {!isAccepted && (
        <ResearchActionNotice
          action={action}
          reload={changed}
          resetAfterRead={!acceptIntent}
        />
      )}
    </div>
  );
}

export function FormalResearchExecution({
  sessionId,
  workspaceId,
  acceptanceId,
  readOnly = false,
  protocolBound = false,
}: {
  sessionId: string;
  workspaceId: string;
  acceptanceId: string;
  readOnly?: boolean;
  protocolBound?: boolean;
}) {
  const { t } = useLanguage();
  const execution = useQuery({
    queryKey: ["research", workspaceId, sessionId, "execution", acceptanceId],
    queryFn: ({ signal }) =>
      researchApi.plans.execution(sessionId, acceptanceId, signal),
    refetchInterval: 3000,
    retry: false,
  });
  const action = useResearchAction(`${sessionId}:${acceptanceId}`);
  const value = execution.data;
  const reportAvailable =
    protocolBound &&
    !execution.isError &&
    value?.acceptance_id === acceptanceId &&
    value.state === "completed" &&
    value.steps.some(
      (step) =>
        step.state === "proven" &&
        researchObject(researchObject(step.result)?.citation)?.domain_kind ===
          "statistics" &&
        typeof researchObject(researchObject(step.result)?.citation)
          ?.completion_hash === "string",
    );
  return (
    <section className="formal-execution">
      <h3>{t("research.execution")}</h3>
      {execution.isPending && <p role="status">{t("research.loading")}</p>}
      {execution.isError && (
        <ResearchFailure
          error={execution.error}
          retry={() => {
            void execution.refetch();
          }}
        />
      )}
      {value && (
        <>
          <ResearchStatus state={value.state} />
          <ol className="formal-plan-steps">
            {value.steps.map((step) => (
              <li key={step.step_id}>
                <strong data-no-translate>{step.step_id}</strong> ·{" "}
                <ResearchStatus state={step.state} />
                {step.child_run_id && (
                  <DesktopLink
                    href={`/agent?workspaceId=${workspaceId}&sessionId=${sessionId}&runId=${step.child_run_id}`}
                    className="formal-source-link"
                  >
                    {t("research.openSource")}
                  </DesktopLink>
                )}
                {step.result != null && (
                  <details>
                    <summary>{t("research.sourceDetails")}</summary>
                    <ResearchValue value={step.result} />
                  </details>
                )}
                {protocolBound &&
                  step.state === "proven" &&
                  researchObject(researchObject(step.result)?.citation)
                    ?.domain_kind === "statistics" &&
                  typeof researchObject(researchObject(step.result)?.citation)
                    ?.completion_hash === "string" && (
                    <FormalResearchFacts
                      key={String(
                        researchObject(researchObject(step.result)?.citation)
                          ?.completion_hash,
                      )}
                      sessionId={sessionId}
                      acceptanceId={acceptanceId}
                      stepId={step.step_id}
                      completionHash={String(
                        researchObject(researchObject(step.result)?.citation)
                          ?.completion_hash,
                      )}
                    />
                  )}
              </li>
            ))}
          </ol>
          {reportAvailable && (
            <DownloadButton
              identity={`${workspaceId}:${sessionId}:${acceptanceId}:${value.plan_version_id}:${value.source_version}`}
              className="formal-text-action"
              limitMiB={8}
              handoffMessage={t("research.reportHandoff")}
              download={(signal) =>
                downloadApiFile(
                  `/api/v1/research/sessions/${sessionId}/plans/acceptances/${acceptanceId}/report`,
                  `research-report-${acceptanceId}.zip`,
                  signal,
                  8 * 1024 * 1024,
                )
              }
            >
              {t("research.exportSavedReport")}
            </DownloadButton>
          )}
          <Button
            variant="outline"
            disabled={
              readOnly ||
              action.pending ||
              action.error != null ||
              value.cancel_requested ||
              value.state === "completed" ||
              value.state === "cancelled"
            }
            onClick={() =>
              action.start(
                (signal) =>
                  researchApi.plans.cancelExecution(
                    sessionId,
                    acceptanceId,
                    { expected_revision: value.revision },
                    signal,
                  ),
                () => {
                  void execution.refetch();
                },
              )
            }
          >
            {t("research.cancelPlan")}
          </Button>
        </>
      )}
      <ResearchActionNotice
        action={action}
        reload={() => execution.refetch()}
      />
    </section>
  );
}

function manualStatistics(
  plan: ResearchAnyPlan,
  context: ResearchContext,
  source: { datasetId: string; sha256: string },
): StatisticalRunRequest | null {
  if (
    plan.version === "research-plan-v2" ||
    plan.source_version !== context.source_version ||
    plan.steps.length !== 1
  )
    return null;
  const step = plan.steps[0],
    config = step.configuration;
  if (
    step.dataset_id !== source.datasetId ||
    step.dataset_sha256 !== source.sha256
  )
    return null;
  if (
    !config ||
    !["descriptive", "correlation"].includes(step.capability) ||
    !context.datasets.some(
      (dataset) =>
        dataset.dataset_id === step.dataset_id &&
        dataset.sha256 === step.dataset_sha256,
    )
  )
    return null;
  const method = config.method_id;
  if (
    typeof method !== "string" ||
    !["descriptive", "pearson", "spearman", "kendall"].includes(method) ||
    !Array.isArray(config.variables) ||
    config.variables.some((item) => typeof item !== "string") ||
    Object.keys(config).some(
      (key) =>
        ![
          "method_id",
          "variables",
          "group_variable",
          "paired",
          "mu",
          "expected_proportions",
          "alternative",
          "confidence_level",
          "missing_value_policy",
          "target",
          "categorical_variables",
          "reference_categories",
          "standardize",
          "alpha",
          "l1_ratio",
        ].includes(key),
    )
  )
    return null;
  return {
    ...config,
    method_id: method,
    variables: config.variables as string[],
  } as StatisticalRunRequest;
}
