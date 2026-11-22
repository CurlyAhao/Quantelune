"use client";

import { Message as UiMessage } from "@/components/message";
import { useLanguage } from "@/providers/language-provider";
import { ownedRecordValue } from "@/lib/owned-record-value";
import type { AppTranslator, NoParameterMessageKey } from "@/lib/i18n-messages";
import { useEffect, useRef, useState } from "react";
import { apiFetch, isApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { PreviewEvidence } from "./preview-evidence";
import { PreviewHistory } from "./preview-history";
import type {
  PreviewScope,
  PreviewContext as Context,
  PreviewTurn as Turn,
} from "@/lib/preview-types";
import type {
  MLExperimentCreateRequest,
  StatisticalRunRequest,
} from "@/lib/types";
export type { PreviewScope } from "@/lib/preview-types";
const EGRESS_CATEGORY_LABELS: Record<string, NoParameterMessageKey> = {
  project: "preview.disclosure.project",
  schema: "preview.disclosure.schema",
  quality: "preview.disclosure.quality",
  results: "preview.disclosure.results",
  form: "preview.disclosure.form",
  materials: "preview.disclosure.materials",
  history: "preview.disclosure.history",
  rules: "preview.disclosure.rules",
};
function egressCategoryLabel(
  category: string,
  hasML: boolean,
  t: AppTranslator,
): string {
  if (category === "quality" && hasML) return t("preview.disclosure.qualityML");
  const key = ownedRecordValue(EGRESS_CATEGORY_LABELS, category);
  return key ? t(key) : category;
}

/** Shared turn approval surface; ProjectShell publishes its current scope in AP-02. */
export function PreviewTurnPanel({
  scope,
  identity = "",
  initialQuestion = "",
  onApplyStatistics,
  onApplyML,
  onCompleted,
}: {
  scope: PreviewScope;
  identity?: string;
  initialQuestion?: string;
  onApplyStatistics?: (config: StatisticalRunRequest) => void;
  onApplyML?: (config: MLExperimentCreateRequest) => void;
  onCompleted?: (value: {
    turn: Turn;
    scope: PreviewScope;
    question: string;
    requestId: string;
  }) => Promise<void>;
}) {
  const { t } = useLanguage();
  const scopeKey = JSON.stringify(scope);
  const activeIdentity = useRef(identity + scopeKey);
  activeIdentity.current = identity + scopeKey;
  const [context, setContext] = useState<Context | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [question, setQuestion] = useState(initialQuestion);
  const [approved, setApproved] = useState<string[]>([]);
  const [consent, setConsent] = useState(false);
  const [auto, setAuto] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<React.ReactNode>("");
  const [turn, setTurn] = useState<Turn | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const submission = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    submission.current?.abort();
    setBusy(false);
    setContext(null);
    setConsent(false);
    setApproved([]);
    setTurn(null);
    setRequestId(null);
    void Promise.all([
      apiFetch<Context>("/api/v1/preview/context", {
        method: "POST",
        body: scopeKey,
        signal: controller.signal,
      }),
      apiFetch<{ configured: boolean; revision: number | null }>(
        "/api/v1/preview/configuration",
        { signal: controller.signal },
      ),
    ])
      .then(([value, config]) => {
        if (controller.signal.aborted) return;
        setContext(value);
        setRevision(config.configured ? config.revision : null);
        setError("");
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setError(
            isApiError(error) && error.errorCode.startsWith("MATERIAL_") ? (
              <UiMessage id="ui.residual.materials.referenceMetadataUnavailable" />
            ) : (
              <UiMessage id="ui.residual.preview.contextUnavailable" />
            ),
          );
      });
    return () => {
      controller.abort();
      submission.current?.abort();
    };
  }, [scopeKey, identity]);

  async function submit() {
    if (!context || !revision || !consent) return;
    const requestIdentity = requestId ?? crypto.randomUUID();
    setRequestId(requestIdentity);
    setBusy(true);
    setError("");
    const controller = new AbortController();
    const startedIdentity = identity + scopeKey;
    submission.current = controller;
    try {
      const result = await apiFetch<Turn>("/api/v1/preview/turns", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          request_id: requestIdentity,
          scope: context.scope,
          config_revision: revision,
          disclosure_hash: context.disclosure_hash,
          question,
          approved_categories: approved,
          approve_question: true,
          approve_tool_summaries: true,
          local_auto_execute: auto,
        }),
      });
      if (
        !controller.signal.aborted &&
        activeIdentity.current === startedIdentity
      ) {
        setTurn(result);
        if (result.status === "finished")
          await onCompleted?.({
            turn: result,
            scope: context.scope,
            question,
            requestId: requestIdentity,
          });
      }
    } catch {
      if (!controller.signal.aborted)
        setError(
          <UiMessage id="ui.residual.preview.callIncompleteNoDuplicateSuccess" />,
        );
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function changed() {
    submission.current?.abort();
    setBusy(false);
    setConsent(false);
    setRequestId(null);
    setTurn(null);
  }

  return (
    <div className="space-y-3 text-sm">
      {!revision && (
        <p>
          {" "}
          <UiMessage id="ui.preview.agentNotReadyConfigureTheModelKeyAndCostCeilingsThroughTheSecureSettingsEntryPointFirstManualAnalysisRemainsAvailable" />{" "}
        </p>
      )}
      {context && (
        <>
          <p className="text-xs text-muted-foreground">
            {" "}
            <UiMessage id="ui.preview.eachRequestSendsOnlyTheCurrentQuestionSelectedCurrentSourceSummariesControlledToolParametersAndReturnedSummariesEarlierChatRawRowsAndMaterialsAreNotSentAutomatically" />{" "}
          </p>
          <details>
            <summary>
              <UiMessage id="ui.preview.reviewThisRequestSSourceAndEgressSummary" />
            </summary>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">
              {JSON.stringify(context, null, 2)}
            </pre>
          </details>
          {Object.keys(context.categories).map((category) => (
            <label key={category} className="flex gap-2">
              <input
                type="checkbox"
                checked={approved.includes(category)}
                onChange={(e) => {
                  changed();
                  setApproved(
                    e.target.checked
                      ? [...approved, category]
                      : approved.filter((c) => c !== category),
                  );
                }}
              />{" "}
              <UiMessage id="ui.preview.allowEgress" />{" "}
              {egressCategoryLabel(category, !!scope.ml_config, t)}
            </label>
          ))}
          <label className="block">
            {" "}
            <UiMessage id="ui.preview.researchQuestion" />{" "}
            <textarea
              className="mt-1 min-h-24 w-full rounded border bg-background p-2"
              maxLength={4000}
              value={question}
              onChange={(e) => {
                changed();
                setQuestion(e.target.value);
              }}
            />
          </label>
          <label className="flex gap-2">
            <input
              type="checkbox"
              checked={auto}
              onChange={(e) => {
                changed();
                setAuto(e.target.checked);
              }}
            />{" "}
            <UiMessage id="ui.preview.allowValidLocalStatisticsMlExecutionForThisRequestOtherwiseProposeOnly" />{" "}
          </label>
          {!!scope.material_references?.length && (
            <p className="text-xs">
              {" "}
              <UiMessage id="ui.preview.withMaterialCitationsAutomaticExecutionAcceptsOnlyTheApprovedCurrentValidFormOtherParametersMustBeFilledInForManualConfirmationFirst" />{" "}
            </p>
          )}
          <label className="flex gap-2">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />{" "}
            <UiMessage id="ui.preview.approveThisQuestionSelectedSourceVersionsColumnsAndControlledToolSummaryEgressUsingTheModelAndLocalBudgetConfirmedInSettings" />{" "}
          </label>
          <Button
            disabled={
              busy || !consent || !question.trim() || !revision || !!turn
            }
            onClick={() => void submit()}
          >
            {" "}
            <UiMessage id="ui.preview.submitThisRequest" />{" "}
          </Button>
        </>
      )}
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      {turn && (
        <div>
          <p>
            {" "}
            <UiMessage id="ui.preview.sessionStatus" />
            {turn.status} {turn.error_code ?? ""}
            <UiMessage id="ui.preview.businessCompletionIsDeterminedByTheActualRun" />{" "}
          </p>
          <PreviewEvidence
            evidence={turn.evidence}
            scope={context?.scope ?? scope}
            onApplyStatistics={onApplyStatistics}
            onApplyML={onApplyML}
          />
        </div>
      )}
      <PreviewHistory projectId={scope.project_id} identity={identity} />
    </div>
  );
}
