"use client";

import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import {
  qualityRuleName,
  qualityRuleDescription,
} from "@/lib/quality-rule-labels";
import { SavedDiagnostic } from "@/components/saved-diagnostic";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { LightbulbIcon, MessageSquareWarningIcon } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  formatObserved,
  formatThreshold,
  qualityCategoryLabel,
} from "./quality-shared";
import { FINDING_SEVERITY_META, FINDING_STATUS_META } from "@/lib/format";
import type { QualityFinding } from "@/lib/types";

const MAX_COLUMN_CHIPS = 8;
const MAX_EVIDENCE_SAMPLES = 10;

interface QualityFindingDetailProps {
  finding: QualityFinding | null;
  onClose: () => void;
}

/** Full rule-detail dialog opened from a findings row. */
export function QualityFindingDetail({
  finding,
  onClose,
}: QualityFindingDetailProps) {
  const { t: uiLabelT, locale } = useDomainLanguage();

  const { formatNumber, formatPercent, formatDate } = useLocaleDisplay();

  return (
    <Dialog
      open={finding !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="scrollbar-slim max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        {finding && (
          <>
            <DialogHeader>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge
                  label={
                    localizedMetaLabel(
                      FINDING_SEVERITY_META[finding.severity],
                      uiLabelT,
                    ) ?? finding.severity
                  }
                  tone={
                    FINDING_SEVERITY_META[finding.severity]?.tone ?? "slate"
                  }
                  dot={false}
                />
                <StatusBadge
                  label={
                    localizedMetaLabel(
                      FINDING_STATUS_META[finding.status],
                      uiLabelT,
                    ) ?? finding.status
                  }
                  tone={FINDING_STATUS_META[finding.status]?.tone ?? "slate"}
                  dot={false}
                />
                <span className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.62rem] tracking-wide text-muted-foreground uppercase">
                  {qualityCategoryLabel(finding.category, uiLabelT)}
                </span>
              </div>
              <DialogTitle>
                {qualityRuleName(
                  finding.rule_id,
                  finding.rule_version,
                  uiLabelT,
                ) ?? finding.title}
              </DialogTitle>
              <DialogDescription className="font-mono text-xs">
                {finding.rule_name && (
                  <span className="font-sans font-medium text-foreground">
                    {qualityRuleName(
                      finding.rule_id,
                      finding.rule_version,
                      uiLabelT,
                    ) ?? finding.rule_name}{" "}
                    ·{" "}
                  </span>
                )}
                {finding.rule_id}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-5">
              <SavedDiagnostic>{finding.title}</SavedDiagnostic>
              {finding.description && (
                <UiAttributes
                  messages={{ label: { id: "ui.quality.whatThisRuleChecks" } }}
                >
                  <Section label={"What this rule checks"}>
                    <p className="text-sm text-foreground/90">
                      {qualityRuleDescription(
                        finding.rule_id,
                        finding.rule_version,
                        uiLabelT,
                      ) ?? finding.description}
                    </p>
                  </Section>
                </UiAttributes>
              )}

              {finding.summary && (
                <UiAttributes
                  messages={{ label: { id: "ui.quality.whyItTriggered" } }}
                >
                  <Section label={"Why it triggered"}>
                    <p className="rounded-md border-l-2 border-accent bg-accent/10 px-3 py-2 text-sm">
                      <UiMessage id="diagnostic.original" />: {finding.summary}
                    </p>
                  </Section>
                </UiAttributes>
              )}

              {/* Key numbers */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                <UiAttributes
                  messages={{ label: { id: "ui.quality.observed" } }}
                >
                  <Stat
                    label={"Observed"}

                    value={formatObserved(finding.observed_value, locale)}
                    mono
                  />
                </UiAttributes>
                <UiAttributes
                  messages={{ label: { id: "ui.quality.threshold" } }}
                >
                  <Stat
                    label={"Threshold"}

                    value={formatThreshold(finding.threshold)}
                    mono
                  />
                </UiAttributes>
                <UiAttributes
                  messages={{ label: { id: "ui.quality.affectedRows" } }}
                >
                  <Stat
                    label={"Affected rows"}

                    value={formatNumber(finding.affected_count)}
                    mono
                  />
                </UiAttributes>
                <UiAttributes
                  messages={{ label: { id: "ui.quality.affectedRatio" } }}
                >
                  <Stat
                    label={"Affected ratio"}

                    value={formatPercent(finding.affected_ratio)}
                    mono
                  />
                </UiAttributes>
              </div>

              {finding.affected_columns.length > 0 && (
                <UiAttributes
                  messages={{ label: { id: "ui.quality.affectedColumns" } }}
                >
                  <Section label={"Affected columns"}>
                    <div className="flex flex-wrap gap-1.5">
                      {finding.affected_columns
                        .slice(0, MAX_COLUMN_CHIPS)
                        .map((col) => (
                          <code
                            key={col}
                            className="rounded-sm border bg-background px-1.5 py-0.5 font-mono text-[0.7rem]"
                          >
                            {col}
                          </code>
                        ))}
                      {finding.affected_columns.length > MAX_COLUMN_CHIPS && (
                        <span className="self-center text-xs text-muted-foreground">
                          +{finding.affected_columns.length - MAX_COLUMN_CHIPS}{" "}
                          <UiMessage id="ui.quality.more" />{" "}
                        </span>
                      )}
                    </div>
                  </Section>
                </UiAttributes>
              )}

              <EvidenceView evidence={finding.evidence} />

              {finding.remediation && (
                <UiAttributes
                  messages={{ label: { id: "ui.quality.howToFixIt" } }}
                >
                  <Section label={"How to fix it"}>
                    <div className="flex items-start gap-2 rounded-md border-l-2 border-primary bg-primary/5 px-3 py-2">
                      <LightbulbIcon className="mt-0.5 size-4 shrink-0 text-primary" />
                      <p className="text-sm">
                        <UiMessage id="diagnostic.original" />:{" "}
                        {finding.remediation}
                      </p>
                    </div>
                  </Section>
                </UiAttributes>
              )}

              {finding.limitations && (
                <UiAttributes
                  messages={{ label: { id: "ui.quality.limitations" } }}
                >
                  <Section label={"Limitations"}>
                    <div className="flex items-start gap-2 rounded-md border-l-2 border-amber-400 bg-amber-50 px-3 py-2">
                      <MessageSquareWarningIcon className="mt-0.5 size-4 shrink-0 text-amber-600" />
                      <p className="text-sm text-amber-900">
                        <UiMessage id="diagnostic.original" />:{" "}
                        {finding.limitations}
                      </p>
                    </div>
                  </Section>
                </UiAttributes>
              )}

              {/* Footer meta */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 font-mono text-[0.65rem] text-muted-foreground">
                <span>
                  <UiMessage id="ui.quality.ruleV" />
                  {finding.rule_version}
                </span>
                {finding.created_at && (
                  <span>
                    <UiMessage id="ui.quality.checked" />{" "}
                    {formatDate(finding.created_at)}
                  </span>
                )}
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */

function EvidenceView({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence);
  if (entries.length === 0) return null;

  const rawSamples = evidence.samples;
  const samples = Array.isArray(rawSamples)
    ? rawSamples.slice(0, MAX_EVIDENCE_SAMPLES).map((s) => String(s))
    : null;
  const rest = entries.filter(([key]) => key !== "samples");

  return (
    <UiAttributes messages={{ label: { id: "ui.quality.evidence" } }}>
      <Section label={"Evidence"}>
        {samples && samples.length > 0 && (
          <div className="mb-2.5">
            <p className="mb-1.5 text-[0.62rem] tracking-wide text-muted-foreground uppercase">
              {" "}
              <UiMessage id="ui.profiling.sampleValues" />{" "}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {samples.map((sample, i) => (
                <code
                  key={`${sample}-${i}`}
                  className="rounded-sm border bg-background px-1.5 py-0.5 font-mono text-[0.7rem]"
                >
                  {sample}
                </code>
              ))}
            </div>
          </div>
        )}
        {rest.length > 0 && (
          <dl className="space-y-1.5">
            {rest.map(([key, value]) => (
              <div key={key} className="flex gap-3 text-xs">
                <dt className="w-40 shrink-0 font-mono text-muted-foreground">
                  {key}
                </dt>
                <dd className="min-w-0 flex-1 font-mono break-words">
                  {formatEvidenceValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </Section>
    </UiAttributes>
  );
}

function formatEvidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.length === 0
      ? "[]"
      : value.map((item) => formatEvidenceValue(item)).join(", ");
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-1.5 text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      {children}
    </div>
  );
}

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <p className="text-[0.62rem] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      <p
        className={`mt-0.5 text-sm font-medium break-words ${
          mono ? "font-mono text-xs" : ""
        }`}
      >
        {value}
      </p>
    </div>
  );
}
