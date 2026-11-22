"use client";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import type { MLDiagnosticCurve, MLPredictionDiagnostics } from "@/lib/types";
import { displayCount, displayMetric, isFiniteMetric } from "./ml-display";

function RateFigure({
  title,
  xLabel,
  yLabel,
  x,
  y,
}: {
  title: string;
  xLabel: string;
  yLabel: string;
  x: number[];
  y: number[];
}) {
  const { t: uiCopyT, formatNumber } = useCopyLanguage();

  const points = x
    .map((value, index) => [value, y[index]] as const)
    .filter((pair) =>
      pair.every((value) => isFiniteMetric(value) && value >= 0 && value <= 1),
    );
  if (!points.length)
    return (
      <p className="text-xs text-muted-foreground">
        {title}
        <UiMessage id="ui.ml.noAvailablePoints" />
      </p>
    );
  return (
    <figure className="space-y-2">
      <figcaption className="text-sm font-medium">{title}</figcaption>
      <svg
        viewBox="0 0 420 220"
        role="img"
        aria-label={uiCopyT("chart.aria.axes", { title, x: xLabel, y: yLabel })}
        className="h-auto w-full max-w-lg rounded-sm bg-white text-[#172337]"
      >
        <path d="M48 16V172H390" fill="none" stroke="#8795aa" />
        {[0, 0.5, 1].map((value) => (
          <g key={value}>
            <text
              x={48 + value * 342}
              y="190"
              textAnchor="middle"
              fill="currentColor"
              fontSize="12"
            >
              {formatNumber(value)}
            </text>
            <text
              x="38"
              y={176 - value * 156}
              textAnchor="end"
              fill="currentColor"
              fontSize="12"
            >
              {formatNumber(value)}
            </text>
          </g>
        ))}
        <polyline
          points={points
            .map((pair) => 48 + pair[0] * 342 + "," + (172 - pair[1] * 156))
            .join(" ")}
          fill="none"
          stroke="#355e80"
          strokeWidth="2.5"
        />
        <text
          x="220"
          y="212"
          textAnchor="middle"
          fill="currentColor"
          fontSize="12"
        >
          {xLabel}
        </text>
        <text
          x="12"
          y="100"
          transform="rotate(-90 12 100)"
          textAnchor="middle"
          fill="currentColor"
          fontSize="12"
        >
          {yLabel}
        </text>
      </svg>
      <details className="text-xs">
        <summary className="w-fit cursor-pointer py-1 focus-visible:outline-2 focus-visible:outline-primary">
          {" "}
          <UiMessage id="ui.ml.inspectTheEquivalentValuesTable" />{" "}
        </summary>
        <div className="max-h-48 overflow-auto">
          <table
            className="w-full text-right tabular-nums"
            aria-label={uiCopyT("chart.aria.table", { title })}
          >
            <thead>
              <tr>
                <th scope="col">{xLabel}</th>
                <th scope="col">{yLabel}</th>
              </tr>
            </thead>
            <tbody>
              {points.map((pair, index) => (
                <tr key={index}>
                  <td>{displayMetric(pair[0], undefined, uiCopyT)}</td>
                  <td>{displayMetric(pair[1], undefined, uiCopyT)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

function Curve({
  title,
  curve,
  kind,
}: {
  title: string;
  curve?: MLDiagnosticCurve;
  kind: "roc" | "pr";
}) {
  const { t: uiCopyT } = useCopyLanguage();

  if (!curve || curve.status !== "available")
    return (
      <p className="text-xs text-muted-foreground">
        {title}
        <UiMessage id="ui.ml.theSavedTestSetCannotEstimateThisCurveOrPredictionProbabilitiesWereNotSaved" />{" "}
      </p>
    );
  return (
    <div className="space-y-1">
      <RateFigure
        title={title}
        xLabel={
          kind === "roc"
            ? uiCopyT("chart.axis.falsePositive")
            : uiCopyT("chart.axis.recall")
        }
        yLabel={
          kind === "roc"
            ? uiCopyT("chart.axis.truePositive")
            : uiCopyT("chart.axis.precision")
        }
        x={kind === "roc" ? (curve.fpr ?? []) : (curve.recall ?? [])}
        y={kind === "roc" ? (curve.tpr ?? []) : (curve.precision ?? [])}
      />
      {curve.downsampled && (
        <p className="text-xs text-muted-foreground">
          {" "}
          <UiMessage id="ui.ml.originalCurve" />{" "}
          {displayCount(curve.n_points_original, uiCopyT)}{" "}
          <UiMessage id="ui.ml.pointsSavedDeterministicSamplingRetainsBothEndpoints" />{" "}
        </p>
      )}
    </div>
  );
}

export function PredictionDiagnostics({
  data,
}: {
  data?: MLPredictionDiagnostics;
}) {
  const { t: uiCopyT } = useCopyLanguage();

  if (!data)
    return (
      <p className="text-xs text-muted-foreground">
        {" "}
        <UiMessage id="ui.ml.thisLegacyRunHasNoSavedPredictionDiagnosticsViewingItDoesNotRetrainTheModel" />{" "}
      </p>
    );
  const bins =
    data.calibration?.bins?.filter(
      (bin) =>
        bin.n > 0 &&
        isFiniteMetric(bin.mean_predicted_probability) &&
        isFiniteMetric(bin.positive_fraction),
    ) ?? [];
  return (
    <UiAttributes
      messages={{ "aria-label": { id: "ui.ml.savedPredictionDiagnostics" } }}
    >
      <section
        aria-label={"保存的预测诊断"}
        className="space-y-3 border-t pt-3"
      >
        <h4 className="text-sm font-medium">
          <UiMessage id="ui.ml.testPredictionDiagnostics" />
        </h4>
        <p className="text-xs text-muted-foreground">
          {" "}
          <UiMessage id="ui.ml.savedTestSet" />{" "}
          {displayCount(data.n_evaluated, uiCopyT)}{" "}
          <UiMessage id="ui.ml.samplesTheFigureUsesThisModelSPredictionsAndContainsNoRawPerRowData" />{" "}
        </p>
        {data.kind === "binary_classification" && (
          <div
            className="grid gap-4"
            style={{
              gridTemplateColumns:
                "repeat(auto-fit, minmax(min(100%, 280px), 1fr))",
            }}
          >
            <UiAttributes messages={{ title: { id: "ui.ml.roc" } }}>
              <Curve title={"ROC"} curve={data.roc} kind="roc" />
            </UiAttributes>
            <UiAttributes messages={{ title: { id: "ui.ml.precisionRecall" } }}>
              <Curve title={"Precision–Recall"} curve={data.pr} kind="pr" />
            </UiAttributes>
          </div>
        )}
        {bins.length > 0 && (
          <RateFigure
            title={
              data.calibration?.mode === "top_label_correctness"
                ? uiCopyT("chart.calibration.top")
                : uiCopyT("chart.calibration.positive")
            }
            xLabel={uiCopyT("chart.axis.predictedProbability")}
            yLabel={
              data.calibration?.mode === "top_label_correctness"
                ? uiCopyT("chart.axis.correctFraction")
                : uiCopyT("chart.axis.positiveFraction")
            }
            x={bins.map((bin) => bin.mean_predicted_probability as number)}
            y={bins.map((bin) => bin.positive_fraction as number)}
          />
        )}
        {data.regression && (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 text-xs">
              {["mae", "rmse", "mean_residual", "residual_std"].map((key) => (
                <div key={key}>
                  <dt className="text-muted-foreground">
                    {
                      {
                        mae: "MAE",
                        rmse: "RMSE",
                        mean_residual: uiCopyT("chart.axis.meanResidual"),
                        residual_std: uiCopyT("chart.axis.residualSd"),
                      }[key]
                    }
                  </dt>
                  <dd className="text-sm tabular-nums">
                    {displayMetric(
                      data.regression?.errors[key],
                      undefined,
                      uiCopyT,
                    )}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.ml.residualObservedPredictedSdIsThePopulationStandardDeviationOfTestSetResidualsNotAConfidenceInterval" />{" "}
            </p>
            <details className="text-xs">
              <summary className="w-fit cursor-pointer py-1 focus-visible:outline-2 focus-visible:outline-primary">
                {" "}
                <UiMessage id="ui.ml.inspectTheSavedResidualDistribution" />{" "}
              </summary>
              <div className="overflow-auto">
                <UiAttributes
                  messages={{
                    "aria-label": { id: "ui.ml.residualDistribution" },
                  }}
                >
                  <table
                    aria-label={"残差分布"}
                    className="w-full text-right tabular-nums"
                  >
                    <thead>
                      <tr>
                        <th scope="col">
                          <UiMessage id="ui.ml.lowerIntervalBound" />
                        </th>
                        <th scope="col">
                          <UiMessage id="ui.ml.upperIntervalBound" />
                        </th>
                        <th scope="col">
                          <UiMessage id="ui.ml.sampleCount" />
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.regression.histogram.counts.map((count, index) => (
                        <tr key={index}>
                          <td>
                            {displayMetric(
                              data.regression?.histogram.edges[index],
                              undefined,
                              uiCopyT,
                            )}
                          </td>
                          <td>
                            {displayMetric(
                              data.regression?.histogram.edges[index + 1],
                              undefined,
                              uiCopyT,
                            )}
                          </td>
                          <td>{displayCount(count, uiCopyT)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </UiAttributes>
              </div>
            </details>
          </div>
        )}
      </section>
    </UiAttributes>
  );
}
