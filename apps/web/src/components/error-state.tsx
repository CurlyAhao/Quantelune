"use client";
import { CircleAlertIcon, RotateCwIcon } from "lucide-react";
import { useLanguage } from "@/providers/language-provider";
import { SavedDiagnostic } from "./saved-diagnostic";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  title?: string;
  message: React.ReactNode;
  diagnostic?: React.ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}

/** Friendly failure panel with an optional retry action. */
export function ErrorState({
  title,
  message,
  diagnostic,
  onRetry,
  retryLabel,
  className,
}: ErrorStateProps) {
  const { t } = useLanguage();
  return (
    <div
      role="alert"
      className={cn(
        "animate-rise flex flex-col items-center gap-3 rounded-lg border border-dashed border-destructive/40 bg-destructive/5 px-6 py-12 text-center",
        className,
      )}
    >
      <span className="flex size-11 items-center justify-center rounded-full bg-destructive/10">
        <CircleAlertIcon className="size-5 text-destructive" />
      </span>
      <div className="space-y-1">
        <h3 className="font-heading text-base font-semibold">
          {title ?? t("error.unexpected.title")}
        </h3>
        <p className="mx-auto max-w-md text-sm text-muted-foreground">
          {message}
        </p>
        {diagnostic && <SavedDiagnostic>{diagnostic}</SavedDiagnostic>}
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
          <RotateCwIcon className="size-3.5" />
          {retryLabel ?? t("common.retry")}
        </Button>
      )}
    </div>
  );
}
