import { cn } from "@/lib/utils";
import type { BadgeTone } from "@/lib/format";

const TONE_CLASSES: Record<BadgeTone, string> = {
  teal: "bg-primary/10 text-primary ring-primary/25",
  amber: "bg-accent/20 text-accent-foreground ring-accent/40",
  slate: "bg-muted text-muted-foreground ring-foreground/10",
  red: "bg-destructive/10 text-destructive ring-destructive/25",
  blue: "bg-chart-2/10 text-chart-2 ring-chart-2/25",
};

const DOT_CLASSES: Record<BadgeTone, string> = {
  teal: "bg-primary",
  amber: "bg-accent-foreground",
  slate: "bg-muted-foreground/60",
  red: "bg-destructive",
  blue: "bg-chart-2",
};

interface StatusBadgeProps {
  label: string;
  tone: BadgeTone;
  /** Show a leading status dot (pulses for in-flight states). */
  dot?: boolean;
  pulse?: boolean;
  className?: string;
}

/** Compact instrument-style status chip with an optional live dot. */
export function StatusBadge({
  label,
  tone,
  dot = true,
  pulse = false,
  className,
}: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1.5 rounded-full px-2 font-mono text-[0.65rem] font-medium tracking-wide uppercase ring-1 ring-inset",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden
          className={cn(
            "size-1.5 rounded-full",
            DOT_CLASSES[tone],
            pulse && "animate-pulse-dot",
          )}
        />
      )}
      {label}
    </span>
  );
}
