import {
  createAppTranslator,
  type AppTranslator,
  type NoParameterMessageKey,
} from "@/lib/i18n-messages";
import { formatStat } from "@/lib/format";
import { ownedRecordValue } from "@/lib/owned-record-value";
import type { MLRun } from "@/lib/types";

export const UNAVAILABLE = "Unavailable";

export function comparisonRanks(run: MLRun): Map<string, number> {
  const result = new Map<string, number>();
  if (!verifiedEvaluation(run)) return result;
  for (const row of run.comparison?.rows ?? []) {
    const model = run.models.find((value) => value.model_id === row.model_id);
    if (
      row.comparable === true &&
      row.evaluation_protocol_id === run.comparison?.evaluation_protocol_id &&
      typeof row.rank_by_primary === "number" &&
      Number.isInteger(row.rank_by_primary) &&
      row.rank_by_primary > 0 &&
      isFiniteMetric(row.primary_value) &&
      model?.test_metrics?.[row.primary_metric] === row.primary_value
    ) {
      result.set(row.model_id, row.rank_by_primary);
    }
  }
  return result;
}

export function evaluationProtocol(run: MLRun) {
  const value = run.split?.evaluation_protocol;
  if (!value || typeof value !== "object") return null;
  const protocol = value as import("@/lib/types").MLEvaluationProtocol;
  if (
    protocol.version !== 1 ||
    !/^[a-f0-9]{64}$/.test(protocol.protocol_id ?? "") ||
    !/^[a-f0-9]{64}$/.test(protocol.dataset_sha256 ?? "") ||
    !protocol.split ||
    !Array.isArray(protocol.split.cv_folds)
  )
    return null;
  return protocol;
}

export function verifiedEvaluation(run: MLRun): boolean {
  const protocol = evaluationProtocol(run);
  return (
    !!protocol &&
    run.comparison?.protocol_verified === true &&
    run.comparison.evaluation_protocol_id === protocol.protocol_id &&
    run.models
      .filter((model) => model.status === "completed")
      .every((model) => model.evaluation_protocol_id === protocol.protocol_id)
  );
}

const METRICS: Record<
  string,
  {
    label: string;
    labelKey?: NoParameterMessageKey;
    direction: NoParameterMessageKey;
    definition: NoParameterMessageKey;
  }
> = {
  accuracy: {
    label: "Accuracy",
    labelKey: "ui.ml.accuracy",
    direction: "ui.ml.higherIsBetter",
    definition: "ui.ml.shareClassifiedCorrectly",
  },
  balanced_accuracy: {
    label: "Balanced accuracy",
    labelKey: "ui.ml.balancedAccuracy",
    direction: "ui.ml.higherIsBetter",
    definition: "ui.ml.meanRecallAcrossClasses",
  },
  precision: {
    label: "Precision",
    labelKey: "ui.ml.precision",
    direction: "ui.ml.higherIsBetter",
    definition:
      "ui.ml.correctShareOfPredictedPositivesMulticlassUsesTheSavedAveragingMethod",
  },
  recall: {
    label: "Recall",
    labelKey: "ui.ml.recall",
    direction: "ui.ml.higherIsBetter",
    definition:
      "ui.ml.detectedShareOfActualPositivesMulticlassUsesTheSavedAveragingMethod",
  },
  f1: {
    label: "F1",
    labelKey: "ui.ml.f1",
    direction: "ui.ml.higherIsBetter",
    definition: "ui.ml.harmonicMeanOfPrecisionAndRecall",
  },
  roc_auc: {
    label: "ROC-AUC",
    labelKey: "ui.ml.rocAuc",
    direction: "ui.ml.higherIsBetter",
    definition: "ui.ml.areaUnderTheRocCurveRequiringSavedPredictionScores",
  },
  pr_auc: {
    label: "PR-AUC",
    labelKey: "ui.ml.prAuc",
    direction: "ui.ml.higherIsBetter",
    definition: "ui.ml.theServiceCurrentlyStoresAveragePrecisionApInThisField",
  },
  log_loss: {
    label: "Log loss",
    labelKey: "ui.ml.logLoss",
    direction: "ui.ml.lowerIsBetter",
    definition: "ui.ml.logarithmicLossOfPredictedProbabilities",
  },
  mae: {
    label: "MAE",
    labelKey: "ui.ml.mae",
    direction: "ui.ml.lowerIsBetter",
    definition: "ui.ml.meanAbsoluteErrorInTheTargetVariableSUnits",
  },
  mse: {
    label: "MSE",
    labelKey: "ui.ml.mse",
    direction: "ui.ml.lowerIsBetter",
    definition: "ui.ml.meanSquaredErrorInSquaredTargetVariableUnits",
  },
  rmse: {
    label: "RMSE",
    labelKey: "ui.ml.rmse",
    direction: "ui.ml.lowerIsBetter",
    definition: "ui.ml.rootMeanSquaredErrorInTheTargetVariableSUnits",
  },
  r2: {
    label: "R²",
    labelKey: "ui.ml.rSquared",
    direction: "ui.ml.higherIsBetter",
    definition: "ui.ml.coefficientOfDeterminationItCanBeNegative",
  },
  mape: {
    label: "MAPE",
    labelKey: "ui.ml.mape",
    direction: "ui.ml.lowerIsBetter",
    definition:
      "ui.ml.meanAbsolutePercentageErrorSavedAsARatioNotEstimableWhenTheTargetIsZero",
  },
};

const DEFAULT_METRICS: Record<string, string[]> = {
  regression: ["rmse", "mae", "r2"],
  binary_classification: [
    "roc_auc",
    "pr_auc",
    "balanced_accuracy",
    "f1",
    "accuracy",
  ],
  multiclass_classification: [
    "balanced_accuracy",
    "accuracy",
    "f1",
    "precision",
    "recall",
  ],
};

const NON_METRIC_KEYS = new Set(["confusion_matrix", "labels", "residual_std"]);

export function metricLabel(
  key: string,
  t: AppTranslator = createAppTranslator("en"),
): string {
  const meta = ownedRecordValue(METRICS, key);
  return meta?.labelKey ? t(meta.labelKey) : (meta?.label ?? key);
}
export function metricDescription(
  key: string,
  t: AppTranslator = createAppTranslator("en"),
): string {
  const meta = ownedRecordValue(METRICS, key);
  return meta
    ? `${t(meta.direction)} · ${t(meta.definition)}`
    : t("ml.metric.unverifiedDefinition");
}

export function isFiniteMetric(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function displayMetric(
  value: unknown,
  digits = 3,
  t: AppTranslator = createAppTranslator("en"),
): string {
  return isFiniteMetric(value)
    ? formatStat(value, digits)
    : t("common.unavailable");
}

export function displayCount(
  value: unknown,
  t: AppTranslator = createAppTranslator("en"),
): string {
  return isFiniteMetric(value) && Number.isInteger(value) && value >= 0
    ? String(value)
    : t("common.unavailable");
}

export function availableMetricKeys(
  run: Pick<MLRun, "models">,
  task: string | null,
): string[] {
  const keys = new Set(DEFAULT_METRICS[task ?? ""] ?? []);
  for (const model of run.models) {
    if (model.status !== "completed") continue;
    for (const metrics of [model.test_metrics, model.validation_metrics]) {
      for (const [key, value] of Object.entries(metrics ?? {})) {
        if (
          !NON_METRIC_KEYS.has(key) &&
          (key in METRICS || value === null || typeof value === "number")
        ) {
          keys.add(key);
        }
      }
    }
  }
  return Array.from(keys);
}

export function initialMetrics(keys: string[], task: string | null): string[] {
  const defaults = DEFAULT_METRICS[task ?? ""];
  return defaults ? defaults.filter((key) => keys.includes(key)) : keys;
}

export function initialPrimaryMetric(
  keys: string[],
  task: string | null,
): string {
  return initialMetrics(keys, task)[0] ?? keys[0] ?? "";
}

export function confusionMatrixData(
  metrics: Record<string, unknown>,
): { cm: number[][]; labels: string[] } | null {
  const cm = metrics.confusion_matrix;
  const labels = metrics.labels;
  if (
    !Array.isArray(labels) ||
    labels.length === 0 ||
    !labels.every((label) => typeof label === "string")
  )
    return null;
  if (
    !Array.isArray(cm) ||
    cm.length !== labels.length ||
    !cm.every(
      (row) =>
        Array.isArray(row) &&
        row.length === labels.length &&
        row.every(
          (value) =>
            isFiniteMetric(value) && Number.isInteger(value) && value >= 0,
        ),
    )
  )
    return null;
  return { cm, labels };
}
