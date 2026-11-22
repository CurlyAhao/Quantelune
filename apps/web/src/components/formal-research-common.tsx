"use client";

import { Button } from "./ui/button";
import { useState } from "react";
import { ErrorState } from "./error-state";
import { friendlyApiError } from "@/lib/format";
import { useLanguage } from "@/providers/language-provider";
import type { NoParameterMessageKey } from "@/lib/i18n-messages";
import type { useResearchAction } from "./use-research-action";

const stateLabels: Record<string, NoParameterMessageKey> = {
  queued: "research.queued",
  running: "research.running",
  waiting_approval: "research.waiting_approval",
  paused: "research.paused",
  cancel_requested: "research.cancel_requested",
  cancelled: "research.cancelled",
  completed: "research.completed",
  failed: "research.failed",
  interrupted: "research.interrupted",
  proven: "research.proven",
  not_started: "research.not_started",
  dependency_blocked: "research.dependency_blocked",
  incomplete: "research.incomplete",
  cancel_pending: "research.cancel_pending",
  pending: "research.waiting_approval",
  approved: "research.approved",
  denied: "research.denied",
  revoked: "research.revoked",
  expired: "research.expired",
  consumed: "research.consumed",
  unknown: "research.unknownCharge",
};

export function ResearchStatus({ state }: { state: string }) {
  const { t } = useLanguage();
  return (
    <span className="formal-status" data-state={state}>
      {t(stateLabels[state] ?? "research.stateUnknown")}
      {!stateLabels[state] && (
        <>
          {" "}
          <code>{state}</code>
        </>
      )}
    </span>
  );
}

export function ResearchFailure({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  const { t } = useLanguage();
  return (
    <ErrorState
      {...friendlyApiError(error, t)}
      onRetry={retry}
      retryLabel={t("research.reload")}
      className="formal-error"
    />
  );
}

export function ResearchActionNotice({
  action,
  reload,
  resetAfterRead = true,
}: {
  action: ReturnType<typeof useResearchAction>;
  reload: () => Promise<unknown>;
  resetAfterRead?: boolean;
}) {
  const { t } = useLanguage();
  const [readError, setReadError] = useState<unknown>(null);
  async function read() {
    try {
      const result = await reload();
      if (
        result &&
        typeof result === "object" &&
        "isError" in result &&
        result.isError
      )
        throw "error" in result
          ? result.error
          : new Error("Research read failed.");
      setReadError(null);
      if (resetAfterRead) action.reset();
    } catch (error) {
      setReadError(error);
    }
  }
  return (
    <>
      {action.pending && <p role="status">{t("research.pending")}</p>}
      {action.error != null && (
        <div className="formal-action-error">
          <ResearchFailure
            error={action.error}
            retry={() => {
              void read();
            }}
          />
          {readError != null && <ResearchFailure error={readError} />}
          <p className="entry-help">{t("research.uncertain")}</p>
          {action.canRetry && (
            <Button
              variant="outline"
              disabled={action.pending}
              onClick={action.retry}
            >
              {t("research.retrySame")}
            </Button>
          )}
        </div>
      )}
    </>
  );
}

/** Unknown objects are displayed as data. No HTML/Markdown execution or inferred numbers. */
export function ResearchValue({ value }: { value: unknown }) {
  const { formatNumber } = useLanguage();
  if (value == null) return <span>—</span>;
  if (typeof value === "number")
    return (
      <span className="tabular-nums">
        {Number.isFinite(value)
          ? formatNumber(value, { maximumSignificantDigits: 10 })
          : "—"}
      </span>
    );
  if (typeof value === "string" || typeof value === "boolean")
    return <span data-no-translate>{String(value)}</span>;
  return (
    <pre className="formal-json" data-no-translate>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function ResearchMoney({ units }: { units: number | null }) {
  const { locale } = useLanguage();
  return (
    <span className="tabular-nums">
      {units === null
        ? locale === "zh-CN"
          ? "未估算"
          : "Not estimated"
        : Number.isSafeInteger(units) && units >= 0
          ? new Intl.NumberFormat(locale, {
              style: "currency",
              currency: "USD",
              maximumFractionDigits: 8,
            }).format(units / 100000000)
          : "—"}
    </span>
  );
}
