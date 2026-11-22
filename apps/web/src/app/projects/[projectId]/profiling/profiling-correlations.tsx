"use client";

import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { InfoIcon } from "lucide-react";

import { HeatmapChart } from "@/components/charts/heatmap-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ProfileCorrelations } from "@/lib/types";
import { cn } from "@/lib/utils";

type Method = "pearson" | "spearman";

export function ProfilingCorrelations({
  correlations,
}: {
  correlations: ProfileCorrelations;
}) {
  const [method, setMethod] = useState<Method>("pearson");
  const active = correlations[method];

  return (
    <div className="space-y-4">
      {/* Method toggle */}
      <div className="flex flex-wrap items-center gap-3">
        <UiAttributes
          messages={{ "aria-label": { id: "ui.profiling.correlationMethod" } }}
        >
          <div
            aria-label={"Correlation method"}
            role="group"

            className="inline-flex overflow-hidden rounded-md border"
          >
            {(["pearson", "spearman"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMethod(m)}
                aria-pressed={method === m}
                className={cn(
                  "px-4 py-1.5 text-sm font-medium capitalize transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                  method === m
                    ? "bg-primary text-primary-foreground"
                    : "bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {m}
              </button>
            ))}
          </div>
        </UiAttributes>
        <span className="text-xs text-muted-foreground">
          {method === "pearson" ? (
            <UiMessage id="ui.displayBranch.profiling.linearAssociation" />
          ) : (
            <UiMessage id="ui.displayBranch.profiling.rankAssociation" />
          )}
        </span>
      </div>

      {correlations.note && (
        <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
          <InfoIcon className="size-4 shrink-0" />
          {correlations.note}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base capitalize">
            {method} <UiMessage id="ui.profiling.correlationMatrix" />{" "}
            <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
              {active.columns.length}{" "}
              <UiMessage id="ui.profiling.numericColumns" />{" "}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <HeatmapChart columns={active.columns} matrix={active.matrix} />
          <p className="mt-3 text-xs text-muted-foreground">
            {" "}
            <UiMessage id="ui.profiling.greyCellsIndicatePairsWithInsufficientOverlappingNonNullValuesBlueNegativeCorrelationRedPositiveCorrelation" />{" "}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
