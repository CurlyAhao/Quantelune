"use client";

import { useLanguage as useArrayLanguage } from "@/providers/language-provider";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { CheckCircle2Icon, InfoIcon } from "lucide-react";

import { QualityScore } from "@/components/charts/quality-score";
import { SavedDiagnostic } from "@/components/saved-diagnostic";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { severityTone } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { FindingStatus, QualityRun } from "@/lib/types";
import type { BadgeTone } from "@/lib/format";

const _OUTCOME_ORDER = [
  {
    key: "passed",
    label: "ui.quality.passed.qualityOverview",
    tone: "teal",
    color: "#2f7a74",
  },
  {
    key: "warning",
    label: "ui.quality.warnings",
    tone: "amber",
    color: "#c99a2e",
  },
  { key: "failed", label: "ui.projects.failed", tone: "red", color: "#c2452e" },
  { key: "error", label: "ui.quality.errors", tone: "red", color: "#8b4a3d" },
  {
    key: "skipped",
    label: "ui.quality.skipped.qualityOverview",
    tone: "slate",
    color: "#7d8aa0",
  },
] as const;

interface QualityOverviewTabProps {
  run: QualityRun;
}

export function QualityOverviewTab({ run }: QualityOverviewTabProps) {
  const { t: arrayT } = useArrayLanguage();
  const OUTCOME_ORDER = _OUTCOME_ORDER.map((item) => ({
    ...item,
    label: arrayT(item.label),
  }));

  const { formatNumber } = useLocaleDisplay();

  const outcomeCounts: Record<FindingStatus, number> = {
    passed: run.passed_count,
    warning: run.warning_count,
    failed: run.failed_count,
    error: run.error_count,
    skipped: run.skipped_count,
  };
  const outcomes = OUTCOME_ORDER.map((o) => ({
    ...o,
    value: outcomeCounts[o.key],
  }));
  const deductions = run.score?.deductions ?? [];

  return (
    <div className="space-y-6">
      {/* Outcome counts */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <UiAttributes messages={{ label: { id: "ui.quality.totalRules" } }}>
          <CountTile
            label={"Total rules"}
            value={formatNumber(run.total_rules)}
          />
        </UiAttributes>
        {outcomes.map((o) => (
          <CountTile
            key={o.key}
            label={o.label}
            value={formatNumber(o.value)}
            tone={o.value > 0 && o.key !== "passed" ? o.tone : undefined}
            highlight={o.key === "passed" && o.value > 0}
          />
        ))}
      </div>

      {!run.score ? (
        <Card>
          <CardContent className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <InfoIcon className="size-4 shrink-0" />{" "}
            <UiMessage id="ui.quality.noScoreWasProducedForThisRun" />{" "}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-5">
          {/* Radial score + dimension bars */}
          <Card className="lg:col-span-3">
            <CardHeader>
              <CardTitle className="text-base">
                <UiMessage id="ui.quality.dataQualityScore" />
              </CardTitle>
            </CardHeader>
            <CardContent>
              <QualityScore
                quality={{
                  total_score: run.score.total_score,
                  dimensions: run.score.dimensions,
                }}
              />
            </CardContent>
          </Card>

          {/* Rule outcomes + deductions */}
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">
                <UiMessage id="ui.quality.ruleOutcomes" />
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              {/* Stacked outcome bar */}
              <div>
                <div className="flex h-4 w-full overflow-hidden rounded-full bg-muted">
                  {outcomes.map((o) =>
                    o.value > 0 ? (
                      <div
                        key={o.key}
                        className="h-full transition-[width] duration-700"
                        style={{
                          width: `${(o.value / Math.max(run.total_rules, 1)) * 100}%`,
                          backgroundColor: o.color,
                        }}
                        title={`${o.label}: ${o.value}`}
                      />
                    ) : null,
                  )}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
                  {outcomes.map((o) => (
                    <div
                      key={o.key}
                      className="flex items-center gap-2 text-sm"
                    >
                      <span
                        className="size-2.5 shrink-0 rounded-sm"
                        style={{ backgroundColor: o.color }}
                      />
                      <span className="text-muted-foreground">{o.label}</span>
                      <span className="ml-auto font-mono text-xs font-semibold">
                        {o.value}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Score deductions */}
              <div className="border-t pt-4">
                <p className="mb-2 text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
                  {" "}
                  <UiMessage id="ui.quality.scoreDeductions" />{" "}
                </p>
                {deductions.length === 0 ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <CheckCircle2Icon className="size-4 text-teal-600" />{" "}
                    <UiMessage id="ui.quality.noDeductionsEveryRuleStayedWithinItsThresholds" />{" "}
                  </div>
                ) : (
                  <ul className="scrollbar-slim max-h-44 space-y-1.5 overflow-y-auto pr-1">
                    {deductions.map((d, i) => (
                      <li
                        key={`${d.rule_id}-${i}`}
                        className="flex items-center gap-2 rounded-md bg-muted/50 px-2 py-1.5"
                      >
                        <StatusBadge
                          label={d.severity}
                          tone={severityTone(d.severity)}
                          dot={false}
                        />
                        <span className="min-w-0 flex-1 truncate font-mono text-[0.7rem]">
                          {d.rule_id}
                        </span>
                        <span className="font-mono text-[0.65rem] text-muted-foreground">
                          ×{d.finding_count}
                        </span>
                        <span className="w-12 text-right font-mono text-xs font-semibold text-destructive">
                          −{d.penalty.toFixed(1)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Ruleset disclaimer */}
      {run.score?.ruleset_note && (
        <div className="flex items-start gap-2 rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <InfoIcon className="mt-0.5 size-3.5 shrink-0" />
          <SavedDiagnostic open>{run.score.ruleset_note}</SavedDiagnostic>
        </div>
      )}
    </div>
  );
}

function CountTile({
  label,
  value,
  tone,
  highlight,
}: {
  label: string;
  value: string;
  tone?: BadgeTone;
  highlight?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-3 shadow-plate">
      <p className="text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 font-heading text-xl font-bold tabular-nums",
          highlight && "text-primary",
          tone === "amber" && "text-amber-600",
          tone === "red" && "text-destructive",
          tone === "slate" && "text-muted-foreground",
          tone === "teal" && "text-primary",
        )}
      >
        {value}
      </p>
    </div>
  );
}
