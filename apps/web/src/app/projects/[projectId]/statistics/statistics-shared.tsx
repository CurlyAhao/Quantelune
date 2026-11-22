"use client";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import type { UseMutationResult } from "@tanstack/react-query";
import { CircleAlertIcon, Loader2Icon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { friendlyApiError } from "@/lib/format";
import type { StatisticalRun, StatisticalRunRequest } from "@/lib/types";

export interface StatRunMutationVars {
  datasetId: string;
  request: StatisticalRunRequest;
  isCurrent: () => boolean;
}

export type StatRunMutation = UseMutationResult<
  StatisticalRun,
  Error,
  StatRunMutationVars
>;

/** Inline error strip for a run mutation failure. */
export function MutationErrorNote({
  mutation,
}: {
  mutation: Pick<StatRunMutation, "isError" | "error">;
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

export function ComputingPanel({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center gap-3 rounded-lg border border-dashed bg-card/60 py-16"
    >
      <Loader2Icon className="size-8 animate-spin text-primary" />
      <p className="text-sm text-muted-foreground">{label}</p>
    </div>
  );
}

export function EmptyPanel({
  icon,
  title,
  message,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  message: string;
  action?: React.ReactNode;
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
        {icon}
        <h3 className="font-heading text-lg font-bold">{title}</h3>
        <p className="max-w-md text-sm text-muted-foreground">{message}</p>
        {action}
      </CardContent>
    </Card>
  );
}

/** Render an arbitrary numeric/string/object value from a result payload. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    if (Number.isInteger(value))
      return new Intl.NumberFormat("en-US").format(value);
    if (Math.abs(value) > 0 && Math.abs(value) < 0.0001)
      return value.toExponential(2);
    return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value === "" ? "(empty)" : value;
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}
