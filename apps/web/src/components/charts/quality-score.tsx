"use client";

import type { QualityGaugeData } from "@/lib/types";
import type { NoParameterMessageKey } from "@/lib/i18n-messages";
import { ownedRecordValue } from "@/lib/owned-record-value";
import { useLanguage } from "@/providers/language-provider";

const DIMENSION_LABELS: Record<string, NoParameterMessageKey> = {
  completeness: "quality.category.completeness",
  validity: "quality.category.validity",
  uniqueness: "quality.category.uniqueness",
  consistency: "quality.category.consistency",
  temporal: "quality.category.temporal",
  usability: "quality.dimension.usability",
};

function scoreColor(score: number): string {
  if (score < 40) return "#c2452e";
  if (score < 70) return "#c99a2e";
  return "#2f7a74";
}

/** Radial quality score with per-dimension breakdown bars. */
export function QualityScore({ quality }: { quality: QualityGaugeData }) {
  const { t, formatNumber } = useLanguage();
  const total = Math.round(quality.total_score);
  const ring = scoreColor(total);
  const circumference = 2 * Math.PI * 42;
  const filled = (total / 100) * circumference;

  return (
    <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
      {/* Radial gauge */}
      <div className="relative flex size-36 shrink-0 items-center justify-center">
        <svg viewBox="0 0 100 100" className="size-full -rotate-90">
          <circle
            cx="50"
            cy="50"
            r="42"
            fill="none"
            stroke="var(--muted)"
            strokeWidth="9"
          />
          <circle
            cx="50"
            cy="50"
            r="42"
            fill="none"
            stroke={ring}
            strokeWidth="9"
            strokeLinecap="round"
            strokeDasharray={`${filled} ${circumference}`}
            className="transition-[stroke-dasharray] duration-1000 ease-out"
          />
        </svg>
        <div className="absolute flex flex-col items-center">
          <span
            className="font-heading text-4xl font-bold tabular-nums"
            style={{ color: ring }}
          >
            {formatNumber(total)}
          </span>
          <span className="text-[0.65rem] tracking-wide text-muted-foreground uppercase">
            / 100
          </span>
        </div>
      </div>

      {/* Dimension bars */}
      <div className="flex-1 space-y-2.5">
        {Object.entries(quality.dimensions).map(([key, dim]) => {
          const labelKey = ownedRecordValue(DIMENSION_LABELS, key);
          const label = labelKey ? t(labelKey) : key;
          const pct = Math.round(dim.score);
          return (
            <div key={key}>
              <div className="mb-1 flex items-baseline justify-between">
                <span className="text-xs font-medium">{label}</span>
                <span className="font-mono text-[0.7rem] text-muted-foreground">
                  {formatNumber(pct)}
                  <span className="ml-1 text-muted-foreground/60">
                    w=
                    {formatNumber(dim.weight, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                      useGrouping: false,
                    })}
                  </span>
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full transition-[width] duration-700 ease-out"
                  style={{
                    width: `${pct}%`,
                    backgroundColor: scoreColor(pct),
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
