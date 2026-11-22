"use client";

import type { AppLocale } from "@/lib/i18n";
import { ownedRecordValue } from "@/lib/owned-record-value";
import {
  createAppTranslator,
  type AppTranslator,
  type NoParameterMessageKey,
} from "@/lib/i18n-messages";
import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { CircleAlertIcon } from "lucide-react";
import type { UseMutationResult } from "@tanstack/react-query";

import { friendlyApiError } from "@/lib/format";
import type { QualityRun, QualityRunRequest } from "@/lib/types";

export interface RunMutationVars {
  datasetId: string;
  request: QualityRunRequest;
}

export type QualityRunMutation = UseMutationResult<
  QualityRun,
  Error,
  RunMutationVars
>;

/** Inline error strip for run/recompute mutation failures. */
export function MutationErrorNote({
  mutation,
}: {
  mutation: Pick<QualityRunMutation, "isError" | "error">;
}) {
  const { t: uiCopyT } = useCopyLanguage();

  if (!mutation.isError) return null;
  const friendly = friendlyApiError(mutation.error, uiCopyT);
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
    >
      <CircleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
      <span>
        <strong className="font-semibold">{friendly.title}.</strong>{" "}
        {friendly.message}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Value formatting for findings                                       */
/* ------------------------------------------------------------------ */

function trimZeros(text: string): string {
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

/** Render an arbitrary observed_value (number | string | bool | object | null). */
export function formatObserved(
  value: unknown,
  locale: AppLocale = "en",
): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) {
      return new Intl.NumberFormat(locale).format(value);
    }
    if (Math.abs(value) > 0 && Math.abs(value) < 0.0001) {
      return value.toExponential(2);
    }
    return trimZeros(value.toFixed(4));
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string")
    return value === ""
      ? createAppTranslator(locale)("quality.emptyValue")
      : value;
  if (Array.isArray(value)) {
    return value.length === 0
      ? "[]"
      : value.map((item) => formatObserved(item, locale)).join(", ");
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}

/** Render a threshold map like `high_missingness=0.8`. */
export function formatThreshold(
  threshold: Record<string, number> | null,
): string {
  if (!threshold) return "—";
  const entries = Object.entries(threshold);
  if (entries.length === 0) return "—";
  return entries.map(([key, val]) => `${key}=${val}`).join(", ");
}

const CATEGORY_KEYS: Record<string, NoParameterMessageKey> = {
  structure: "quality.category.structure",
  completeness: "quality.category.completeness",
  uniqueness: "quality.category.uniqueness",
  validity: "quality.category.validity",
  consistency: "quality.category.consistency",
  temporal: "quality.category.temporal",
  distribution: "quality.category.distribution",
  domain: "quality.category.domain",
};
export function qualityCategoryLabel(id: string, t: AppTranslator): string {
  const key = ownedRecordValue(CATEGORY_KEYS, id);
  return key ? t(key) : id;
}
