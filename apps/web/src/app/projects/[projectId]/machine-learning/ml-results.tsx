"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { workflowEnumLabel } from "@/lib/workflow-labels";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage } from "@/providers/language-provider";

import { Message as UiMessage } from "@/components/message";
import { SavedDiagnostic } from "@/components/saved-diagnostic";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import dynamic from "next/dynamic";
import { useId, useState } from "react";
import { CheckIcon, DownloadIcon, XIcon } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { DownloadButton } from "@/components/download-button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BASE_TEXT_STYLE, useIsClient } from "@/components/charts/effect";

import { mlApi } from "@/lib/api-endpoints";
import { localizedMetaLabel, ML_RUN_STATUS_META } from "@/lib/format";
import type { MLExperiment, MLRun } from "@/lib/types";
import { PredictionDiagnostics } from "./ml-prediction-diagnostics";
import {
  availableMetricKeys,
  confusionMatrixData,
  displayCount,
  displayMetric,
  initialMetrics,
  initialPrimaryMetric,
  isFiniteMetric,
  metricDescription,
  metricLabel,
  evaluationProtocol,
  verifiedEvaluation,
  comparisonRanks,
} from "./ml-display";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => (
    <div className="flex h-48 w-full items-center justify-center text-xs text-muted-foreground">
      {" "}
      <UiMessage id="ui.ml.loadingChart" />{" "}
    </div>
  ),
});

function ComparisonTable({
  run,
  metrics,
  primary,
  verified,
}: {
  run: MLRun;
  metrics: string[];
  primary: string;
  verified: boolean;
}) {
  const { t: uiCopyT } = useCopyLanguage();

  const rows = run.models.filter((model) => model.status === "completed");
  const ranks = comparisonRanks(run);
  if (verified)
    rows.sort(
      (a, b) =>
        (ranks.get(a.model_id) ?? Infinity) -
        (ranks.get(b.model_id) ?? Infinity),
    );
  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {" "}
        <UiMessage id="ui.ml.noCompletedModelResultsCheckTheRunStatusAndFailureDetails" />{" "}
      </p>
    );
  }
  const cvMetric = run.comparison?.primary_metric;

  return (
    <UiAttributes
      messages={{
        "aria-label": { id: "ui.ml.modelMetricsTableHorizontallyScrollable" },
      }}
    >
      <div
        aria-label={"模型指标表，可横向滚动"}
        className="overflow-x-auto rounded-md border"
        tabIndex={0}
      >
        <UiAttributes
          messages={{ "aria-label": { id: "ui.ml.savedModelMetrics" } }}
        >
          <table
            aria-label={"已保存的模型指标"}

            className="w-full min-w-[56rem] border-collapse text-sm"
          >
            <caption className="sr-only">
              {" "}
              <UiMessage id="ui.ml.testSetMetricsSavedByThisRunShownInModelRecordOrderCvMeansAndBetweenFoldSdAreListedSeparately" />{" "}
            </caption>
            <thead>
              <tr className="border-b bg-muted/40 text-left text-muted-foreground">
                <th scope="col" className="px-3 py-2 font-medium">
                  {" "}
                  <UiMessage id="ui.ml.model" />{" "}
                </th>
                {verified && (
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {" "}
                    <UiMessage id="ui.ml.savedScoringRank" />{" "}
                  </th>
                )}
                {metrics.map((key) => (
                  <th
                    scope="col"
                    key={key}
                    className={`px-3 py-2 text-right font-medium ${key === primary ? "bg-primary/5 text-foreground" : ""}`}
                  >
                    {" "}
                    <UiMessage id="ui.ml.test" /> {metricLabel(key, uiCopyT)}
                  </th>
                ))}
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {" "}
                  <UiMessage id="ui.ml.cvMean" />{" "}
                  {cvMetric ? (
                    metricLabel(cvMetric, uiCopyT)
                  ) : (
                    <UiMessage id="ui.displayBranch.ml.metricNotReturned" />
                  )}
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {" "}
                  <UiMessage id="ui.ml.cvSdBetweenFoldsNotCi" />{" "}
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {" "}
                  <UiMessage id="ui.ml.cvFoldCount" />{" "}
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {" "}
                  <UiMessage id="ui.ml.trainingSeconds" />{" "}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((model) => (
                <tr key={model.model_id} className="border-b last:border-0">
                  <td className="px-3 py-2 font-medium">
                    {workflowEnumLabel(model.model_id, uiCopyT)}
                  </td>
                  {verified && (
                    <td className="px-3 py-2 text-right tabular-nums">
                      {displayCount(ranks.get(model.model_id), uiCopyT)}
                    </td>
                  )}
                  {metrics.map((key) => (
                    <td
                      key={key}
                      className={`px-3 py-2 text-right tabular-nums ${key === primary ? "bg-primary/5 font-medium" : ""}`}
                    >
                      {displayMetric(
                        model.test_metrics?.[key],
                        undefined,
                        uiCopyT,
                      )}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-right tabular-nums">
                    {displayMetric(model.cv?.mean, undefined, uiCopyT)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {displayMetric(model.cv?.std, undefined, uiCopyT)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {displayCount(model.cv?.n_splits, uiCopyT)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {displayMetric(model.training_seconds, 2, uiCopyT)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </UiAttributes>
      </div>
    </UiAttributes>
  );
}

function FeatureImportanceChart({
  features,
}: {
  features: Record<string, number>;
}) {
  const { t: uiCopyT } = useCopyLanguage();

  const { t, formatNumber } = useCopyLanguage();
  const isClient = useIsClient();
  if (!isClient) return null;
  const entries = Object.entries(features)
    .filter(([, value]) => isFiniteMetric(value))
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 12);
  if (entries.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {" "}
        <UiMessage id="ui.ml.thisModelHasNoAvailableFeatureImportance" />{" "}
      </p>
    );
  }
  const option = {
    backgroundColor: "#ffffff",
    textStyle: { ...BASE_TEXT_STYLE, color: "#172337" },
    grid: { left: 110, right: 24, top: 8, bottom: 24 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown) =>
        typeof value === "number"
          ? formatNumber(value, { maximumSignificantDigits: 17 })
          : String(value),
    },
    aria: { enabled: true, description: t("chart.diagnostics.saved") },
    xAxis: { type: "value", axisLabel: { color: "#172337" } },
    yAxis: {
      type: "category",
      data: entries.map((e) => e[0]),
      inverse: true,
      axisLabel: { color: "#172337" },
    },
    series: [
      {
        type: "bar",
        data: entries.map((e) => e[1]),
        itemStyle: { color: "#355e80" },
      },
    ],
  };
  return (
    <div className="space-y-2">
      <ReactECharts
        option={option}
        style={{ height: Math.max(140, entries.length * 26) }}
      />
      <details className="text-xs">
        <summary className="w-fit cursor-pointer py-1 focus-visible:outline-2 focus-visible:outline-primary">
          {" "}
          <UiMessage id="ui.ml.inspectFeatureImportanceValues" />{" "}
        </summary>
        <UiAttributes
          messages={{
            "aria-label": { id: "ui.ml.savedFeatureImportanceValuesTable" },
          }}
        >
          <table
            aria-label={"保存的特征重要性数值表"}
            className="w-full tabular-nums"
          >
            <thead>
              <tr>
                <th scope="col" className="text-left">
                  {" "}
                  <UiMessage id="ui.ml.features" />{" "}
                </th>
                <th scope="col" className="text-right">
                  {" "}
                  <UiMessage id="ui.ml.savedValue" />{" "}
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map(([name, value]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="text-right">
                    {displayMetric(value, undefined, uiCopyT)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </UiAttributes>
      </details>
    </div>
  );
}

function ConfusionMatrix({ cm, labels }: { cm: number[][]; labels: string[] }) {
  return (
    <div className="overflow-x-auto">
      <UiAttributes
        messages={{ "aria-label": { id: "ui.ml.savedConfusionMatrix" } }}
      >
        <table
          aria-label={"保存的混淆矩阵"}
          className="border-collapse text-xs"
        >
          <thead>
            <tr>
              <th className="p-1" />
              {labels.map((l) => (
                <th
                  key={l}
                  className="p-1.5 text-center font-medium text-muted-foreground"
                >
                  {" "}
                  <UiMessage id="ui.ml.predicted" /> {l}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cm.map((row, i) => (
              <tr key={i}>
                <td className="p-1.5 font-medium text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.ml.actual" /> {labels[i]}
                </td>
                {row.map((v, j) => (
                  <td
                    key={j}
                    className={`border p-1.5 text-center tabular-nums ${i === j ? "bg-primary/10" : ""}`}
                  >
                    {v}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </UiAttributes>
    </div>
  );
}

export function MLResultsView({
  run,
  experiment = null,
}: {
  run: MLRun;
  experiment?: MLExperiment | null;
}) {
  const { t: uiCopyT } = useCopyLanguage();

  const source = experiment?.id === run.experiment_id ? experiment : null;
  const task = source?.task_type ?? null;
  const metricKeys = availableMetricKeys(run, task);
  const [primary, setPrimary] = useState(() =>
    initialPrimaryMetric(metricKeys, task),
  );
  const { t: mlT, locale } = useLanguage();
  const [selectedMetrics, setSelectedMetrics] = useState(() =>
    initialMetrics(metricKeys, task),
  );
  const primaryMetric = metricKeys.includes(primary)
    ? primary
    : initialPrimaryMetric(metricKeys, task);
  const visibleMetrics = [
    primaryMetric,
    ...selectedMetrics.filter(
      (key) => key !== primaryMetric && metricKeys.includes(key),
    ),
  ].filter(Boolean);
  const controlId = useId();
  const completed = run.models.filter((m) => m.status === "completed");
  const incomplete = run.models.filter((m) => m.status !== "completed");
  const leakageFailed = run.leakage_checks.filter((c) => !c.passed);
  const split = run.split ?? {};
  const protocol = evaluationProtocol(run);
  const verified =
    verifiedEvaluation(run) &&
    (!source || source.dataset_sha256 === protocol?.dataset_sha256);
  const baseline = verified ? run.comparison?.baseline : undefined;
  const sourceDetails = (
    <details className="border-t pt-3">
      <summary className="cursor-pointer text-sm font-medium">
        {locale === "zh-CN" ? "运行信息与下载" : "Run details and download"}
      </summary>
      <UiAttributes
        messages={{ "aria-label": { id: "ui.ml.savedRunAndSource" } }}
      >
        <section
          aria-label={"保存的 Run 与来源"}

          className="space-y-3 border-b pb-4 text-sm"
        >
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="font-medium">
              <UiMessage id="ui.ml.savedRun" />
            </h3>
            <span className="break-all font-mono text-xs">{run.id}</span>
            <StatusBadge
              label={localizedMetaLabel(
                ML_RUN_STATUS_META[run.status] ?? { label: run.status },
                uiCopyT,
              )}
              tone={
                run.status === "failed"
                  ? "red"
                  : run.status === "completed"
                    ? "teal"
                    : "amber"
              }
            />
            {run.artifact_key && (
              <DownloadButton
                identity={run.id + run.artifact_key}
                download={(signal) => mlApi.downloadArtifact(run.id, signal)}
                className="ml-auto inline-flex min-h-9 items-center gap-1 rounded-md border px-3 font-medium transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"
              >
                <DownloadIcon className="size-4" aria-hidden="true" />{" "}
                <UiMessage id="ui.ml.downloadModelArtifact" />{" "}
              </DownloadButton>
            )}
          </div>
          {source ? (
            <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted-foreground">
                  <UiMessage id="ui.ml.sourceDataset" />
                </dt>
                <dd className="break-all font-mono text-xs">
                  {source.dataset_id}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  <UiMessage id="ui.ml.dataSha256" />
                </dt>
                <dd className="break-all font-mono text-xs">
                  {source.dataset_sha256 || mlT("common.unavailable")}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  <UiMessage id="ui.ml.targetVariable" />
                </dt>
                <dd>{source.target_variable || mlT("common.unavailable")}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  <UiMessage id="ui.ml.features" />
                </dt>
                <dd className="break-words">
                  {source.feature_variables.join(", ") ||
                    mlT("common.unavailable")}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.ml.runSourceUnverifiedNoExperimentRecordMatchesThisRun" />{" "}
            </p>
          )}
          <dl className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
            <div>
              <dt className="text-muted-foreground">
                <UiMessage id="ui.ml.savedSplitStrategy" />
              </dt>
              <dd>
                {run.validation_strategy
                  ? workflowEnumLabel(run.validation_strategy, mlT)
                  : mlT("common.unavailable")}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">
                <UiMessage id="ui.ml.sampleCount" />
              </dt>
              <dd className="tabular-nums">
                {displayCount(run.n_observations, uiCopyT)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">
                <UiMessage id="ui.ml.trainingValidationTest" />
              </dt>
              <dd className="tabular-nums">
                {displayCount(split.n_train, uiCopyT)} /{" "}
                {displayCount(split.n_val, uiCopyT)} /{" "}
                {displayCount(split.n_test, uiCopyT)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">
                <UiMessage id="ui.ml.randomSeed" />
              </dt>
              <dd className="tabular-nums">
                {displayCount(run.random_seed, uiCopyT)}
              </dd>
            </div>
          </dl>
          <p className="text-xs text-muted-foreground">
            {verified ? (
              <UiMessage id="ui.displayBranch.ml.protocolSavedSameRun" />
            ) : (
              <UiMessage id="ui.displayBranch.ml.allocationIdentifierMissing" />
            )}
          </p>
          <details className="text-xs">
            <summary className="w-fit cursor-pointer rounded-sm py-1 font-medium focus-visible:outline-2 focus-visible:outline-primary">
              {" "}
              <UiMessage id="ui.ml.inspectSavedConfigurationAndSoftwareVersions" />{" "}
            </summary>
            <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono">
              {JSON.stringify(
                {
                  experiment_id: run.experiment_id,
                  config: source?.config ?? null,
                  software_versions: run.software_versions,
                  evaluation_protocol: protocol,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </section>
      </UiAttributes>
    </details>
  );

  return (
    <div className="space-y-4">
      {run.status === "failed" && sourceDetails}
      {run.status !== "failed" && (
        <>
          {/* Leakage warnings */}
          {leakageFailed.length > 0 && (
            <div
              role="alert"
              className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
            >
              <p className="font-semibold">
                <UiMessage id="ui.ml.savedChecksDetectedPotentialDataLeakage" />
              </p>
              {leakageFailed.map((c) => (
                <div key={c.rule_id}>
                  <span className="font-mono">
                    [{c.severity}] · {c.rule_id}
                  </span>
                  <SavedDiagnostic open>
                    {c.title} — {c.detail}
                  </SavedDiagnostic>
                </div>
              ))}
            </div>
          )}

          {/* Comparison */}
          <section
            aria-labelledby={`${controlId}-heading`}
            className="space-y-3"
          >
            <h3 id={`${controlId}-heading`} className="text-lg font-medium">
              {" "}
              <UiMessage id="ui.ml.modelMetrics" />{" "}
            </h3>
            <p className="max-w-prose text-xs text-muted-foreground">
              {verified ? (
                <UiMessage id="ui.displayBranch.ml.protocolVerifiedRanking" />
              ) : (
                <UiMessage id="ui.displayBranch.ml.protocolUnverifiedNoRanking" />
              )}
            </p>
            <ComparisonTable
              run={run}
              metrics={visibleMetrics}
              primary={primaryMetric}
              verified={verified}
            />
            {metricKeys.length > 0 && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-end gap-3">
                  <div className="space-y-1">
                    <label
                      htmlFor={`${controlId}-primary`}
                      className="block text-sm font-medium"
                    >
                      {" "}
                      <UiMessage id="ui.ml.primaryDisplayedMetric" />{" "}
                    </label>
                    <SearchSelect
                      id={`${controlId}-primary`}
                      value={primaryMetric}
                      onChange={(event) => setPrimary(event.target.value)}
                      aria-describedby={`${controlId}-metric-description`}
                      className="h-9 min-w-40 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-primary"
                    >
                      {metricKeys.map((key) => (
                        <option key={key} value={key}>
                          {metricLabel(key, uiCopyT)}
                        </option>
                      ))}
                    </SearchSelect>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {" "}
                    <UiMessage id="ui.ml.displayChangesOnlyNoRecalculationOrChangeToTheSavedCvScoringMetric" />{" "}
                  </p>
                </div>
                <p
                  id={`${controlId}-metric-description`}
                  className="text-xs text-muted-foreground"
                >
                  {metricLabel(primaryMetric, uiCopyT)}：
                  {metricDescription(primaryMetric, uiCopyT)}{" "}
                  <UiMessage id="ui.ml.theTableShowsTestSetResults" />{" "}
                </p>
                <fieldset className="space-y-1">
                  <legend className="text-xs font-medium">
                    <UiMessage id="ui.ml.otherDisplayedMetrics" />
                  </legend>
                  <div className="flex flex-wrap gap-x-4 gap-y-2">
                    {metricKeys.map((key) => (
                      <label
                        key={key}
                        className="flex min-h-9 cursor-pointer items-center gap-2 text-xs"
                      >
                        <UiAttributes
                          messages={{
                            "aria-label": {
                              id: "ui.ml.showValue",
                              values: {
                                value0: String(metricLabel(key, uiCopyT)),
                              },
                            },
                          }}
                        >
                          <input
                            aria-label={"显示 {value0}".replaceAll(
                              "{value0}",
                              String(metricLabel(key, uiCopyT)),
                            )}
                            type="checkbox"

                            checked={visibleMetrics.includes(key)}
                            disabled={key === primaryMetric}
                            onChange={(event) =>
                              setSelectedMetrics((old) =>
                                event.target.checked
                                  ? Array.from(new Set([...old, key]))
                                  : old.filter((value) => value !== key),
                              )
                            }
                            className="size-4 accent-primary focus-visible:outline-2 focus-visible:outline-primary"
                          />
                        </UiAttributes>
                        {metricLabel(key, uiCopyT)}
                        {key === primaryMetric ? (
                          <UiMessage id="ui.displayBranch.ml.primarySuffix" />
                        ) : (
                          ""
                        )}
                      </label>
                    ))}
                  </div>
                </fieldset>
              </div>
            )}
            {baseline &&
              baseline.evaluation_protocol_id === protocol?.protocol_id && (
                <UiAttributes
                  messages={{
                    "aria-label": { id: "ui.ml.baselineUnderTheSameProtocol" },
                  }}
                >
                  <section
                    aria-label={"同协议基线"}

                    className="space-y-2 border-t pt-3"
                  >
                    <h4 className="text-sm font-medium">
                      {" "}
                      <UiMessage id="ui.ml.dummyBaseline" />{" "}
                      {baseline.strategy === "mean" ? (
                        <UiMessage id="ui.displayBranch.ml.baselineTrainingMean" />
                      ) : (
                        <UiMessage id="ui.displayBranch.ml.baselineTrainingClassProportions" />
                      )}
                    </h4>
                    {baseline.status === "completed" ? (
                      <dl className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
                        {visibleMetrics.map((key) => (
                          <div key={key}>
                            <dt className="text-muted-foreground">
                              {" "}
                              <UiMessage id="ui.ml.test" />{" "}
                              {metricLabel(key, uiCopyT)}
                            </dt>
                            <dd className="text-sm tabular-nums">
                              {displayMetric(
                                run.comparison?.baseline?.test_metrics?.[key],
                                undefined,
                                uiCopyT,
                              )}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        {" "}
                        <UiMessage id="ui.ml.theBaselineWasNotCalculatedSuccessfullyNoMetricsAreFabricated" />{" "}
                      </p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      {" "}
                      <UiMessage id="ui.ml.usesTheSameTrainingAndTestAllocationHigherPredictiveMetricsDoNotEstablishCausation" />{" "}
                    </p>
                  </section>
                </UiAttributes>
              )}
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.ml.unavailableMeansMissingNotCalculatedOrNonFiniteRatherThan0CvIsTheMeanOfFoldScoresSdMeasuresDifferencesBetweenFoldsNotAConfidenceIntervalOrUncertaintyOfTestMetrics" />{" "}
            </p>
          </section>

          {sourceDetails}

          {/* Per-model details */}
          <div className="grid gap-4 2xl:grid-cols-2">
            {completed.map((m) => {
              const tm = m.test_metrics ?? {};
              const matrix = confusionMatrixData(tm);
              const importance =
                (m.interpretation?.original_features &&
                  Object.keys(m.interpretation.original_features).length > 0 &&
                  m.interpretation.original_features) ||
                m.interpretation?.permutation ||
                {};
              return (
                <Card key={m.model_id}>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <span>{workflowEnumLabel(m.model_id, uiCopyT)}</span>
                      <StatusBadge
                        label={localizedMetaLabel(
                          ML_RUN_STATUS_META[m.status] ?? { label: m.status },
                          uiCopyT,
                        )}
                        tone="teal"
                      />
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <PredictionDiagnostics data={m.prediction_diagnostics} />
                    {m.validation_metrics && (
                      <div>
                        <p className="mb-2 text-xs font-medium">
                          {" "}
                          <UiMessage id="ui.ml.validationSetIndependentOfTest" />{" "}
                        </p>
                        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
                          {visibleMetrics.map((key) => (
                            <div key={key}>
                              <dt className="text-xs text-muted-foreground">
                                {metricLabel(key, uiCopyT)}
                              </dt>
                              <dd className="text-sm tabular-nums">
                                {displayMetric(
                                  m.validation_metrics?.[key],
                                  undefined,
                                  uiCopyT,
                                )}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    )}
                    {matrix && (
                      <div>
                        <p className="mb-1 text-xs font-medium text-muted-foreground">
                          {" "}
                          <UiMessage id="ui.ml.testConfusionMatrix" />{" "}
                        </p>
                        <ConfusionMatrix
                          cm={matrix.cm}
                          labels={matrix.labels}
                        />
                      </div>
                    )}

                    <div>
                      <p className="mb-1 text-xs font-medium text-muted-foreground">
                        {" "}
                        <UiMessage id="ui.ml.savedFeatureImportance" />{" "}
                        {m.interpretation?.method
                          ? ` (${m.interpretation.method})`
                          : ""}
                      </p>
                      <FeatureImportanceChart features={importance} />
                    </div>
                  </CardContent>
                </Card>
              );
            })}

            {incomplete.map((m) => (
              <Card key={m.model_id}>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <span>{workflowEnumLabel(m.model_id, uiCopyT)}</span>
                    <StatusBadge
                      label={
                        m.status
                          ? localizedMetaLabel(
                              ML_RUN_STATUS_META[m.status] ?? {
                                label: m.status,
                              },
                              uiCopyT,
                            )
                          : uiCopyT("common.unavailable")
                      }
                      tone={m.status === "failed" ? "red" : "amber"}
                    />
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    <UiMessage id="ui.displayBranch.ml.completedEvaluationNotReturned" />
                  </p>
                  {m.error_message && (
                    <details className="mt-2 text-xs text-muted-foreground">
                      <summary>
                        <UiMessage id="diagnostic.original" />
                      </summary>
                      <p className="mt-1 break-words">{m.error_message}</p>
                    </details>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>

          {/* All leakage checks (informational) */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                <UiMessage id="ui.ml.savedDataLeakageChecks" />
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-xs">
              {run.leakage_checks.length === 0 && (
                <p className="text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.ml.checksWereNotReturnedThisDoesNotEstablishTheAbsenceOfDataLeakage" />{" "}
                </p>
              )}
              {run.leakage_checks.map((c) => (
                <div key={c.rule_id} className="flex items-start gap-2">
                  <span className="inline-flex items-center gap-1">
                    {c.passed ? (
                      <CheckIcon aria-hidden="true" className="size-4" />
                    ) : (
                      <XIcon
                        aria-hidden="true"
                        className="size-4 text-destructive"
                      />
                    )}
                    {c.passed ? (
                      <UiMessage id="ui.displayBranch.common.checkPassed" />
                    ) : (
                      <UiMessage id="ui.displayBranch.common.checkNotPassed" />
                    )}
                  </span>
                  <span>
                    <span className="font-mono">{c.rule_id}</span>
                    <SavedDiagnostic open>{c.detail}</SavedDiagnostic>
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
