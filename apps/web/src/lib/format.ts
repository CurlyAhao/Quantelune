/** Display helpers: sizes, dates, labels, and API-error → friendly copy. */

import { isApiError } from "@/lib/api-client";
import { ownedRecordValue } from "./owned-record-value";
import type { AppLocale } from "@/lib/i18n";
import {
  createAppTranslator,
  type AppTranslator,
  type NoParameterMessageKey,
} from "@/lib/i18n-messages";
import {
  formatLocaleBytes,
  formatLocaleDate,
  formatLocaleNumber,
  formatLocalePercent,
  formatLocalePValue,
  formatLocaleStat,
} from "@/lib/i18n-core";
import type {
  FindingSeverity,
  FindingStatus,
  ProjectStatus,
  QualityRunStatus,
  StatisticalRunStatus,
  TaskType,
  UploadStatus,
} from "@/lib/types";

export function formatBytes(bytes: number, locale: AppLocale = "en"): string {
  return formatLocaleBytes(bytes, locale);
}
export function formatNumber(
  value: number | null | undefined,
  locale: AppLocale = "en",
): string {
  return formatLocaleNumber(value, locale);
}
export function formatDate(
  iso: string | null | undefined,
  locale: AppLocale = "en",
  timeZone?: string,
): string {
  return formatLocaleDate(iso, locale, timeZone);
}
export function formatPercent(
  rate: number | null | undefined,
  locale: AppLocale = "en",
): string {
  return formatLocalePercent(rate, locale);
}

/* ------------------------------------------------------------------ */
/* Domain labels                                                       */
/* ------------------------------------------------------------------ */

const TASK_TYPE_LABELS: Record<TaskType, NoParameterMessageKey> = {
  classification_binary: "ui.projects.binaryClassification",
  classification_multiclass: "ui.projects.multiclassClassification",
  regression: "workflow.node.regression.label",
  clustering: "ui.projects.clustering",
  anomaly_detection: "ui.projects.anomalyDetection",
  statistical_analysis: "ui.projects.statisticalAnalysis",
};

export function taskTypeLabel(
  taskType: TaskType | null,
  t: AppTranslator = createAppTranslator("en"),
): string {
  const key = taskType ? TASK_TYPE_LABELS[taskType] : null;
  return key ? t(key) : (taskType ?? t("ui.common.noTaskType"));
}

export type BadgeTone = "teal" | "amber" | "slate" | "red" | "blue";

export const PROJECT_STATUS_META: Record<
  ProjectStatus,
  { label: string; labelKey?: NoParameterMessageKey; tone: BadgeTone }
> = {
  draft: { label: "Draft", labelKey: "ui.common.draft", tone: "slate" },
  active: { label: "Active", labelKey: "ui.dashboard.active", tone: "teal" },
  archived: {
    label: "Archived",
    labelKey: "ui.common.archived",
    tone: "amber",
  },
};

export const UPLOAD_STATUS_META: Record<
  UploadStatus,
  { label: string; labelKey?: NoParameterMessageKey; tone: BadgeTone }
> = {
  pending: { label: "Pending", labelKey: "ui.common.pending", tone: "slate" },
  uploading: {
    label: "Uploading",
    labelKey: "ui.common.uploading",
    tone: "blue",
  },
  uploaded: { label: "Uploaded", labelKey: "ui.common.uploaded", tone: "blue" },
  validating: {
    label: "Validating",
    labelKey: "ui.common.validating",
    tone: "amber",
  },
  ready: { label: "Ready", labelKey: "ui.common.ready.format", tone: "teal" },
  failed: { label: "Failed", labelKey: "ui.projects.failed", tone: "red" },
  deleted: { label: "Deleted", labelKey: "ui.common.deleted", tone: "red" },
};

/* ------------------------------------------------------------------ */
/* Data-quality labels                                                 */
/* ------------------------------------------------------------------ */

export const QUALITY_RUN_STATUS_META: Record<
  QualityRunStatus,
  { label: string; labelKey?: NoParameterMessageKey; tone: BadgeTone }
> = {
  pending: { label: "Pending", labelKey: "ui.common.pending", tone: "slate" },
  running: { label: "Running", labelKey: "ui.common.running", tone: "blue" },
  completed: {
    label: "Completed",
    labelKey: "ui.report.completed",
    tone: "teal",
  },
  completed_with_errors: {
    label: "Completed with errors",
    labelKey: "ui.common.completedWithErrors",
    tone: "amber",
  },
  failed: { label: "Failed", labelKey: "ui.projects.failed", tone: "red" },
};

export function qualityRunStatusTone(status: string): BadgeTone {
  return QUALITY_RUN_STATUS_META[status as QualityRunStatus]?.tone ?? "slate";
}

export const STAT_RUN_STATUS_META: Record<
  StatisticalRunStatus,
  { label: string; labelKey?: NoParameterMessageKey; tone: BadgeTone }
> = {
  pending: { label: "Pending", labelKey: "ui.common.pending", tone: "slate" },
  running: { label: "Running", labelKey: "ui.common.running", tone: "blue" },
  completed: {
    label: "Completed",
    labelKey: "ui.report.completed",
    tone: "teal",
  },
  not_applicable: {
    label: "Not applicable",
    labelKey: "ui.common.notApplicable",
    tone: "amber",
  },
  failed: { label: "Failed", labelKey: "ui.projects.failed", tone: "red" },
};

export const STAT_METHOD_CATEGORY_LABELS: Record<string, string> = {
  descriptive: "Descriptive",
  correlation: "Correlation",
  two_group: "Two-group comparison",
  categorical: "Categorical",
  multi_group: "Multi-group comparison",
  regression: "Regression",
};

export const ML_RUN_STATUS_META: Record<
  string,
  { label: string; labelKey?: NoParameterMessageKey; tone: BadgeTone }
> = {
  pending: { label: "Pending", labelKey: "ui.common.pending", tone: "slate" },
  running: { label: "Training", labelKey: "ui.common.training", tone: "blue" },
  completed: {
    label: "Completed",
    labelKey: "ui.report.completed",
    tone: "teal",
  },
  failed: { label: "Failed", labelKey: "ui.projects.failed", tone: "red" },
};

export const ML_TASK_LABELS: Record<string, string> = {
  binary_classification: "Binary classification",
  multiclass_classification: "Multiclass classification",
  regression: "Regression",
};

export const ML_SPLIT_LABELS: Record<string, string> = {
  train_test: "Train / test",
  train_val_test: "Train / val / test",
  kfold: "K-Fold",
  stratified_kfold: "Stratified K-Fold",
  group_kfold: "Group K-Fold",
  time_series_split: "Time-series split",
};

/** Format a p-value: exact down to 0.0001, then "< 0.0001". */
export function formatPValue(
  p: number | null | undefined,
  locale: AppLocale = "en",
): string {
  return formatLocalePValue(p, locale);
}
export function formatStat(
  value: number | null | undefined,
  digits = 4,
  locale: AppLocale = "en",
): string {
  return formatLocaleStat(value, locale, digits);
}

export const FINDING_STATUS_META: Record<
  FindingStatus,
  { label: string; labelKey?: NoParameterMessageKey; tone: BadgeTone }
> = {
  passed: {
    label: "Passed",
    labelKey: "ui.quality.passed.qualityOverview",
    tone: "teal",
  },
  warning: { label: "Warning", labelKey: "ui.common.warning", tone: "amber" },
  failed: { label: "Failed", labelKey: "ui.projects.failed", tone: "red" },
  skipped: {
    label: "Skipped",
    labelKey: "ui.quality.skipped.qualityOverview",
    tone: "slate",
  },
  error: { label: "Error", labelKey: "ui.common.error", tone: "red" },
};

export function findingStatusTone(status: string): BadgeTone {
  return FINDING_STATUS_META[status as FindingStatus]?.tone ?? "slate";
}

export const FINDING_SEVERITY_META: Record<
  FindingSeverity,
  { label: string; labelKey?: NoParameterMessageKey; tone: BadgeTone }
> = {
  info: { label: "Info", labelKey: "ui.common.info", tone: "slate" },
  low: { label: "Low", labelKey: "ui.common.low", tone: "blue" },
  medium: { label: "Medium", labelKey: "ui.common.medium", tone: "amber" },
  high: { label: "High", labelKey: "ui.common.high", tone: "red" },
  critical: { label: "Critical", labelKey: "ui.common.critical", tone: "red" },
};

/** Severity → StatusBadge tone (info→slate, low→blue, medium→amber, high/critical→red). */
export function severityTone(severity: string): BadgeTone {
  return FINDING_SEVERITY_META[severity as FindingSeverity]?.tone ?? "slate";
}

/** Solid accent color per severity, for indicator stripes and dots. */
export const SEVERITY_COLORS: Record<FindingSeverity, string> = {
  info: "#7d8aa0",
  low: "#3b62c4",
  medium: "#c99a2e",
  high: "#c2452e",
  critical: "#c2452e",
};

export function severityColor(severity: string): string {
  return SEVERITY_COLORS[severity as FindingSeverity] ?? SEVERITY_COLORS.info;
}

/* ------------------------------------------------------------------ */
/* Friendly API error copy                                             */
/* ------------------------------------------------------------------ */

const ERROR_COPY: Record<
  string,
  { title: NoParameterMessageKey; message?: NoParameterMessageKey }
> = {
  AUTH_INVALID_CREDENTIALS: {
    title: "ui.common.signInFailed",
    message: "ui.common.incorrectEmailOrPassword",
  },
  AUTH_EMAIL_EXISTS: {
    title: "ui.common.emailAlreadyRegistered",
    message: "ui.common.anAccountWithThisEmailAlreadyExistsTrySigningIn",
  },
  AUTH_WEAK_PASSWORD: { title: "ui.common.passwordTooWeak" },
  AUTH_TOKEN_EXPIRED: {
    title: "ui.common.sessionExpired",
    message: "ui.common.pleaseSignInAgain",
  },
  AUTH_TOKEN_INVALID: {
    title: "ui.common.notSignedIn",
    message: "ui.common.pleaseSignInToContinue",
  },
  PROJECT_NOT_FOUND: {
    title: "ui.common.projectNotFound",
    message: "ui.common.itMayHaveBeenDeleted",
  },
  PROJECT_ACCESS_DENIED: {
    title: "ui.common.accessDenied",
    message: "ui.common.youDoNotHaveAccessToThisProject",
  },
  DATASET_NOT_FOUND: {
    title: "ui.common.datasetNotFound",
    message: "ui.common.itMayHaveBeenDeleted",
  },
  DATASET_DUPLICATE: {
    title: "ui.common.duplicateFile",
    message: "ui.common.thisExactFileHasAlreadyBeenUploadedToThisProject",
  },
  FILE_TOO_LARGE: {
    title: "ui.common.fileTooLarge",
    message: "ui.common.datasetsAreLimitedTo100Mb",
  },
  FILE_UNSUPPORTED_TYPE: {
    title: "ui.common.unsupportedFileType",
    message: "ui.common.uploadACsvExcelXlsxOrParquetFile",
  },
  STORAGE_ERROR: {
    title: "ui.common.storageError",
    message: "ui.common.theFileCouldNotBeStoredPleaseTryAgain",
  },
  NETWORK_ERROR: {
    title: "ui.common.connectionProblem",
    message: "ui.common.cannotReachTheStatforgeApiIsTheBackendRunning",
  },
  QUALITY_RUN_NOT_FOUND: {
    title: "ui.common.noQualityRunYet",
    message: "ui.common.runAQualityCheckToInspectThisDataset",
  },
  INVALID_THRESHOLD: {
    title: "ui.common.invalidThreshold",
    message: "ui.common.aThresholdKeyIsUnknownOrItsValueIsOutOfRange",
  },
  DATASET_NOT_READY: {
    title: "ui.common.datasetNotReady",
    message: "ui.common.theDatasetMustFinishProcessingBeforeItCanBeChecked",
  },
};

export interface FriendlyError {
  title: string;
  message: string;
}

export function safeDiagnosticCode(code: unknown): string {
  return typeof code === "string" && /^[A-Z][A-Z0-9_]{0,79}$/.test(code)
    ? code
    : "UNKNOWN";
}

export function friendlyApiError(
  error: unknown,
  t: AppTranslator = createAppTranslator("en"),
): FriendlyError {
  // Tauri rejects native editor commands with their string error code.
  if (typeof error === "string") {
    const message = ownedRecordValue<NoParameterMessageKey>({
      CODE_EDITOR_UNAVAILABLE: "error.plotEditor.unavailable",
      CODE_FILE_UNAVAILABLE: "error.plotEditor.file",
      CODE_FILE_CHANGED: "error.plotEditor.changed",
      CODE_RUNTIME_UNAVAILABLE: "error.plotEditor.runtime",
    }, error);
    if (message) return { title: t("error.plotEditor.title"), message: t(message) };
  }
  if (isApiError(error)) {
    const code = safeDiagnosticCode(error.errorCode);
    const copy = ERROR_COPY[code];
    if (copy?.message)
      return { title: t(copy.title), message: t(copy.message) };
    if (copy)
      return {
        title: t(copy.title),
        message: t("error.requestFailed.message", { code }),
      };
    if (code === "WORKFLOW_VERSION_CONFLICT")
      return {
        title: t("error.versionConflict.title"),
        message: t("error.versionConflict.message"),
      };
    if (code.endsWith("PUBLICATION_UNKNOWN"))
      return {
        title: t("error.publicationUnknown.title"),
        message: t("error.publicationUnknown.message"),
      };
    if (code === "NETWORK_ERROR")
      return {
        title: t("error.offline.title"),
        message: t("error.offline.message"),
      };
    const httpMessage = (key: NoParameterMessageKey) =>
      `${t(key)} ${t("diagnostic.code", { code })}`;
    switch (error.status) {
      case 401:
        return {
          title: t("error.http401.title"),
          message: httpMessage("error.http401.message"),
        };
      case 403:
        return {
          title: t("error.http403.title"),
          message: httpMessage("error.http403.message"),
        };
      case 404:
        return {
          title: t("error.http404.title"),
          message: httpMessage("error.http404.message"),
        };
      case 413:
        return {
          title: t("error.http413.title"),
          message: httpMessage("error.http413.message"),
        };
      case 422:
        return {
          title: t("error.http422.title"),
          message: httpMessage("error.http422.message"),
        };
      case 500:
        return {
          title: t("error.http500.title"),
          message: httpMessage("error.http500.message"),
        };
      default:
        return {
          title: t("error.requestFailed.title", { code }),
          message: t("error.requestFailed.message", { code }),
        };
    }
  }
  return {
    title: t("error.unexpected.title"),
    message: t("error.unexpected.message"),
  };
}

/** Only typed domain metadata carries a display key; unknown technical labels stay raw. */
export function localizedMetaLabel(
  meta: { label: string; labelKey?: NoParameterMessageKey },
  t: AppTranslator = createAppTranslator("en"),
): string {
  return meta.labelKey ? t(meta.labelKey) : meta.label;
}

const VERSION_STATUS_LABELS: Record<string, NoParameterMessageKey> = {
  draft: "ui.common.draft",
  active: "ui.dashboard.active",
  archived: "ui.common.archived",
  frozen: "ui.projects.frozen.versionsClient",
};
export function versionStatusLabel(
  status: string | null | undefined,
  t: AppTranslator,
): string {
  if (!status) return t("ui.displayBranch.common.unknown");
  const key = ownedRecordValue(VERSION_STATUS_LABELS, status);
  return key ? t(key) : status;
}
