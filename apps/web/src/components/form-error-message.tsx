"use client";
import { useLanguage } from "@/providers/language-provider";
import { safeDiagnosticCode } from "@/lib/format";
import type { NoParameterMessageKey } from "@/lib/i18n-messages";
import { ownedRecordValue } from "@/lib/owned-record-value";

const VALIDATION_MESSAGES: Record<string, NoParameterMessageKey> = {
  "validation.nameLength": "validation.nameLength",
  "validation.email": "validation.email",
  "validation.passwordLength": "validation.passwordLength",
  "validation.passwordRequired": "validation.passwordRequired",
  "validation.confirmPassword": "validation.confirmPassword",
  "validation.passwordMismatch": "validation.passwordMismatch",
  "validation.titleRequired": "validation.titleRequired",
  "validation.titleLength": "validation.titleLength",
  "validation.descriptionLength": "validation.descriptionLength",
  "validation.researchQuestionLength": "validation.researchQuestionLength",
  AUTH_EMAIL_EXISTS: "ui.common.emailAlreadyRegistered",
  AUTH_WEAK_PASSWORD: "ui.common.passwordTooWeak",
};
/** Form state stores semantic IDs, so retained validation reacts to language changes. */
export function FormErrorMessage({ code }: { code?: string }) {
  const { t } = useLanguage();
  if (!code) return null;
  const key = ownedRecordValue(VALIDATION_MESSAGES, code);
  return key
    ? t(key)
    : t("error.requestFailed.message", { code: safeDiagnosticCode(code) });
}
