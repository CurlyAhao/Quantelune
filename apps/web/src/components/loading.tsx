"use client";
import { Spinner } from "@/components/ui/spinner";
import { useLanguage } from "@/providers/language-provider";
import { cn } from "@/lib/utils";

/** Full-area loader used while pages / auth checks resolve. */
export function PageLoader({
  label,
  className,
}: {
  label?: string;
  className?: string;
}) {
  const { t } = useLanguage();
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex min-h-[60vh] flex-col items-center justify-center gap-3 p-8",
        className,
      )}
    >
      <div className="relative">
        <Spinner className="size-8 text-primary" />
        <div className="absolute inset-0 -z-10 animate-ping rounded-full bg-primary/10" />
      </div>
      <p className="font-mono text-xs tracking-widest text-muted-foreground uppercase">
        {label ?? t("common.loading")}
      </p>
    </div>
  );
}

/** Small inline spinner for buttons and compact areas. */
export function InlineSpinner({
  label,
  className,
}: {
  label?: string;
  className?: string;
}) {
  const { t } = useLanguage();
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-2 text-sm text-muted-foreground",
        className,
      )}
    >
      <Spinner className="size-4" />
      {label ?? t("common.loading")}
    </span>
  );
}
