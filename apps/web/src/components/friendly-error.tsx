"use client";
import { friendlyApiError, safeDiagnosticCode } from "@/lib/format";
import { useLanguage } from "@/providers/language-provider";

/** Resolves retained UI error state on each locale change, without raw detail. */
export function FriendlyErrorMessage({
  error,
  part = "message",
}: {
  error: unknown;
  part?: "title" | "message";
}) {
  const { t } = useLanguage();
  return friendlyApiError(error, t)[part];
}
export function SavedFailureMessage({ code }: { code: unknown }) {
  const { t } = useLanguage();
  return t("error.requestFailed.message", { code: safeDiagnosticCode(code) });
}
