"use client";

import { Message as UiMessage } from "@/components/message";
import { useLanguage } from "@/providers/language-provider";
import type { AppTranslator } from "@/lib/i18n-messages";
import { workflowStatusLabel } from "@/lib/workflow-labels";
import { useEffect, useState } from "react";
import { DesktopLink } from "./desktop-link";
import { Button } from "./ui/button";
import { SourceReferences } from "./source-references";
import type { Citation } from "@/lib/material-types";
import { statisticsApi, mlApi } from "@/lib/api-endpoints";
import type { PreviewScope, ReadEvidence } from "@/lib/preview-types";
import type {
  MLExperimentCreateRequest,
  StatisticalRunRequest,
} from "@/lib/types";

function scalar(value: unknown, t: AppTranslator) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toPrecision(6)
    : t("common.unavailable");
}

function ReadSummary({
  item,
  scope,
}: {
  item: ReadEvidence;
  scope: PreviewScope;
}) {
  const { formatNumber } = useLanguage();
  const source = item.binding?.source;
  const categories = {
    project_context: "project",
    field_explanation: "schema",
    quality_summary: "quality",
  };
  const same =
    source &&
    item.binding.tool === item.kind &&
    item.binding.category === categories[item.kind] &&
    source.project_id === scope.project_id &&
    (source.dataset_id ?? null) === (scope.dataset_id ?? null) &&
    (source.dataset_sha256 ?? null) === (scope.dataset_sha256 ?? null) &&
    (source.schema_sha256 ?? null) === (scope.schema_sha256 ?? null) &&
    JSON.stringify([...(source.columns ?? [])].sort()) ===
      JSON.stringify([...(scope.columns ?? [])].sort());
  if (!same)
    return (
      <p role="alert">
        <UiMessage id="ui.preview.theToolSummarySourceDoesNotMatchTheCurrentContextContentIsNotDisplayed" />
      </p>
    );
  const href = source.dataset_id
    ? `/projects/${source.project_id}/data/${source.dataset_id}`
    : `/projects/${source.project_id}`;
  return (
    <section className="space-y-2 rounded border p-3">
      <h3 className="font-semibold">
        {" "}
        <UiMessage id="ui.preview.approvedLocalToolSummary" />{" "}
        {item.kind === "project_context" ? (
          <UiMessage id="ui.displayBranch.preview.project" />
        ) : item.kind === "field_explanation" ? (
          <UiMessage id="ui.displayBranch.preview.fields" />
        ) : (
          <UiMessage id="ui.displayBranch.preview.dataQuality" />
        )}
      </h3>
      {item.kind === "project_context" ? (
        <>
          <p>{item.summary.title}</p>
          <p>
            {item.summary.description ?? (
              <UiMessage id="ui.displayBranch.preview.researchNotesMissing" />
            )}
          </p>
        </>
      ) : item.kind === "field_explanation" ? (
        <ul>
          {item.summary.columns.map((column) => (
            <li key={column.name}>
              {column.name}
              <UiMessage id="ui.preview.physicalType" /> {column.dtype}
              <UiMessage id="ui.preview.inferredType" /> {column.inferred_type}
              。
            </li>
          ))}
        </ul>
      ) : (
        <>
          <p>
            <UiMessage id="ui.preview.rowsReadLocally" />
            {formatNumber(item.summary.row_count)}。
          </p>
          <ul>
            {Object.entries(item.summary.null_counts).map(([column, count]) => (
              <li key={column}>
                {column}
                <UiMessage id="ui.preview.missing" /> {formatNumber(count)}。
              </li>
            ))}
          </ul>
          <p>
            <UiMessage id="ui.preview.rawRowsLabelSamplesAndAutomaticResearchConclusionsAreNotDisplayed" />
          </p>
        </>
      )}
      <p className="break-all text-xs">
        {" "}
        <UiMessage id="ui.preview.project" />
        {source.project_id}
        {source.dataset_id && (
          <>
            {" "}
            <UiMessage id="ui.preview.data" />
            {source.dataset_id}
            <br /> <UiMessage id="ui.preview.dataSha" />
            {source.dataset_sha256}
          </>
        )}
      </p>
      <DesktopLink className="text-primary underline" href={href}>
        {" "}
        <UiMessage id="ui.preview.locateTheReadSource" />{" "}
      </DesktopLink>
      <p className="text-xs text-muted-foreground">
        {" "}
        <UiMessage id="ui.preview.thisTemplateShowsSummariesActuallyReadAndSavedAfterThisTurnSApprovalAFinishedSessionDoesNotMeanANewBusinessRunWasCreatedModelFreeTextIsUnpublished" />{" "}
      </p>
    </section>
  );
}

/** Templates use saved records; provider free text is never rendered here. */
function SavedFact({
  fact,
  scope,
}: {
  fact: Record<string, unknown>;
  scope: PreviewScope;
}) {
  const { t, formatNumber } = useLanguage();
  const [detail, setDetail] = useState<{
    params: unknown;
    warnings: string[];
    limits: string[];
  } | null>(null);
  const [error, setError] = useState<React.ReactNode>("");
  const id = typeof fact.run_id === "string" ? fact.run_id : "";
  const kind = fact.kind;
  const matches =
    id &&
    fact.project_id === scope.project_id &&
    fact.dataset_id === scope.dataset_id &&
    fact.dataset_sha256 === scope.dataset_sha256 &&
    fact.schema_sha256 === scope.schema_sha256 &&
    scope.dataset_id;
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setError("");
    if (!matches) return () => controller.abort();
    const load = async () => {
      if (kind === "statistics") {
        const run = await statisticsApi.getRun(id, controller.signal);
        if (
          run.id !== id ||
          run.project_id !== scope.project_id ||
          run.dataset_id !== scope.dataset_id ||
          run.dataset_sha256 !== scope.dataset_sha256
        )
          throw new Error();
        return {
          params: {
            ...run.input_config,
            missing_policy: run.missing_value_policy,
          },
          warnings: run.result?.warnings ?? [],
          limits: run.result?.limitations ?? [],
        };
      }
      const run = await mlApi.getRun(id, controller.signal);
      const experiment = await mlApi.getExperiment(
        run.experiment_id,
        controller.signal,
      );
      if (
        run.id !== id ||
        experiment.project_id !== scope.project_id ||
        experiment.dataset_id !== scope.dataset_id ||
        experiment.dataset_sha256 !== scope.dataset_sha256
      )
        throw new Error();
      return {
        params: experiment.config,
        warnings: run.leakage_checks
          .filter((c) => !c.passed)
          .map((c) => c.title),
        limits: ["ML 分数为保存的计算结果，不能证明外部数据泛化效果。"],
      };
    };
    void load()
      .then((value) => {
        if (!controller.signal.aborted) setDetail(value);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            <UiMessage id="ui.residual.workspace.savedRecordSourceUnavailable" />,
          );
      });
    return () => controller.abort();
  }, [
    id,
    kind,
    matches,
    scope.project_id,
    scope.dataset_id,
    scope.dataset_sha256,
  ]);
  if (!matches)
    return (
      <p role="alert">
        <UiMessage id="ui.preview.theResultSourceDoesNotMatchTheCurrentDataVersionValuesAreNotDisplayed" />
      </p>
    );
  if (!detail)
    return (
      <p role={error ? "alert" : "status"}>
        {error || (
          <UiMessage id="ui.displayBranch.preview.verifyingSavedEvidence" />
        )}
      </p>
    );
  const target = kind === "statistics" ? "statistics" : "machine-learning";
  const href = `/projects/${scope.project_id}/${target}?datasetId=${scope.dataset_id}&runId=${id}`;
  return (
    <section className="space-y-2 rounded border p-3">
      <h3 className="font-semibold">
        {" "}
        <UiMessage id="ui.preview.calculatedFacts" />{" "}
        {kind === "statistics" ? (
          String(fact.method)
        ) : (
          <UiMessage id="ui.common.ml" />
        )}
      </h3>
      <p>
        {" "}
        <UiMessage id="ui.preview.businessStatus" />
        {workflowStatusLabel(
          typeof fact.status === "string" ? fact.status : null,
          t,
        )}
        <UiMessage id="ui.preview.effectiveSampleN" />
        {typeof fact.n === "number"
          ? formatNumber(fact.n)
          : t("common.unavailable")}
        。
      </p>
      {kind === "statistics" ? (
        <p>
          {String(fact.statistic_name ?? t("preview.statisticLabel"))}=
          {scalar(fact.statistic, t)}
          <UiMessage id="ui.preview.p" /> {scalar(fact.p_value, t)}
          <UiMessage id="ui.preview.noResearchConclusionIsDrawnAutomaticallyFromThePValue" />{" "}
        </p>
      ) : (
        <>
          <p>
            <UiMessage id="ui.preview.primaryMetric" />
            {String(fact.primary_metric ?? t("common.unavailable"))}
          </p>
          {Array.isArray(fact.scores) &&
            fact.scores.map((score, index) => {
              const row = score as { model_id?: string; score?: number };
              return (
                <p key={index}>
                  {row.model_id}：{scalar(row.score, t)}
                </p>
              );
            })}
        </>
      )}
      <p className="break-all text-xs">
        {" "}
        <UiMessage id="ui.preview.run" />
        {id}
        <br /> <UiMessage id="ui.preview.dataSha" />
        {String(fact.dataset_sha256)}
      </p>
      <DesktopLink className="text-primary underline" href={href}>
        {" "}
        <UiMessage id="ui.preview.locateTheActualSourceRecord" />{" "}
      </DesktopLink>
      {detail && (
        <>
          <details>
            <summary>
              <UiMessage id="ui.preview.savedParametersLocalViewingOnly" />
            </summary>
            <pre className="overflow-auto whitespace-pre-wrap text-xs">
              {JSON.stringify(detail.params, null, 2)}
            </pre>
          </details>
          {detail.warnings.map((warning, index) => (
            <p key={index}>
              <UiMessage id="ui.preview.warning" />
              <UiMessage id="diagnostic.original" />: {warning}
            </p>
          ))}
          {detail.limits.map((limit, index) => (
            <p key={index}>
              <UiMessage id="ui.preview.limitations" />
              <UiMessage id="diagnostic.original" />: {limit}
            </p>
          ))}
        </>
      )}
      {error && <p role="alert">{error}</p>}
      <p className="text-xs text-muted-foreground">
        {" "}
        <UiMessage id="ui.preview.trustedTemplatesReadCalculationRecordsModelNaturalLanguageInterpretationsAreUnpublishedAndUnverified" />{" "}
      </p>
    </section>
  );
}

export function PreviewEvidence({
  evidence,
  scope,
  onApplyStatistics,
  onApplyML,
}: {
  evidence: Record<string, unknown>[];
  scope: PreviewScope;
  onApplyStatistics?: (spec: StatisticalRunRequest) => void;
  onApplyML?: (spec: MLExperimentCreateRequest) => void;
}) {
  return (
    <div className="space-y-3">
      {evidence.map((item, index) => {
        if (
          (item.kind === "project_context" ||
            item.kind === "field_explanation" ||
            item.kind === "quality_summary") &&
          item.state === "observed"
        )
          return (
            <ReadSummary
              key={index}
              item={item as ReadEvidence}
              scope={scope}
            />
          );
        if (item.kind === "source_references" && Array.isArray(item.citations))
          return (
            <SourceReferences
              key={index}
              citations={item.citations as Citation[]}
              scope={scope}
            />
          );
        if (item.kind === "statistics" || item.kind === "ml")
          return (
            <SavedFact
              key={`${item.kind}:${item.run_id}:${scope.project_id}:${scope.dataset_sha256}`}
              fact={item}
              scope={scope}
            />
          );
        if (item.kind === "plan_proposal")
          return (
            <p key={index}>
              {" "}
              <UiMessage id="ui.preview.theBoundedPlanIsSavedButNotCalculatedSelectItsRevisionInPlansAndTemplates" />{" "}
              {String(item.revision)}
              <UiMessage id="ui.preview.editItAndApproveExecution" />{" "}
            </p>
          );
        if (item.kind === "validated_explanation") {
          const source = item.source as Partial<PreviewScope> | undefined;
          const same =
            source?.project_id === scope.project_id &&
            source.dataset_id === scope.dataset_id &&
            source.dataset_sha256 === scope.dataset_sha256 &&
            source.schema_sha256 === scope.schema_sha256;
          if (!same || !Array.isArray(item.fields))
            return (
              <p role="alert" key={index}>
                {" "}
                <UiMessage id="ui.preview.interpretationSourceMismatchFieldsAreNotDisplayed" />{" "}
              </p>
            );
          return (
            <section className="space-y-2 border rounded p-3" key={index}>
              <h3 className="font-semibold">
                <UiMessage id="ui.preview.researchInterpretationFieldReferencesChecked" />
              </h3>
              {item.fields.map((value, i) => {
                const field = value as {
                  field: string;
                  run_id: string;
                  value: unknown;
                  text: string;
                  availability: string;
                };
                return (
                  <div key={i}>
                    <p>{field.text}</p>
                    <p className="text-xs break-all">
                      {field.field}：
                      {field.availability === "unavailable" ? (
                        <UiMessage id="ui.displayBranch.preview.unrecordedUnavailable" />
                      ) : typeof field.value === "object" ? (
                        JSON.stringify(field.value)
                      ) : (
                        String(field.value)
                      )}
                      <br /> <UiMessage id="ui.preview.run" />
                      {field.run_id}
                    </p>
                  </div>
                );
              })}
              <p className="text-xs">
                {" "}
                <UiMessage id="ui.preview.valuesAndStatusesAreRenderedFromSavedRecordsModelGeneratedFactsOrCausalConclusionsAreUnpublished" />{" "}
              </p>
              {Array.isArray(item.citations) && (
                <SourceReferences
                  citations={item.citations as Citation[]}
                  scope={scope}
                />
              )}
            </section>
          );
        }
        if (
          item.kind === "statistics_proposal" ||
          item.kind === "ml_proposal"
        ) {
          const sameVersion =
            item.project_id === scope.project_id &&
            item.dataset_id === scope.dataset_id &&
            item.dataset_sha256 === scope.dataset_sha256 &&
            item.schema_sha256 === scope.schema_sha256;
          const apply =
            item.kind === "statistics_proposal" ? onApplyStatistics : onApplyML;
          return (
            <section key={index} className="space-y-2 rounded border p-3">
              <h3 className="font-semibold">
                <UiMessage id="ui.preview.analysisProposalNotYetCalculated" />
              </h3>
              <pre className="overflow-auto whitespace-pre-wrap text-xs">
                {JSON.stringify(item.config, null, 2)}
              </pre>
              {apply && (
                <Button
                  size="sm"
                  disabled={!sameVersion}
                  onClick={() => {
                    if (item.kind === "statistics_proposal")
                      onApplyStatistics?.(item.config as StatisticalRunRequest);
                    else onApplyML?.(item.config as MLExperimentCreateRequest);
                  }}
                >
                  {" "}
                  <UiMessage id="ui.preview.fillTheExistingFormForManualConfirmation" />{" "}
                </Button>
              )}
              {!apply && (
                <p>
                  {" "}
                  <UiMessage id="ui.preview.selectTheSameDatasetOnTheCorrespondingStatisticsOrMlPageBeforeProposingOtherPagesAreNotChangedAutomatically" />{" "}
                </p>
              )}
              {!sameVersion && (
                <p role="alert">
                  <UiMessage id="ui.preview.theProposalSourceChangedTheFormCannotBePopulated" />
                </p>
              )}
            </section>
          );
        }
        return (
          <p key={index}>
            {" "}
            <UiMessage id="ui.preview.toolStatus" />
            {String(item.state ?? "未完成")} {String(item.error_code ?? "")}
          </p>
        );
      })}
    </div>
  );
}
